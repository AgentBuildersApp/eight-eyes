#!/usr/bin/env python3
"""collabctl -- CLI controller for the /collab Claude Code plugin.

Schema v3. 100% stdlib. Cross-platform (Windows / macOS / Linux).

Usage:
    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py <command> [options]

Commands:
    init        Create a new mission
    show        Display active mission state (JSON)
    status      Display active mission progress (text)
    phase       Advance to a phase
    progress    Append a progress message
    close       Close mission as pass or abort
    ledger-trim Trim ledger to N most recent entries
    migrate     Migrate mission state to the latest schema
    verify      Verify plugin installation
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path

# Resolve to hooks/scripts/ for collab_common imports
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "hooks" / "scripts"))

from collab_common import (  # noqa: E402
    CUSTOM_ROLE_SCOPE_TYPES,
    active_pointer_path,
    atomic_write_json,
    is_active_manifest,
    load_active_context,
    load_json,
    load_role_result,
    resolve_git_common_dir,
    resolve_worktree_root,
    spec_hash,
    utc_now,
    validate_custom_role_name,
)

SCHEMA_VERSION = 3

ALL_PHASES = [
    "plan", "implement", "test", "audit", "review",
    "security", "performance", "accessibility",
    "verify", "docs",
]

# Legal phase transitions - from_phase -> set of allowed next phases
LEGAL_TRANSITIONS = {
    "plan": {"implement"},
    "implement": {"test", "review"},  # test is optional, can skip to review
    "test": {"audit", "review"},
    "audit": {"implement", "verify"},
    "review": {"implement", "security", "performance", "accessibility", "verify"},  # loop back or advance
    "security": {"implement", "performance", "accessibility", "verify"},  # loop back or advance
    "performance": {"implement", "accessibility", "verify"},
    "accessibility": {"implement", "verify"},
    "verify": {"implement", "docs"},  # loop back or advance to docs
    "docs": {},  # terminal before close — use collabctl close pass|abort
}

LEGAL_TRANSITIONS_TDD = {
    "plan": {"test"},
    "test": {"implement"},
    "implement": {"audit", "review"},
    "audit": {"test", "verify"},
    "review": {"test", "security", "performance", "accessibility", "verify"},
    "security": {"implement", "performance", "accessibility", "verify"},
    "performance": {"implement", "accessibility", "verify"},
    "accessibility": {"implement", "verify"},
    "verify": {"implement", "docs"},
    "docs": {},
}

DEFAULT_TEST_PATHS = ["tests/", "test/", "__tests__/"]
DEFAULT_DOC_PATHS = ["docs/", "README.md", "CHANGELOG.md"]
DEFAULT_DENIED_PATHS = [".git", ".env", "secrets", "node_modules"]
ROLE_STATUS_ORDER = [
    "implementer",
    "test-writer",
    "skeptic",
    "security",
    "performance",
    "accessibility",
    "verifier",
    "docs",
]
FAILED_ROLE_OUTCOMES = {"abort", "blocked", "fail", "failed", "needs_changes"}


def state_root(cwd: Path) -> Path:
    return resolve_git_common_dir(cwd) / "claude-collab"


def save_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at"] = utc_now()
    atomic_write_json(path, manifest)


def load_active(cwd: Path):
    sroot = state_root(cwd)
    active = load_json(active_pointer_path(sroot), default=None)
    if not active:
        return None
    mid = active["mission_id"]
    mdir = sroot / "missions" / mid
    mpath = mdir / "manifest.json"
    manifest = load_json(mpath, default=None)
    if not manifest:
        return None
    return sroot, mid, mdir, mpath, manifest


def bool_arg(value: str) -> bool:
    v = str(value).strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def phase_transitions_for(manifest: dict) -> dict[str, set[str]]:
    """Return the active phase-transition table for the mission manifest."""
    return LEGAL_TRANSITIONS_TDD if manifest.get("tdd_mode") else LEGAL_TRANSITIONS


def _cmd_list(raw: list[str] | None) -> list[dict[str, str]]:
    """Normalize repeated CLI command arguments into manifest command objects."""
    return [{"name": cmd, "command": cmd} for cmd in (raw or [])]


def _parse_custom_role_commands(value: str) -> list[str]:
    """Parse a custom-role commands value into a flat command list."""
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid custom role commands JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise SystemExit("custom role commands JSON must be a list")
        commands: list[str] = []
        for item in parsed:
            if isinstance(item, str):
                commands.append(item.strip())
            elif isinstance(item, dict) and isinstance(item.get("command"), str):
                commands.append(item["command"].strip())
            else:
                raise SystemExit(
                    "custom role commands list entries must be strings or {command: ...} objects"
                )
        return [command for command in commands if command]
    return [command.strip() for command in raw.split(";") if command.strip()]


def parse_custom_role(raw: str) -> dict[str, object]:
    """Parse a --custom-role argument into the manifest custom_roles schema."""
    text = str(raw or "").strip()
    if not text:
        raise SystemExit("custom role cannot be empty")

    fields: dict[str, str] = {}
    for chunk in [part.strip() for part in text.split(",") if part.strip()]:
        if "=" not in chunk:
            raise SystemExit(
                f"invalid custom role '{text}': expected comma-separated key=value pairs"
            )
        key, value = chunk.split("=", 1)
        fields[key.strip().lower()] = value.strip()

    name = fields.get("name", "")
    ok, reason = validate_custom_role_name(name)
    if not ok:
        raise SystemExit(f"invalid custom role name: {reason}")

    scope_type = fields.get("scope_type", fields.get("scope", "")).strip().lower().replace("-", "_")
    if scope_type not in CUSTOM_ROLE_SCOPE_TYPES:
        allowed = ", ".join(sorted(CUSTOM_ROLE_SCOPE_TYPES))
        raise SystemExit(
            f"invalid custom role scope '{scope_type or '(missing)'}'; expected one of: {allowed}"
        )

    role_config: dict[str, object] = {
        "name": name.strip(),
        "scope_type": scope_type,
        "approved_commands": _parse_custom_role_commands(
            fields.get("approved_commands", fields.get("commands", ""))
        ),
        "isolation": fields.get("isolation", "none").strip() or "none",
    }
    instructions = fields.get("instructions", "").strip()
    if instructions:
        role_config["instructions"] = instructions
    return role_config


def _parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None on failure."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_elapsed(created_at: object) -> str:
    """Format mission elapsed time as a compact human-readable duration."""
    created = _parse_timestamp(created_at)
    if created is None:
        return "unknown"

    total_seconds = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _result_outcome(result: dict[str, object] | None) -> str:
    """Return the canonical status or recommendation string for a role result."""
    if not isinstance(result, dict):
        return ""
    outcome = result.get("recommendation") or result.get("status") or ""
    return str(outcome).strip()


def _result_status(result: dict[str, object] | None) -> str:
    """Map a result object to the requested pending/complete/failed state."""
    if not isinstance(result, dict):
        return "pending"
    if _result_outcome(result).lower() in FAILED_ROLE_OUTCOMES:
        return "failed"
    return "complete"


def _stringify_detail(item: object) -> str:
    """Format a compact detail line for status output."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        criterion = str(item.get("criterion") or "").strip()
        status = str(item.get("status") or "").strip()
        category = str(item.get("category") or "").strip()
        summary = str(
            item.get("summary")
            or item.get("title")
            or item.get("finding")
            or item.get("detail")
            or item.get("message")
            or item.get("issue")
            or ""
        ).strip()
        if criterion:
            return f"{criterion}: {status or 'unknown'}"
        if category and summary:
            return f"{category}: {summary}"
        if summary:
            return summary
        if category:
            return category
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return str(item).strip()


def _role_summary_lines(result: dict[str, object] | None) -> list[str]:
    """Return at most two compact summary lines for a role result."""
    if not isinstance(result, dict):
        return []

    candidates: list[str] = []

    summary = result.get("summary")
    if isinstance(summary, str):
        candidates.extend(line.strip() for line in summary.splitlines() if line.strip())

    findings = result.get("findings")
    if isinstance(findings, list):
        candidates.extend(_stringify_detail(item) for item in findings)

    criteria_results = result.get("criteria_results")
    if isinstance(criteria_results, list):
        candidates.extend(_stringify_detail(item) for item in criteria_results)

    list_fields = [
        ("changed_paths", "Changed paths"),
        ("test_files_created", "Test files"),
        ("coverage_targets", "Coverage targets"),
        ("tests_run", "Tests"),
        ("scan_commands_run", "Scans"),
        ("benchmarks_run", "Benchmarks"),
        ("a11y_commands_run", "A11y"),
        ("docs_updated", "Docs updated"),
        ("docs_created", "Docs created"),
    ]
    for key, label in list_fields:
        value = result.get(key)
        if isinstance(value, list) and value:
            preview = ", ".join(str(item) for item in value[:3])
            candidates.append(f"{label}: {preview}")

    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
        if len(unique) == 2:
            break
    return unique


def _role_names_for_status(manifest: dict) -> list[str]:
    """Return built-in roles followed by any manifest-defined custom roles."""
    roles = list(ROLE_STATUS_ORDER)
    seen = set(roles)
    for item in manifest.get("custom_roles", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in seen:
            roles.append(name)
            seen.add(name)
    return roles


# ── Commands ──


def cmd_init(args):
    """Create a new /collab mission with the given objective and scope."""
    cwd = Path(args.cwd or ".").resolve()
    project_root = resolve_worktree_root(cwd)
    sroot = state_root(cwd)
    ap = active_pointer_path(sroot)
    active = load_json(ap, default=None)
    if active and not args.force:
        raise SystemExit("An active /collab mission already exists. Close it first or use --force.")

    objective = args.objective or ""
    if args.objective_file:
        objective = Path(args.objective_file).read_text(encoding="utf-8").strip()
    if not objective:
        raise SystemExit("objective is required (--objective or --objective-file)")

    criteria = list(args.criterion or [])
    if args.criteria_file:
        criteria.extend([ln.strip() for ln in Path(args.criteria_file).read_text(encoding="utf-8").splitlines() if ln.strip()])

    mid = f"collab-{utc_now().replace(':', '').replace('-', '').replace('T', 't').replace('Z', 'z')}"
    mdir = sroot / "missions" / mid

    spec = None
    if args.spec_path:
        spec = {"path": args.spec_path, "sha256": spec_hash(project_root, args.spec_path)}

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "mission_id": mid,
        "status": "active",
        "phase": "plan",
        "awaiting_user": True if args.awaiting_user is None else args.awaiting_user,
        "objective": objective,
        "project_root": str(project_root),
        "spec": spec,
        "allowed_paths": list(args.allowed_path or []),
        "denied_paths": list(args.denied_path) if args.denied_path else list(DEFAULT_DENIED_PATHS),
        "test_paths": list(args.test_path) if args.test_path else list(DEFAULT_TEST_PATHS),
        "doc_paths": list(args.doc_path) if args.doc_path else list(DEFAULT_DOC_PATHS),
        "acceptance_criteria": criteria,
        "verification_commands": _cmd_list(args.verify_command),
        "security_scan_commands": _cmd_list(args.security_command),
        "benchmark_commands": _cmd_list(args.benchmark_command),
        "a11y_commands": _cmd_list(args.a11y_command),
        "tdd_mode": bool(args.tdd),
        "custom_roles": [parse_custom_role(item) for item in (args.custom_role or [])],
        "timeout_hours": args.timeout_hours,
        "role_failure_counts": {},
        "loop_count": 0,
        "loop_epoch": 0,
        "max_loops": args.max_loops,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "mission_id": mid,
            "state_root": str(sroot),
            "paths": {
                "manifest": str(mdir / "manifest.json"),
                "progress": str(mdir / "progress.md"),
                "ledger": str(mdir / "ledger.ndjson"),
                "results_dir": str(mdir / "results"),
            },
            "manifest": manifest,
        }, indent=2, ensure_ascii=False))
        return 0

    mdir.mkdir(parents=True, exist_ok=True)
    save_manifest(mdir / "manifest.json", manifest)
    atomic_write_json(ap, {"mission_id": mid})
    (mdir / "ledger.ndjson").write_text("", encoding="utf-8")
    (mdir / "results").mkdir(exist_ok=True)
    (mdir / "snapshots").mkdir(exist_ok=True)
    (mdir / "progress.md").write_text(
        f"# /collab progress for {mid}\n\n"
        f"- {utc_now()} Mission initialized in plan phase, awaiting_user={manifest['awaiting_user']}\n",
        encoding="utf-8",
    )
    print(json.dumps({"mission_id": mid, "manifest": str(mdir / "manifest.json")}, indent=2))
    return 0


def cmd_show(args):
    """Display the active mission manifest and key file locations."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        print("No active /collab mission.")
        return 0
    sroot, mid, mdir, mpath, manifest = loaded
    print(json.dumps({
        "mission_id": mid,
        "state_root": str(sroot),
        "manifest": manifest,
        "paths": {
            "manifest": str(mpath),
            "progress": str(mdir / "progress.md"),
            "ledger": str(mdir / "ledger.ndjson"),
            "results_dir": str(mdir / "results"),
        },
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_status(args):
    """Display a compact text summary of the active mission and role results."""
    cwd = Path(args.cwd or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx or not is_active_manifest(ctx.manifest):
        print("No active /collab mission.")
        return 0

    manifest = ctx.manifest
    lines = [
        f"Mission ID: {ctx.mission_id}",
        f"Objective: {manifest.get('objective', '')}",
        f"Current phase: {manifest.get('phase', 'unknown')}",
        f"Elapsed: {_format_elapsed(manifest.get('created_at'))}",
        "Roles:",
    ]

    for role in _role_names_for_status(manifest):
        result = load_role_result(ctx, role)
        lines.append(f"- {role}: {_result_status(result)}")
        outcome = _result_outcome(result)
        if outcome:
            lines.append(f"  recommendation: {outcome}")
        for detail in _role_summary_lines(result):
            lines.append(f"  {detail}")

    print("\n".join(lines))
    return 0


def cmd_phase(args):
    """Advance the active mission to a new phase with transition validation."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission.")
    _, mid, mdir, mpath, manifest = loaded

    current = manifest.get("phase", "plan")

    # Enforce legal phase transitions unless --force
    if not args.force:
        allowed = phase_transitions_for(manifest).get(current, set())
        if args.phase not in allowed:
            raise SystemExit(
                f"Cannot transition from '{current}' to '{args.phase}'. "
                f"Allowed: {', '.join(sorted(allowed)) or '(none -- use close pass|abort)'}"
            )

    # Track loops: transitioning back to implement from a review/check phase
    loop_back_phases = {"audit", "review", "security", "performance", "accessibility", "verify"}
    if args.phase in {"implement", "test"} and current in loop_back_phases:
        manifest["loop_count"] = manifest.get("loop_count", 0) + 1
        manifest["loop_epoch"] = manifest.get("loop_epoch", 0) + 1
        max_loops = manifest.get("max_loops", 3)
        if manifest["loop_count"] > max_loops:
            raise SystemExit(
                f"Loop limit reached ({manifest['loop_count']}/{max_loops}). "
                f"Use 'collabctl close abort' or increase max_loops in manifest."
            )

    manifest["phase"] = args.phase
    if args.awaiting_user is not None:
        manifest["awaiting_user"] = args.awaiting_user
    save_manifest(mpath, manifest)
    with (mdir / "progress.md").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {utc_now()} Phase set to {args.phase}, awaiting_user={manifest.get('awaiting_user')}\n")
    print(f"{mid}: phase={args.phase} awaiting_user={manifest.get('awaiting_user')}")
    return 0


def cmd_progress(args):
    """Append a progress entry to the active mission log."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission.")
    _, mid, mdir, mpath, manifest = loaded
    message = args.message or ""
    if args.message_file:
        message = Path(args.message_file).read_text(encoding="utf-8").strip()
    if not message:
        raise SystemExit("message is required")
    with (mdir / "progress.md").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {utc_now()} {message}\n")
    save_manifest(mpath, manifest)
    print(f"{mid}: progress updated")
    return 0


def cmd_close(args):
    """Close the active mission with a terminal pass or abort outcome."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission.")
    sroot, mid, mdir, mpath, manifest = loaded
    manifest["status"] = args.outcome
    manifest["phase"] = args.outcome
    manifest["awaiting_user"] = False
    if args.reason:
        manifest["close_reason"] = args.reason
    manifest["closed_at"] = utc_now()
    save_manifest(mpath, manifest)
    with (mdir / "progress.md").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {utc_now()} Mission closed with outcome={args.outcome}. {args.reason or ''}\n")
    try:
        active_pointer_path(sroot).unlink()
    except FileNotFoundError:
        pass
    print(f"{mid}: closed as {args.outcome}")
    return 0


def cmd_ledger_trim(args):
    """Trim the active mission ledger while archiving removed entries."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission.")
    _, mid, mdir, _, _ = loaded
    ledger = mdir / "ledger.ndjson"
    if not ledger.exists():
        print(f"{mid}: no ledger to trim")
        return 0
    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    original = len(lines)
    if original <= args.keep:
        print(f"{mid}: ledger has {original} entries, under limit of {args.keep}")
        return 0
    archive = mdir / "snapshots" / f"ledger-trimmed-{utc_now().replace(':', '').replace('-', '')}.ndjson"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("\n".join(lines[:original - args.keep]) + "\n", encoding="utf-8")
    ledger.write_text("\n".join(lines[-args.keep:]) + "\n", encoding="utf-8")
    trimmed = original - args.keep
    print(f"{mid}: trimmed {trimmed} entries (archived to {archive.name}), kept {args.keep}")
    return 0


def cmd_verify(args):
    """Verify plugin installation is correct."""
    root = _PLUGIN_ROOT
    errors = []

    checks = [
        (root / ".claude-plugin" / "plugin.json", "Plugin manifest"),
        (root / "hooks" / "hooks.json", "Hook wiring"),
        (root / "skills" / "collab" / "SKILL.md", "Coordinator skill"),
        (root / "scripts" / "collabctl.py", "CLI tool"),
        (root / "commands" / "8eyes.md", "Claude /8eyes command"),
        (root / "adapters" / "copilot_cli" / "plugin.json", "Copilot adapter manifest"),
        (root / "adapters" / "copilot_cli" / "hooks.json", "Copilot adapter hooks"),
        (root / "adapters" / "codex_cli" / "hooks.json", "Codex adapter hooks"),
        (root / "adapters" / "codex_cli" / "AGENTS.md", "Codex adapter instructions"),
        (root / "install.py", "Cross-platform installer"),
    ]
    for path, label in checks:
        rel_path = path.relative_to(root).as_posix()
        if path.exists():
            print(f"  [OK] {label}: {rel_path}")
        else:
            errors.append(f"Missing {label}: {rel_path}")

    # Check hook scripts
    expected_hooks = [
        "collab_common.py", "collab_pre_tool.py", "collab_post_tool.py",
        "collab_session_start.py", "collab_subagent_start.py", "collab_subagent_stop.py",
        "collab_stop.py", "collab_pre_compact.py",
    ]
    for hook in expected_hooks:
        path = root / "hooks" / "scripts" / hook
        if path.exists():
            print(f"  [OK] Hook: {hook}")
        else:
            errors.append(f"Missing hook: hooks/scripts/{hook}")

    # Check agents
    expected_agents = [
        "collab-implementer.md", "collab-test-writer.md", "collab-skeptic.md",
        "collab-security.md", "collab-performance.md", "collab-accessibility.md",
        "collab-docs.md", "collab-verifier.md",
    ]
    for agent in expected_agents:
        path = root / "agents" / agent
        if path.exists():
            print(f"  [OK] Agent: {agent}")
        else:
            errors.append(f"Missing agent: agents/{agent}")

    # Check Python version
    if sys.version_info < (3, 10):
        errors.append(f"Python {sys.version_info.major}.{sys.version_info.minor} found, need 3.10+")

    # Check git
    cwd = Path(args.cwd or ".").resolve()
    try:
        resolve_worktree_root(cwd)
        print("  [OK] Git repository detected")
    except Exception:
        errors.append("Not in a git repository (required for mission state)")

    if errors:
        print(f"\nVERIFY FAILED: {len(errors)} error(s)")
        for e in errors:
            print(f"  [FAIL] {e}")
        return 1
    print("\n[OK] Plugin installation verified. All files present.")
    return 0


def cmd_migrate(args):
    """Migrate a manifest forward to the current schema version."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission.")
    sroot, mid, mdir, mpath, manifest = loaded

    current_version = manifest.get("schema_version", 1)
    if current_version >= SCHEMA_VERSION:
        print(f"{mid}: already at schema v{current_version}")
        return 0

    start_version = current_version

    if current_version == 1:
        manifest.setdefault("test_paths", ["tests/", "test/", "__tests__/"])
        manifest.setdefault("doc_paths", ["docs/", "README.md", "CHANGELOG.md"])
        manifest.setdefault("security_scan_commands", [])
        manifest.setdefault("benchmark_commands", [])
        manifest.setdefault("a11y_commands", [])
        manifest.setdefault("loop_count", 0)
        manifest.setdefault("max_loops", 3)
        manifest["schema_version"] = 2
        current_version = 2

    if current_version == 2:
        manifest.setdefault("tdd_mode", False)
        manifest.setdefault("custom_roles", [])
        manifest.setdefault("timeout_hours", 24)
        manifest.setdefault("role_failure_counts", {})
        manifest.setdefault("loop_epoch", 0)
        manifest["schema_version"] = 3

    save_manifest(mpath, manifest)
    print(f"{mid}: migrated from schema v{start_version} to v{manifest['schema_version']}")
    return 0


# ── Argument Parser ──


def build_parser():
    p = argparse.ArgumentParser(prog="collabctl", description="Manage /collab mission state.")
    p.add_argument("--cwd")
    sub = p.add_subparsers(dest="command", required=True)

    # init
    init = sub.add_parser("init", help="Create a new mission")
    init.add_argument("--objective")
    init.add_argument("--objective-file")
    init.add_argument("--spec-path")
    init.add_argument("--allowed-path", action="append", default=[])
    init.add_argument("--denied-path", action="append", default=None)
    init.add_argument("--test-path", action="append", default=None)
    init.add_argument("--doc-path", action="append", default=None)
    init.add_argument("--criterion", action="append", default=[])
    init.add_argument("--criteria-file")
    init.add_argument("--verify-command", action="append", default=[])
    init.add_argument("--security-command", action="append", default=[])
    init.add_argument("--benchmark-command", action="append", default=[])
    init.add_argument("--a11y-command", action="append", default=[])
    init.add_argument(
        "--custom-role",
        action="append",
        default=[],
        help="Custom role spec: name=<role>,scope=<read_only|write_allowed|write_test|write_doc>,commands=<cmd>",
    )
    init.add_argument("--tdd", action="store_true", help="Require tests before implementation")
    init.add_argument("--timeout-hours", type=int, default=24, help="Mission timeout in hours (default: 24)")
    init.add_argument("--dry-run", action="store_true", help="Print the mission plan without creating files")
    init.add_argument("--awaiting-user", type=bool_arg, nargs="?", const=True, default=None)
    init.add_argument("--max-loops", type=int, default=3, help="Maximum implement loops (default: 3)")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    # show
    show = sub.add_parser("show", help="Display active mission state")
    show.set_defaults(func=cmd_show)

    # status
    status = sub.add_parser("status", help="Display active mission progress")
    status.set_defaults(func=cmd_status)

    # phase
    phase = sub.add_parser("phase", help="Advance to a phase")
    phase.add_argument("phase", choices=ALL_PHASES)
    phase.add_argument("--awaiting-user", type=bool_arg, nargs="?", const=True, default=None)
    phase.add_argument("--force", action="store_true", help="Bypass phase transition validation")
    phase.set_defaults(func=cmd_phase)

    # progress
    progress = sub.add_parser("progress", help="Append a progress message")
    progress.add_argument("--message")
    progress.add_argument("--message-file")
    progress.set_defaults(func=cmd_progress)

    # close
    close = sub.add_parser("close", help="Close mission as pass or abort")
    close.add_argument("outcome", choices=["pass", "abort"])
    close.add_argument("--reason")
    close.set_defaults(func=cmd_close)

    # ledger-trim
    trim = sub.add_parser("ledger-trim", help="Trim ledger to most recent N entries")
    trim.add_argument("--keep", type=int, default=200)
    trim.set_defaults(func=cmd_ledger_trim)

    # migrate
    migrate = sub.add_parser("migrate", help="Migrate mission state to the latest schema")
    migrate.set_defaults(func=cmd_migrate)

    # verify
    verify = sub.add_parser("verify", help="Verify plugin installation")
    verify.set_defaults(func=cmd_verify)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
