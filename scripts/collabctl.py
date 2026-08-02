#!/usr/bin/env python3
"""collabctl -- CLI controller for the /collab Claude Code plugin.

Schema v4. 100% stdlib. Cross-platform (Windows / macOS / Linux).

Usage:
    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py <command> [options]

Commands:
    init        Create a new mission
    show        Display active mission state (JSON)
    status      Display active mission progress (text)
    phase       Advance to a phase
    run         Live mission controller
    progress    Append a progress message
    close       Close mission as pass or abort
    ledger-trim Trim ledger to N most recent entries
    timeline    Display role dispatch/completion timeline
    report      Produce consolidated mission report
    migrate     Migrate mission state to the latest schema
    verify      Verify plugin installation
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# Resolve to hooks/scripts/ for collab_common imports
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "hooks" / "scripts"))

from collab_common import (  # noqa: E402
    CUSTOM_ROLE_SCOPE_TYPES,
    active_pointer_path,
    build_research_gate,
    append_ledger,
    atomic_write_json,
    get_research_gate,
    is_active_manifest,
    latest_buyoff,
    load_active_context,
    load_json,
    load_role_result,
    plan_buyoff_is_satisfied,
    plan_buyoff_exists,
    research_is_required,
    research_is_satisfied,
    RESEARCH_FACTOR_KEYS,
    RESEARCH_PENALTY_WEIGHTS,
    resolve_project_root,
    resolve_git_common_dir,
    resolve_worktree_root,
    save_role_result,
    spec_hash,
    state_root_for,
    utc_now,
    validate_custom_role_name,
)
from core.roles import validate_role_result  # noqa: E402

SCHEMA_VERSION = 4

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
    "review": {"implement", "security", "performance", "accessibility"},  # must pass through audit roles before verify
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
    "review": {"test", "security", "performance", "accessibility"},  # must pass through audit roles before verify
    "security": {"implement", "performance", "accessibility", "verify"},
    "performance": {"implement", "accessibility", "verify"},
    "accessibility": {"implement", "verify"},
    "verify": {"implement", "docs"},
    "docs": {},
}

DEFAULT_TEST_PATHS = ["tests/", "test/", "__tests__/"]
DEFAULT_DOC_PATHS = ["docs/", "README.md", "CONTRIBUTING.md"]
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
SCOPE_EXCLUDED_PREFIXES = (
    ".claude/",
    ".codex/collab-state/",
    ".git/",
    ".github/",
    ".internal/",
    "node_modules/",
)
NON_GIT_BASELINE_MAX_FILES = 20_000
PHASE_ROLE_REQUIREMENTS = {
    "implement": ("implementer",),
    "test": ("test-writer",),
    "audit": ("skeptic", "security", "performance", "accessibility"),
    "review": ("implementer", "skeptic"),
    "security": ("security",),
    "performance": ("performance",),
    "accessibility": ("accessibility",),
    "verify": ("verifier",),
    "docs": ("docs",),
}


def state_root(cwd: Path) -> Path:
    return state_root_for(cwd)


def _scope_roots(manifest: dict) -> set[str]:
    """Return normalized allowed/test/doc roots for close-time scope checks."""
    allowed: set[str] = set()
    for key in ("allowed_paths", "test_paths", "doc_paths"):
        for path in manifest.get(key, []):
            item = str(path).strip().rstrip("/")
            if item:
                allowed.add(item)
    return allowed


def _scope_excluded(rel_path: str) -> bool:
    """Return True for paths that should never count as scope drift."""
    return any(rel_path.startswith(prefix) for prefix in SCOPE_EXCLUDED_PREFIXES)


def _scope_allowed(rel_path: str, allowed: set[str]) -> bool:
    """Return True when rel_path is covered by allowed roots."""
    return not allowed or any(rel_path == root or rel_path.startswith(f"{root}/") for root in allowed)


def _capture_non_git_baseline(project_root: Path, max_files: int = NON_GIT_BASELINE_MAX_FILES) -> dict:
    """Capture a bounded non-git file baseline for close-time scope checks."""
    files: dict[str, dict[str, int]] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(project_root):
            current_dir = Path(dirpath)
            rel_dir = current_dir.relative_to(project_root).as_posix()
            if rel_dir == ".":
                rel_dir = ""
            dirnames[:] = [
                dirname for dirname in dirnames
                if not _scope_excluded(f"{rel_dir}/{dirname}/".lstrip("/"))
            ]
            for filename in filenames:
                path = current_dir / filename
                rel_path = path.relative_to(project_root).as_posix()
                if _scope_excluded(rel_path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if not path.is_file():
                    continue
                files[rel_path] = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
                if len(files) > max_files:
                    return {
                        "type": "non_git",
                        "status": "unavailable",
                        "reason": f"file count exceeded {max_files}",
                        "file_count": len(files),
                        "files": {},
                    }
    except OSError as exc:
        return {
            "type": "non_git",
            "status": "unavailable",
            "reason": str(exc),
            "file_count": len(files),
            "files": {},
        }
    return {
        "type": "non_git",
        "status": "captured",
        "file_count": len(files),
        "files": files,
    }


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


def _parse_model_map(args) -> dict[str, str]:
    """Combine --model-map JSON with --default-model into a role->model mapping."""
    model_map: dict[str, str] = {}
    raw = getattr(args, "model_map", None)
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid --model-map JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SystemExit("--model-map must be a JSON object")
        for key, value in parsed.items():
            model_map[str(key).strip()] = str(value).strip()
    default_model = getattr(args, "default_model", None) or "claude"
    model_map.setdefault("default", default_model)
    return model_map


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


def _result_status(
    result: dict[str, object] | None,
    role: str = "",
    manifest: dict | None = None,
) -> str:
    """Map a result object to the requested pending/complete/failed/running state."""
    if isinstance(result, dict):
        if _result_outcome(result).lower() in FAILED_ROLE_OUTCOMES:
            return "failed"
        return "complete"
    # No result — check if dispatched but not yet complete
    if manifest:
        assignments = manifest.get("role_assignments", {})
        role_info = assignments.get(role, {})
        if isinstance(role_info, dict) and role_info.get("started_at") and not role_info.get("completed_at"):
            return "running"
    return "pending"


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


def _required_roles_for_phase(manifest: dict, phase: str | None = None) -> list[str]:
    """Return the roles required to complete a given phase."""
    selected_phase = phase or str(manifest.get("phase") or "")
    roles = list(PHASE_ROLE_REQUIREMENTS.get(selected_phase, ()))
    if selected_phase == "audit":
        skipped = set(manifest.get("skipped_roles", []))
        roles = [role for role in roles if role not in skipped]
    return roles


def _agent_name_for_role(role: str) -> str:
    """Return the named agent handle expected by /8eyes orchestration."""
    return f"collab-{role}"


def _controller_transition(cwd: Path, phase: str) -> None:
    """Advance the active mission phase using normal transition validation."""
    sink = io.StringIO()
    with redirect_stdout(sink):
        cmd_phase(SimpleNamespace(
            cwd=str(cwd),
            phase=phase,
            awaiting_user=False,
            skip_role=[],
            force=False,
        ))


def _controller_close(cwd: Path, outcome: str, reason: str) -> None:
    """Close the active mission using the standard close path."""
    sink = io.StringIO()
    with redirect_stdout(sink):
        cmd_close(SimpleNamespace(
            cwd=str(cwd),
            outcome=outcome,
            reason=reason,
            force_close=None,
        ))


def _controller_next_success_phase(manifest: dict, phase: str) -> str | None:
    """Return the next phase after a successful role completion."""
    if phase == "implement":
        return "test"
    if phase == "test":
        return "audit"
    if phase == "audit":
        return "verify"
    if phase == "review":
        return "security"
    if phase == "security":
        return "performance"
    if phase == "performance":
        return "accessibility"
    if phase == "accessibility":
        return "verify"
    if phase == "verify":
        planned = manifest.get("planned_roles", [])
        return "docs" if "docs" in planned else None
    return None


def _controller_waiting_payload(
    ctx,
    phase: str,
    pending_roles: list[str],
    history: list[dict[str, object]],
    reason: str | None = None,
) -> dict[str, object]:
    """Build a machine-readable waiting payload for the live controller."""
    return {
        "state": "waiting",
        "mission_id": ctx.mission_id,
        "phase": phase,
        "pending_roles": pending_roles,
        "dispatch_roles": [
            {"role": role, "agent_type": _agent_name_for_role(role)}
            for role in pending_roles
        ],
        "history": history,
        "reason": reason or "Awaiting validated role results for the current phase.",
    }


def _controller_step(cwd: Path, history: list[dict[str, object]]) -> dict[str, object]:
    """Drive the active mission forward until it needs more role results."""
    ctx = load_active_context(cwd)
    if not ctx or not is_active_manifest(ctx.manifest):
        return {
            "state": "closed",
            "mission_id": None,
            "phase": None,
            "history": history,
            "reason": "No active /collab mission.",
        }

    manifest = ctx.manifest
    phase = str(manifest.get("phase") or "")
    required_roles = _required_roles_for_phase(manifest, phase)

    if phase == "plan":
        return {
            "state": "waiting_for_user",
            "mission_id": ctx.mission_id,
            "phase": phase,
            "pending_roles": [],
            "dispatch_roles": [],
            "history": history,
            "reason": "Plan phase still requires explicit user approval before implementation begins.",
        }

    if phase in {"pass", "abort"}:
        return {
            "state": "closed",
            "mission_id": ctx.mission_id,
            "phase": phase,
            "history": history,
            "reason": f"Mission already closed as {phase}.",
        }

    pending_roles: list[str] = []
    failed_roles: list[dict[str, str]] = []
    for role in required_roles:
        result = load_role_result(ctx, role)
        if not result:
            pending_roles.append(role)
            continue
        outcome = _result_outcome(result).lower()
        if outcome in FAILED_ROLE_OUTCOMES:
            failed_roles.append({"role": role, "outcome": outcome or "failed"})

    if pending_roles:
        return _controller_waiting_payload(ctx, phase, pending_roles, history)

    if failed_roles:
        if phase in {"audit", "review", "security", "performance", "accessibility", "verify"}:
            _controller_transition(cwd, "implement")
            history.append({
                "action": "loopback",
                "from_phase": phase,
                "to_phase": "implement",
                "failed_roles": failed_roles,
            })
            refreshed = load_active_context(cwd)
            return _controller_waiting_payload(
                refreshed,
                "implement",
                _required_roles_for_phase(refreshed.manifest, "implement"),
                history,
                reason="Looped back to implement because a review-phase role requested changes.",
            )
        return {
            "state": "blocked",
            "mission_id": ctx.mission_id,
            "phase": phase,
            "failed_roles": failed_roles,
            "history": history,
            "reason": "Current phase has a blocking role result and cannot auto-advance.",
        }

    if phase == "docs":
        docs_result = load_role_result(ctx, "docs") or {}
        if str(docs_result.get("status") or "").lower() == "blocked":
            return {
                "state": "blocked",
                "mission_id": ctx.mission_id,
                "phase": phase,
                "history": history,
                "reason": "Docs role reported blocked and requires manual intervention.",
            }
        _controller_close(cwd, "pass", "Live controller closed mission after docs completed.")
        history.append({"action": "close", "outcome": "pass", "from_phase": phase})
        return {
            "state": "closed",
            "mission_id": ctx.mission_id,
            "phase": "pass",
            "history": history,
            "reason": "Mission closed as pass after docs completed.",
        }

    next_phase = _controller_next_success_phase(manifest, phase)
    if next_phase is None:
        _controller_close(cwd, "pass", "Live controller closed mission after all configured phases completed.")
        history.append({"action": "close", "outcome": "pass", "from_phase": phase})
        return {
            "state": "closed",
            "mission_id": ctx.mission_id,
            "phase": "pass",
            "history": history,
            "reason": "Mission closed as pass after all configured phases completed.",
        }

    _controller_transition(cwd, next_phase)
    history.append({
        "action": "advance",
        "from_phase": phase,
        "to_phase": next_phase,
    })
    refreshed = load_active_context(cwd)
    return _controller_waiting_payload(
        refreshed,
        next_phase,
        _required_roles_for_phase(refreshed.manifest, next_phase),
        history,
        reason=f"Advanced to {next_phase}; dispatch the listed role(s) to continue.",
    )


def _source_record_from_cli(raw: str) -> dict[str, str]:
    """Normalize a simple source string into a structured source record."""
    text = str(raw or "").strip()
    return {
        "title": text,
        "kind": "reviewed_source",
        "location": "",
        "notes": "",
        "added_at": utc_now(),
    }


def _artifact_record_from_cli(raw: str) -> dict[str, str]:
    """Normalize a simple artifact path into a structured artifact record."""
    text = str(raw or "").strip()
    return {
        "path": text,
        "kind": "artifact",
        "notes": "",
        "added_at": utc_now(),
    }


def _stage0_inputs_present(args) -> bool:
    """Return True when any Stage 0 research-gate inputs were supplied."""
    keys = (
        "domain",
        "action_type",
        "risk",
        "root_cause_clarity",
        "fix_path_clarity",
        "verification_clarity",
        "prior_pattern_match",
        "environmental_stability",
        "research_rationale",
    )
    if any(getattr(args, key, None) not in (None, "") for key in keys):
        return True
    list_keys = ("source_reviewed", "research_artifact")
    if any(getattr(args, key, None) for key in list_keys):
        return True
    penalty_keys = (
        "penalty_architecture",
        "penalty_security_data",
        "penalty_cross_module",
        "penalty_ecosystem_churn",
        "penalty_weak_observability",
    )
    return any(bool(getattr(args, key, False)) for key in penalty_keys)


def _penalty_breakdown(args) -> dict[str, int]:
    """Return manifest penalty values using deterministic negative integers."""
    return {
        "architecture_irreversible": RESEARCH_PENALTY_WEIGHTS["architecture_irreversible"] if getattr(args, "penalty_architecture", False) else 0,
        "security_auth_billing_data": RESEARCH_PENALTY_WEIGHTS["security_auth_billing_data"] if getattr(args, "penalty_security_data", False) else 0,
        "cross_module_integration": RESEARCH_PENALTY_WEIGHTS["cross_module_integration"] if getattr(args, "penalty_cross_module", False) else 0,
        "ecosystem_churn": RESEARCH_PENALTY_WEIGHTS["ecosystem_churn"] if getattr(args, "penalty_ecosystem_churn", False) else 0,
        "weak_observability": RESEARCH_PENALTY_WEIGHTS["weak_observability"] if getattr(args, "penalty_weak_observability", False) else 0,
    }


def build_research_gate_from_args(args, previous_failure: bool = False) -> dict:
    """Build the init-time research gate from CLI args."""
    if not _stage0_inputs_present(args):
        return build_research_gate(created_at=utc_now())

    required = ("domain", "action_type", "risk")
    missing = [key for key in required if not getattr(args, key, None)]
    factor_values = {key: getattr(args, key, None) for key in RESEARCH_FACTOR_KEYS}
    missing_factors = [key for key, value in factor_values.items() if value is None]
    if missing or missing_factors:
        missing_names = ", ".join(missing + missing_factors)
        raise SystemExit(f"incomplete Stage 0 classification: missing {missing_names}")

    gate = build_research_gate(
        domain=args.domain,
        action_type=args.action_type,
        risk=args.risk,
        factors=factor_values,
        penalties=_penalty_breakdown(args),
        rationale=getattr(args, "research_rationale", "") or "",
        sources_reviewed=[_source_record_from_cli(item) for item in (getattr(args, "source_reviewed", None) or [])],
        research_artifacts=[_artifact_record_from_cli(item) for item in (getattr(args, "research_artifact", None) or [])],
        research_completed=bool(getattr(args, "research_completed", False)),
        previous_failure=previous_failure,
        created_at=utc_now(),
    )
    if gate.get("research_mode") in {"targeted", "broad"} and not gate.get("research_completed"):
        gate["status"] = "incomplete"
    return gate


# ── Commands ──


def cmd_init(args):
    """Create a new /collab mission with the given objective and scope."""
    cwd = Path(args.cwd or ".").resolve()
    project_root = resolve_project_root(cwd)
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
        "model_map": _parse_model_map(args),
        "phase_started_at": utc_now(),
        "role_assignments": {},
        "planned_roles": list(ROLE_STATUS_ORDER),
        "skipped_roles": [],
        "fail_closed": bool(args.fail_closed),
        "research_gate": build_research_gate_from_args(args, previous_failure=False),
        "buyoffs": [],
        "evidence_sources": [],
        "role_failure_counts": {},
        "loop_count": 0,
        "loop_epoch": 0,
        "max_loops": args.max_loops,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }

    if manifest["research_gate"].get("sources_reviewed"):
        manifest["evidence_sources"] = list(manifest["research_gate"]["sources_reviewed"])

    # Capture git baseline for close-time scope verification
    import subprocess as _sp
    baseline_proc = _sp.run(
        ["git", "status", "--porcelain", "-z"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    manifest["git_baseline"] = baseline_proc.stdout if baseline_proc.returncode == 0 else ""
    if baseline_proc.returncode != 0:
        manifest["non_git_baseline"] = _capture_non_git_baseline(project_root)

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
        print("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
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
        print("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
        return 0

    manifest = ctx.manifest
    role_assignments = manifest.get("role_assignments", {})
    planned = manifest.get("planned_roles", list(ROLE_STATUS_ORDER))
    skipped = manifest.get("skipped_roles", [])
    research_gate = get_research_gate(manifest)
    buyoff_ok, buyoff_reason = plan_buyoff_is_satisfied(manifest)
    research_ok, research_reason = research_is_satisfied(manifest)

    # Categorize roles
    completed_roles: list[str] = []
    pending_roles: list[str] = []
    role_details: list[dict] = []
    for role in _role_names_for_status(manifest):
        result = load_role_result(ctx, role)
        assignment = role_assignments.get(role, {})
        status_str = _result_status(result, role=role, manifest=manifest)
        outcome = _result_outcome(result)
        if status_str in ("complete", "failed"):
            completed_roles.append(f"{role} ({outcome})" if outcome else role)
        elif role not in skipped:
            pending_roles.append(role)
        role_details.append({
            "role": role,
            "status": status_str,
            "recommendation": outcome or None,
            "model": assignment.get("model") if isinstance(assignment, dict) else None,
            "duration_seconds": assignment.get("duration_seconds") if isinstance(assignment, dict) else None,
            "finding_count": assignment.get("finding_count") if isinstance(assignment, dict) else None,
        })

    # JSON mode
    if getattr(args, "json_output", False):
        print(json.dumps({
            "mission_id": ctx.mission_id,
            "objective": manifest.get("objective", ""),
            "phase": manifest.get("phase", "unknown"),
            "elapsed": _format_elapsed(manifest.get("created_at")),
            "fail_closed": manifest.get("fail_closed", False),
            "research_gate": {
                "status": research_gate.get("status"),
                "research_mode": research_gate.get("research_mode"),
                "confidence": (research_gate.get("confidence") or {}).get("total"),
                "override_reasons": research_gate.get("override_reasons", []),
            },
            "plan_buyoff": {
                "present": plan_buyoff_exists(manifest),
                "satisfied": buyoff_ok,
                "reason": buyoff_reason,
            },
            "blocker": None if research_ok else research_reason,
            "loop_count": manifest.get("loop_count", 0),
            "max_loops": manifest.get("max_loops", 3),
            "planned_roles": planned,
            "completed": [r for r in role_details if r["status"] in ("complete", "failed")],
            "pending": [r["role"] for r in role_details if r["status"] == "pending"],
            "skipped": skipped,
            "roles": role_details,
        }, indent=2, ensure_ascii=False))
        return 0

    # Text mode with enhanced output
    lines = [
        f"Mission ID: {ctx.mission_id}",
        f"Objective: {manifest.get('objective', '')}",
        f"Current phase: {manifest.get('phase', 'unknown')}",
        f"Elapsed: {_format_elapsed(manifest.get('created_at'))}",
        f"Fail-closed: {manifest.get('fail_closed', False)}",
        "Research gate: "
        f"{research_gate.get('status', 'incomplete')} "
        f"(mode={research_gate.get('research_mode') or 'unset'}, "
        f"confidence={(research_gate.get('confidence') or {}).get('total', 'n/a')})",
        f"Plan buyoff: {'satisfied' if buyoff_ok else 'unsatisfied'} ({buyoff_reason})",
        f"Loops: {manifest.get('loop_count', 0)}/{manifest.get('max_loops', 3)}",
        f"Planned roles: {', '.join(planned)}",
        f"Completed: {', '.join(completed_roles) if completed_roles else '(none)'}",
        f"Pending: {', '.join(pending_roles) if pending_roles else '(none)'}",
        f"Skipped: {', '.join(skipped) if skipped else '(none)'}",
        "Roles:",
    ]
    if not research_ok:
        lines.insert(7, f"Blocker: {research_reason}")

    for detail in role_details:
        role = detail["role"]
        result = load_role_result(ctx, role)
        assignment = role_assignments.get(role, {})
        extras: list[str] = []
        model = detail.get("model")
        if model:
            extras.append(f"model={model}")
        duration = detail.get("duration_seconds")
        if duration is not None:
            mins, secs = divmod(int(duration), 60)
            extras.append(f"duration={mins}m{secs:02d}s")
        suffix = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"- {role}: {detail['status']}{suffix}")
        outcome = detail.get("recommendation")
        if outcome:
            lines.append(f"  recommendation: {outcome}")
        for summary_line in _role_summary_lines(result):
            lines.append(f"  {summary_line}")

    crash_warnings = manifest.get("crash_warnings", [])
    if crash_warnings:
        lines.append("WARNINGS:")
        for warning in crash_warnings:
            hook = warning.get("hook", "unknown")
            ts = warning.get("ts", "?")
            error = warning.get("error", "unknown error")
            lines.append(f"  - {hook} crashed at {ts}: {error}")
        lines.append("  Run 'collabctl show' for full details.")

    print("\n".join(lines))
    return 0


def cmd_capabilities(args):
    """Display the enforcement contract: hook semantics and platform coverage."""
    # Try loading the compiled enforcement contract
    contract_path = _PLUGIN_ROOT / "spec" / "enforcement_compiled.json"
    roles_path = _PLUGIN_ROOT / "spec" / "roles" / "roles_compiled.json"

    contract = None
    if contract_path.exists():
        contract = load_json(contract_path)
    else:
        # Fall back to raw YAML-like JSON
        raw_path = _PLUGIN_ROOT / "spec" / "enforcement.yaml"
        if raw_path.exists():
            contract = _parse_simple_yaml(raw_path)

    if not contract:
        print("Enforcement contract not found. Run generate_role_assets.py first.")
        return 1

    roles_data = None
    if roles_path.exists():
        roles_data = load_json(roles_path)

    if getattr(args, "json_output", False):
        output = {"enforcement": contract}
        if roles_data:
            output["roles"] = roles_data
        if getattr(args, "role_filter", None):
            role_name = args.role_filter
            if roles_data and isinstance(roles_data.get("roles"), dict):
                filtered = roles_data["roles"].get(role_name)
                output["roles"] = {"roles": {role_name: filtered}} if filtered else {"roles": {}}
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0

    # Text output
    hooks = contract.get("hooks", {})
    print("eight-eyes Enforcement Contract")
    print("=" * 40)
    print()
    print("Hook Enforcement Model:")
    print(f"{'Hook':<18} {'Gate Class':<14} {'Failure Mode':<16} {'Claude':<9} {'Copilot':<9} {'Codex'}")
    print("-" * 85)
    for name, hook in hooks.items():
        platforms = hook.get("platforms", {})
        print(
            f"{name:<18} {hook.get('gate_class', '?'):<14} "
            f"{hook.get('failure_mode', '?'):<16} "
            f"{platforms.get('claude_code', '?'):<9} "
            f"{platforms.get('copilot_cli', '?'):<9} "
            f"{platforms.get('codex_cli', '?')}"
        )

    print()
    print("Gate Class Semantics:")
    semantics = contract.get("gate_class_semantics", {})
    for name, sem in semantics.items():
        print(f"  {name}: {sem.get('behavior', '?')} (blocks_mission={sem.get('blocks_mission', '?')})")

    if roles_data and isinstance(roles_data.get("roles"), dict):
        role_filter = getattr(args, "role_filter", None)
        roles_dict = roles_data["roles"]
        if role_filter:
            roles_dict = {role_filter: roles_dict.get(role_filter)} if role_filter in roles_dict else {}

        print()
        print("Role Capabilities:")
        print(f"{'Role':<17} {'Scope':<14} {'Bash':<22} {'Phase':<10} {'Parallel'}")
        print("-" * 80)
        for rname, rspec in roles_dict.items():
            if not rspec:
                continue
            print(
                f"{rname:<17} {rspec.get('scope_mode', '?'):<14} "
                f"{rspec.get('bash_policy', '?'):<22} "
                f"{rspec.get('phase', '?'):<10} "
                f"{rspec.get('supports_parallel_phase', '?')}"
            )

    return 0


def _parse_simple_yaml(path: Path) -> dict | None:
    """Parse a minimal YAML subset (flat keys, nested dicts) without PyYAML."""
    try:
        text = path.read_text(encoding="utf-8")
        # For now, return None — the compiled JSON is the primary path
        return None
    except Exception:
        return None


def cmd_buyoff(args):
    """Append a structured buyoff record to the active mission manifest."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
    _, mid, _, mpath, manifest = loaded
    gate = get_research_gate(manifest)

    if args.phase == "plan":
        if not gate.get("research_mode"):
            raise SystemExit("Cannot record plan buyoff: research gate is incomplete.")
        expected_recommendation = "approve" if gate.get("research_mode") == "skip" else "approve_with_research"
        chosen_recommendation = args.recommendation or gate.get("recommendation") or expected_recommendation
        if chosen_recommendation != expected_recommendation:
            raise SystemExit(
                f"Cannot record plan buyoff: recommendation must be '{expected_recommendation}' "
                f"for research mode '{gate.get('research_mode')}'."
            )
        record = {
            "phase": "plan",
            "kind": "research_gate",
            "objective": args.objective or manifest.get("objective", ""),
            "risk": gate.get("risk"),
            "confidence_score": (gate.get("confidence") or {}).get("total"),
            "confidence_breakdown": (gate.get("confidence") or {}).get("factors", {}),
            "penalty_breakdown": (gate.get("confidence") or {}).get("penalties", {}),
            "research_mode": gate.get("research_mode"),
            "why_research_required": args.why or gate.get("rationale") or "",
            "sources_reviewed": list(gate.get("sources_reviewed") or []),
            "recommendation": chosen_recommendation,
            "note": args.note or "",
            "created_at": utc_now(),
        }
    else:
        record = {
            "phase": args.phase,
            "kind": args.kind,
            "objective": args.objective or manifest.get("objective", ""),
            "recommendation": args.recommendation or "",
            "why": args.why or "",
            "sources_reviewed": [_source_record_from_cli(item) for item in (args.source_reviewed or [])],
            "note": args.note or "",
            "created_at": utc_now(),
        }

    manifest.setdefault("buyoffs", []).append(record)
    save_manifest(mpath, manifest)
    print(f"{mid}: recorded {args.phase} buyoff ({record.get('kind', args.kind)})")
    return 0


def cmd_research(args):
    """Manage the active mission's research-gate evidence and completion state."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
    _, mid, _, mpath, manifest = loaded
    gate = get_research_gate(manifest)
    manifest.setdefault("research_gate", gate)
    manifest.setdefault("evidence_sources", [])

    if args.research_command == "add-source":
        record = {
            "title": args.title,
            "kind": args.kind,
            "location": args.location,
            "notes": args.notes or "",
            "added_at": utc_now(),
        }
        gate.setdefault("sources_reviewed", []).append(record)
        manifest["evidence_sources"].append(record)
        gate["updated_at"] = utc_now()
        save_manifest(mpath, manifest)
        print(f"{mid}: research source added")
        return 0

    if args.research_command == "add-artifact":
        record = {
            "path": args.path,
            "kind": args.kind,
            "notes": args.notes or "",
            "added_at": utc_now(),
        }
        gate.setdefault("research_artifacts", []).append(record)
        gate["updated_at"] = utc_now()
        save_manifest(mpath, manifest)
        print(f"{mid}: research artifact added")
        return 0

    if args.research_command == "complete":
        mode = gate.get("research_mode")
        if mode == "skip":
            print(f"{mid}: research mode is skip; explicit completion not required")
            return 0
        if mode not in {"targeted", "broad"}:
            raise SystemExit("Cannot complete research: research gate is incomplete.")
        sources = gate.get("sources_reviewed") or []
        artifacts = gate.get("research_artifacts") or []
        rationale = str(gate.get("rationale") or "").strip()
        if mode == "targeted":
            if len(sources) < 1:
                raise SystemExit("Cannot complete research: targeted research requires at least one reviewed source.")
            if not rationale:
                raise SystemExit("Cannot complete research: targeted research requires rationale.")
        if mode == "broad":
            if len(sources) < 2:
                raise SystemExit("Cannot complete research: broad research requires at least two reviewed sources.")
            if not artifacts and not rationale:
                raise SystemExit("Cannot complete research: broad research requires at least one artifact or evidence summary.")
        gate["research_completed"] = True
        gate["status"] = "complete"
        gate["updated_at"] = utc_now()
        save_manifest(mpath, manifest)
        print(f"{mid}: research marked complete")
        return 0

    if args.research_command == "show":
        print(json.dumps(gate, indent=2, ensure_ascii=False))
        return 0

    raise SystemExit(f"unsupported research subcommand: {args.research_command}")


def cmd_phase(args):
    """Advance the active mission to a new phase with transition validation."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
    _, mid, mdir, mpath, manifest = loaded

    current = manifest.get("phase", "plan")

    # --force audit trail
    if args.force:
        ctx = load_active_context(cwd)
        if ctx:
            append_ledger(ctx, {
                "kind": "force_override",
                "phase_from": current,
                "phase_to": args.phase,
                "tool_use_id": f"force:{ctx.mission_id}:{args.phase}",
            })
        with (mdir / "progress.md").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"- {utc_now()} WARNING: --force used to bypass transition adjacency from {current} to {args.phase}; safety gates still enforced.\n")

    # Enforce legal phase transitions unless --force
    if not args.force:
        allowed = phase_transitions_for(manifest).get(current, set())
        if args.phase not in allowed:
            raise SystemExit(
                f"Cannot transition from '{current}' to '{args.phase}'. "
                f"Allowed: {', '.join(sorted(allowed)) or '(none -- use close pass|abort)'}"
            )

    if args.phase == "implement":
        gate = get_research_gate(manifest)
        if not gate.get("research_mode"):
            raise SystemExit("Cannot transition to 'implement': research gate incomplete.")
        buyoff_ok, buyoff_reason = plan_buyoff_is_satisfied(manifest)
        if not buyoff_ok:
            raise SystemExit(f"Cannot transition to 'implement': {buyoff_reason}")
        ok, reason = research_is_satisfied(manifest)
        if not ok:
            raise SystemExit(f"Cannot transition to 'implement': {reason}")

    # Validate --skip-role values are valid audit roles
    _VALID_SKIP_ROLES = frozenset({"skeptic", "security", "performance", "accessibility"})
    for role_name in getattr(args, "skip_role", None) or []:
        if role_name not in _VALID_SKIP_ROLES:
            raise SystemExit(f"--skip-role '{role_name}' is not a valid audit role. Valid: {sorted(_VALID_SKIP_ROLES)}")

    # Record any --skip-role values into the manifest
    for role_name in getattr(args, "skip_role", None) or []:
        skipped = manifest.setdefault("skipped_roles", [])
        if role_name not in skipped:
            skipped.append(role_name)

    # Enforce audit role gate: transitioning to verify requires all four
    # audit role results to exist or be explicitly skipped via --skip-role.
    _AUDIT_ROLES = ("security", "performance", "accessibility", "skeptic")
    if args.phase == "verify":
        buyoff_ok, buyoff_reason = plan_buyoff_is_satisfied(manifest)
        if not buyoff_ok:
            raise SystemExit(f"Cannot transition to 'verify': {buyoff_reason}")
        gate = get_research_gate(manifest)
        if research_is_required(gate):
            ok, reason = research_is_satisfied(manifest)
            if not ok:
                raise SystemExit(f"Cannot transition to 'verify': {reason}")
        skipped_roles = set(manifest.get("skipped_roles", []))
        results_dir = mdir / "results"
        missing: list[str] = []
        for audit_role in _AUDIT_ROLES:
            if audit_role in skipped_roles:
                continue
            result_path = results_dir / f"{audit_role}.json"
            if not result_path.exists():
                missing.append(audit_role)
        if missing:
            raise SystemExit(
                f"Cannot transition to 'verify': audit role results missing for "
                f"{', '.join(sorted(missing))}. Run those roles first or use "
                f"--skip-role <role> to explicitly skip."
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
    manifest["phase_started_at"] = utc_now()
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
        raise SystemExit("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
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


def cmd_run(args):
    """Converge an active mission by auto-advancing phases as results arrive."""
    cwd = Path(args.cwd or ".").resolve()
    history: list[dict[str, object]] = []
    deadline = None
    if getattr(args, "timeout_seconds", 0):
        deadline = time.monotonic() + float(args.timeout_seconds)

    while True:
        payload = _controller_step(cwd, history)
        state = str(payload.get("state") or "")

        if state == "closed":
            if getattr(args, "json_output", False):
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(payload.get("reason", "Mission closed."))
            return 0

        if state in {"blocked", "waiting_for_user"}:
            if getattr(args, "json_output", False):
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(payload.get("reason", "Mission cannot auto-advance."))
            return 0

        if state == "waiting":
            if not getattr(args, "watch", False):
                if getattr(args, "json_output", False):
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                else:
                    dispatch = ", ".join(
                        f"{item['agent_type']} ({item['role']})"
                        for item in payload.get("dispatch_roles", [])
                    )
                    print(f"Phase: {payload.get('phase')}")
                    print(f"Pending roles: {', '.join(payload.get('pending_roles', [])) or '(none)'}")
                    print(f"Dispatch next: {dispatch or '(none)'}")
                    print(payload.get("reason", "Awaiting results."))
                return 0
            if deadline is not None and time.monotonic() >= deadline:
                payload["timed_out"] = True
                payload["reason"] = (
                    f"Timed out while waiting for {', '.join(payload.get('pending_roles', [])) or 'phase results'}."
                )
                if getattr(args, "json_output", False):
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                else:
                    print(payload["reason"])
                return 0
            time.sleep(float(getattr(args, "poll_interval", 1.0) or 1.0))
            continue

        raise SystemExit(f"unsupported controller state: {state}")


def _verify_close_scope(manifest: dict, project_root: Path) -> list[str]:
    """Check git diff against allowed_paths for scope violations."""
    import subprocess as _sp
    proc = _sp.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return _verify_non_git_close_scope(manifest, project_root)
    status_proc = _sp.run(
        ["git", "status", "--porcelain", "-z"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    changed_files = [f.strip() for f in proc.stdout.splitlines() if f.strip()]
    if status_proc.returncode == 0 and status_proc.stdout:
        for entry in status_proc.stdout.split("\0"):
            entry = entry.strip()
            if entry and entry[:2].strip() == "??":
                changed_files.append(entry[3:].strip())

    allowed = _scope_roots(manifest)
    if not allowed:
        return []

    violations = []
    for f in changed_files:
        if _scope_excluded(f):
            continue
        if not _scope_allowed(f, allowed):
            violations.append(f)
    return violations


def _verify_non_git_close_scope(manifest: dict, project_root: Path) -> list[str]:
    """Compare a bounded non-git baseline against current file metadata."""
    allowed = _scope_roots(manifest)
    if not allowed:
        return []

    baseline = manifest.get("non_git_baseline") or {}
    if baseline.get("status") != "captured":
        reason = baseline.get("reason", "baseline missing")
        return [f"<non-git scope verification unavailable: {reason}>"]

    current = _capture_non_git_baseline(project_root)
    if current.get("status") != "captured":
        reason = current.get("reason", "current snapshot unavailable")
        return [f"<non-git scope verification unavailable: {reason}>"]

    baseline_files = baseline.get("files", {})
    current_files = current.get("files", {})
    changed: set[str] = set()
    for rel_path, meta in current_files.items():
        if rel_path not in baseline_files or baseline_files[rel_path] != meta:
            changed.add(rel_path)
    for rel_path in baseline_files:
        if rel_path not in current_files:
            changed.add(rel_path)

    return [
        rel_path for rel_path in sorted(changed)
        if not _scope_excluded(rel_path) and not _scope_allowed(rel_path, allowed)
    ]


def cmd_close(args):
    """Close the active mission with a terminal pass or abort outcome."""
    cwd = Path(args.cwd or ".").resolve()
    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
    sroot, mid, mdir, mpath, manifest = loaded

    # Close-time scope verification
    if args.outcome == "pass" and not getattr(args, "force_close", None):
        violations = _verify_close_scope(manifest, Path(manifest.get("project_root", str(cwd))))
        if violations:
            print(f"SCOPE VIOLATIONS DETECTED ({len(violations)} files):")
            for v in violations:
                print(f"  - {v}")
            raise SystemExit(
                f"Cannot close as pass with {len(violations)} scope violation(s). "
                f"Use --force-close 'reason' to override."
            )

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
        raise SystemExit("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
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


def cmd_locate(args):
    """Print all known install locations for eight-eyes."""
    home = Path.home()
    locations = [
        ("Claude Code plugin", home / ".claude" / "plugins" / "eight-eyes"),
        ("Claude Code marketplace", home / ".claude" / "plugins" / "marketplaces" / "8eyes-marketplace"),
        ("Copilot CLI skill", home / ".copilot" / "skills" / "8eyes"),
        ("Copilot CLI marketplace", home / "AppData" / "Local" / "copilot" / "marketplaces" / "AgentBuildersApp-eight-eyes"),
        ("Codex CLI skill", home / ".codex" / "skills" / "8eyes"),
        ("Codex CLI trigger", home / ".agents" / "skills" / "8eyes-collab"),
        ("Working copy", Path(__file__).resolve().parent.parent),
    ]
    print("eight-eyes install locations:")
    for label, path in locations:
        exists = "FOUND" if path.exists() else "not found"
        print(f"  [{exists}] {label}: {path}")
    return 0


def cmd_verify(args):
    """Verify plugin installation is correct."""
    root = _PLUGIN_ROOT
    errors = []
    install_only = getattr(args, "install_only", False)

    checks = [
        (root / ".claude-plugin" / "plugin.json", "Plugin manifest"),
        (root / "hooks" / "hooks.json", "Hook wiring"),
        (root / "skills" / "collab" / "SKILL.md", "Coordinator skill"),
        (root / "docs" / "collab-golden-path.md", "Canonical operator runbook"),
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

    # Check VERSION file
    version_file = root / "VERSION"
    if version_file.exists():
        print(f"  [OK] VERSION file: VERSION")
    else:
        errors.append("Missing VERSION file")

    # Check Python version
    if sys.version_info < (3, 10):
        errors.append(f"Python {sys.version_info.major}.{sys.version_info.minor} found, need 3.10+")

    # Check git
    cwd = Path(args.cwd or ".").resolve()
    try:
        resolve_worktree_root(cwd)
        print("  [OK] Git repository detected")
    except Exception:
        if install_only:
            print("  [WARN] Not in a git repo -- install files verified; mission state will use Codex non-git state.")
        else:
            state_root = state_root_for(cwd)
            print(f"  [OK] Non-git mission state root: {state_root}")

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
        raise SystemExit("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
    sroot, mid, mdir, mpath, manifest = loaded

    current_version = manifest.get("schema_version", 1)
    if current_version >= SCHEMA_VERSION:
        print(f"{mid}: already at schema v{current_version}")
        return 0

    start_version = current_version

    if current_version == 1:
        manifest.setdefault("test_paths", ["tests/", "test/", "__tests__/"])
        manifest.setdefault("doc_paths", ["docs/", "README.md", "CONTRIBUTING.md"])
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
        current_version = 3

    if current_version == 3:
        manifest.setdefault("model_map", {})
        manifest.setdefault("phase_started_at", None)
        manifest.setdefault("role_assignments", {})
        manifest.setdefault("planned_roles", list(ROLE_STATUS_ORDER))
        manifest.setdefault("skipped_roles", [])
        manifest.setdefault("fail_closed", False)
        manifest.setdefault("research_gate", build_research_gate())
        manifest.setdefault("buyoffs", [])
        manifest.setdefault("evidence_sources", [])
        manifest["schema_version"] = 4

    save_manifest(mpath, manifest)
    print(f"{mid}: migrated from schema v{start_version} to v{manifest['schema_version']}")
    return 0


def _format_duration(seconds: int | None) -> str:
    """Format seconds as M:SS or '-' if None."""
    if seconds is None:
        return "-"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}:{secs:02d}"


def _format_time_part(iso_ts: str | None) -> str:
    """Extract HH:MM:SS from an ISO timestamp or return '-'."""
    if not iso_ts:
        return "-"
    parsed = _parse_timestamp(iso_ts)
    if parsed is None:
        return "-"
    return parsed.strftime("%H:%M:%S")


def cmd_timeline(args):
    """Display a timeline of role dispatches and completions."""
    cwd = Path(args.cwd or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx:
        print("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
        return 0

    manifest = ctx.manifest
    assignments = manifest.get("role_assignments", {})
    if not assignments:
        print("No role assignments recorded yet.")
        return 0

    header = f"{'Phase':<10} {'Role':<17} {'Model':<8} {'Status':<14} {'Start':<8} {'Dur':<8} {'Find'}"
    print(header)
    print("-" * len(header))

    # Build rows sorted by started_at
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    for role, info in assignments.items():
        if not isinstance(info, dict):
            continue
        phase = info.get("phase") or manifest.get("phase", "?")
        model = info.get("model", "?")
        outcome = info.get("outcome") or ("complete" if info.get("completed_at") else "running")
        start = _format_time_part(info.get("started_at"))
        duration = _format_duration(info.get("duration_seconds"))
        findings = str(info.get("finding_count", "-"))
        rows.append((info.get("started_at", ""), phase, role, model, str(outcome), start, duration, findings))

    rows.sort(key=lambda r: r[0])
    for _, phase, role, model, outcome, start, duration, findings in rows:
        print(f"{phase:<10} {role:<17} {model:<8} {outcome:<14} {start:<8} {duration:<8} {findings}")

    return 0


def cmd_report(args):
    """Produce a consolidated mission report with verdict and findings."""
    cwd = Path(args.cwd or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx:
        print("No active /collab mission. Run 'collabctl init --objective \"...\"' to start one.")
        return 0

    print(_build_report_text(ctx))
    return 0


def _build_report_text(ctx) -> str:
    """Build a consolidated mission report with verdict and findings."""

    manifest = ctx.manifest
    lines: list[str] = []
    lines.append(f"# Mission Report: {ctx.mission_id}")
    lines.append(f"Objective: {manifest.get('objective', '')}")
    lines.append(f"Status: {manifest.get('status', 'unknown')}")
    lines.append(f"Phase: {manifest.get('phase', 'unknown')}")
    lines.append(f"Elapsed: {_format_elapsed(manifest.get('created_at'))}")
    lines.append("")

    # Verdict summary
    assignments = manifest.get("role_assignments", {})
    all_outcomes: list[str] = []
    for role_info in assignments.values():
        if isinstance(role_info, dict) and role_info.get("outcome"):
            all_outcomes.append(str(role_info["outcome"]).lower())

    failed = [o for o in all_outcomes if o in FAILED_ROLE_OUTCOMES]
    if manifest.get("status") == "pass":
        verdict = "PASS"
    elif manifest.get("status") == "abort":
        verdict = "ABORT"
    elif failed:
        verdict = "NEEDS_CHANGES"
    elif all_outcomes:
        verdict = "IN_PROGRESS"
    else:
        verdict = "PENDING"

    lines.append(f"## Verdict: {verdict}")
    lines.append("")

    # Per-role findings grouped by severity
    lines.append("## Role Results")
    for role in _role_names_for_status(manifest):
        result = load_role_result(ctx, role)
        assignment = assignments.get(role, {})
        if not result and not assignment:
            continue
        status = _result_status(result, role=role, manifest=manifest)
        model = assignment.get("model", "?") if assignment else "?"
        duration = _format_duration(assignment.get("duration_seconds")) if assignment else "-"
        lines.append(f"### {role} ({status}, model={model}, duration={duration})")
        findings = result.get("findings", []) if isinstance(result, dict) else []
        if findings:
            for finding in findings:
                severity = finding.get("severity", "?") if isinstance(finding, dict) else "?"
                detail = _stringify_detail(finding)
                lines.append(f"  [{severity.upper()}] {detail}")
        else:
            lines.append("  (no findings)")
        lines.append("")

    # Hook health from ledger
    ledger_path = ctx.mission_dir / "ledger.ndjson"
    hook_errors = 0
    if ledger_path.exists():
        with ledger_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("kind") == "hook_error":
                    hook_errors += 1

    lines.append("## Hook Health")
    if hook_errors:
        lines.append(f"  hook_error events in ledger: {hook_errors}")
    else:
        lines.append("  No hook errors recorded.")

    crash_warnings = manifest.get("crash_warnings", [])
    if crash_warnings:
        lines.append("")
        lines.append("## Crash Warnings")
        for warning in crash_warnings:
            hook = warning.get("hook", "unknown")
            ts = warning.get("ts", "?")
            error = warning.get("error", "unknown error")
            lines.append(f"  - {hook} crashed at {ts}: {error}")
        lines.append("  Run 'collabctl show' for full details.")

    return "\n".join(lines)


def _smoke_role_result(role: str, scenario: str, round_index: int, criteria: list[str]) -> dict:
    """Return a deterministic role result fixture for smoke missions."""
    if role == "implementer":
        return {
            "role": "implementer",
            "status": "complete",
            "summary": (
                "Validated the committed orchestration surfaces and mission-state transitions."
                if round_index == 1
                else "Revalidated the orchestration flow after an audit-requested loopback."
            ),
            "changed_paths": [],
            "artifacts": [
                "No code mutation required; smoke runner validates mission lifecycle and result persistence."
            ],
            "tests_run": [],
        }
    if role == "test-writer":
        return {
            "role": "test-writer",
            "status": "complete",
            "summary": "Confirmed the committed regression coverage exists for the orchestration surfaces and that the synthetic smoke mission itself does not execute additional tests.",
            "test_files_created": ["tests/test_collab_hooks.py"],
            "coverage_targets": [
                "scripts/collabctl.py",
                "hooks/scripts/core/engine.py",
                "hooks/scripts/collab_pre_tool.py",
                "hooks/scripts/core/roles.py",
            ],
            "test_count": 0,
            "edge_cases_covered": [
                "skip/targeted/broad research modes",
                "phase-gate enforcement",
                "custom role write enforcement",
                "force-transition hardening",
            ],
        }
    if role == "skeptic":
        if scenario == "audit-loop" and round_index == 1:
            return {
                "role": "skeptic",
                "summary": "The first audit round intentionally requests one loopback to prove downstream rerun behavior.",
                "recommendation": "needs_changes",
                "findings": [
                    {
                        "severity": "medium",
                        "issue": "Loopback smoke scenario intentionally requests one re-run before final approval.",
                        "path": "scripts/collabctl.py",
                        "line": 1,
                        "evidence": "Synthetic smoke fixture for audit-loop scenario.",
                    }
                ],
                "research_gate_assessment": {
                    "confidence_overstated": False,
                    "research_skip_incorrect": False,
                    "notes": "The loopback is intentional and not caused by a research-gate failure.",
                },
            }
        return {
            "role": "skeptic",
            "summary": "Synthetic smoke mission found no internal inconsistency in the committed orchestration state flow or generated reporting surfaces.",
            "recommendation": "approve",
            "findings": [],
            "research_gate_assessment": {
                "confidence_overstated": False,
                "research_skip_incorrect": False,
                "notes": "The synthetic smoke mission validates deterministic mission-state flow and reporting only; live subagent and hook enforcement require separate audit coverage.",
            },
        }
    if role == "security":
        return {
            "role": "security",
            "summary": "Synthetic smoke mission confirms the committed mission-state orchestration path remains coherent on the clean tree without claiming live security-command execution.",
            "recommendation": "approve",
            "findings": [],
            "scan_commands_run": [],
            "research_gate_assessment": {
                "risk_classification_correct": True,
                "required_research_missing": False,
                "notes": "This synthetic mission does not execute live subagents or hooks; it validates deterministic lifecycle state and fixture persistence only.",
            },
        }
    if role == "performance":
        return {
            "role": "performance",
            "summary": "Synthetic smoke mission found no material runtime overhead in the committed orchestration surfaces and does not claim benchmark execution.",
            "recommendation": "approve",
            "findings": [],
            "benchmarks_run": [],
        }
    if role == "accessibility":
        return {
            "role": "accessibility",
            "summary": "Synthetic smoke mission confirms the runbook and status surfaces remain clear and usable on the committed tree without claiming separate accessibility-command execution.",
            "recommendation": "approve",
            "findings": [],
            "a11y_commands_run": [],
        }
    if role == "verifier":
        return {
            "role": "verifier",
            "summary": "Synthetic smoke mission passed all configured orchestration criteria on the committed tree.",
            "recommendation": "pass",
            "criteria_results": [
                {
                    "criterion": criterion,
                    "status": "pass",
                    "evidence": ["Synthetic smoke runner advanced the mission through the required lifecycle and recorded a passing outcome."]
                }
                for criterion in criteria
            ],
            "research_trace": {
                "sources_influenced_design": True,
                "research_trace_complete": True,
                "notes": "The synthetic smoke mission preserved the plan buyoff and research gate in mission state throughout the lifecycle.",
            },
        }
    if role == "docs":
        return {
            "role": "docs",
            "status": "complete",
            "summary": "Smoke mission confirms the canonical runbook and supporting docs are present on the committed tree.",
            "docs_updated": ["README.md", "CONTRIBUTING.md", "skills/collab/SKILL.md"],
            "docs_created": ["docs/collab-golden-path.md", "docs/research-gate.md"],
        }
    raise SystemExit(f"unsupported smoke role: {role}")


def cmd_smoke(args):
    """Run a deterministic synthetic mission lifecycle for orchestration validation."""
    cwd = Path(args.cwd or ".").resolve()
    if load_active(cwd):
        raise SystemExit("Cannot run smoke mission while another /collab mission is active.")

    scenario = args.scenario
    objective = f"Smoke mission ({scenario}) for committed /collab orchestration"
    init_args = argparse.Namespace(
        cwd=str(cwd),
        force=False,
        objective=objective,
        objective_file=None,
        spec_path=None,
        allowed_path=["scripts", "hooks", "skills", "agents", "tests", "docs", "README.md", "CONTRIBUTING.md"],
        denied_path=None,
        test_path=None,
        doc_path=None,
        criterion=[
            "Mission lifecycle completes cleanly.",
            "Audit roles persist structured results.",
            "Verifier passes with structured criteria evidence.",
        ],
        criteria_file=None,
        verify_command=["python3 -m pytest -q"],
        security_command=[],
        benchmark_command=[],
        a11y_command=[],
        custom_role=[],
        tdd=False,
        timeout_hours=24,
        model_map=None,
        default_model="claude",
        fail_closed=False,
        dry_run=False,
        awaiting_user=False,
        max_loops=3,
        domain="platform",
        action_type="fix",
        risk="low",
        root_cause_clarity=2,
        fix_path_clarity=2,
        verification_clarity=2,
        prior_pattern_match=2,
        environmental_stability=2,
        penalty_architecture=False,
        penalty_security_data=False,
        penalty_cross_module=False,
        penalty_ecosystem_churn=False,
        penalty_weak_observability=False,
        research_rationale="Committed-tree smoke verification of the collab lifecycle.",
        source_reviewed=[],
        research_artifact=[],
        research_completed=False,
    )
    sink = io.StringIO()
    with redirect_stdout(sink):
        cmd_init(init_args)

    loaded = load_active(cwd)
    if not loaded:
        raise SystemExit("Smoke mission failed to initialize.")
    _, mid, mdir, _, manifest = loaded
    criteria = list(manifest.get("acceptance_criteria", []))

    buyoff_args = argparse.Namespace(
        cwd=str(cwd),
        phase="plan",
        kind="manual",
        objective=objective,
        recommendation="approve",
        why="Smoke mission verifies the committed lifecycle on a clean tree.",
        source_reviewed=[],
        note="Generated by collabctl smoke.",
    )
    with redirect_stdout(sink):
        cmd_buyoff(buyoff_args)

    round_index = 1
    with redirect_stdout(sink):
        cmd_phase(argparse.Namespace(cwd=str(cwd), phase="implement", awaiting_user=False, skip_role=[], force=False))
    ctx = load_active_context(cwd)
    _save_smoke_result(ctx, "implementer", _smoke_role_result("implementer", scenario, round_index, criteria))

    with redirect_stdout(sink):
        cmd_phase(argparse.Namespace(cwd=str(cwd), phase="test", awaiting_user=False, skip_role=[], force=False))
    ctx = load_active_context(cwd)
    _save_smoke_result(ctx, "test-writer", _smoke_role_result("test-writer", scenario, round_index, criteria))

    while True:
        with redirect_stdout(sink):
            cmd_phase(argparse.Namespace(cwd=str(cwd), phase="audit", awaiting_user=False, skip_role=[], force=False))
        ctx = load_active_context(cwd)
        audit_roles = ("skeptic", "security", "performance", "accessibility")
        for role in audit_roles:
            _save_smoke_result(ctx, role, _smoke_role_result(role, scenario, round_index, criteria))

        results = [load_role_result(ctx, role) for role in audit_roles]
        if any((result or {}).get("recommendation") in FAILED_ROLE_OUTCOMES for result in results):
            round_index += 1
            with redirect_stdout(sink):
                cmd_phase(argparse.Namespace(cwd=str(cwd), phase="implement", awaiting_user=False, skip_role=[], force=False))
            ctx = load_active_context(cwd)
            _save_smoke_result(ctx, "implementer", _smoke_role_result("implementer", scenario, round_index, criteria))
            with redirect_stdout(sink):
                cmd_phase(argparse.Namespace(cwd=str(cwd), phase="test", awaiting_user=False, skip_role=[], force=False))
            ctx = load_active_context(cwd)
            _save_smoke_result(ctx, "test-writer", _smoke_role_result("test-writer", scenario, round_index, criteria))
            continue
        break

    with redirect_stdout(sink):
        cmd_phase(argparse.Namespace(cwd=str(cwd), phase="verify", awaiting_user=False, skip_role=[], force=False))
    ctx = load_active_context(cwd)
    _save_smoke_result(ctx, "verifier", _smoke_role_result("verifier", scenario, round_index, criteria))

    with redirect_stdout(sink):
        cmd_phase(argparse.Namespace(cwd=str(cwd), phase="docs", awaiting_user=False, skip_role=[], force=False))
    ctx = load_active_context(cwd)
    _save_smoke_result(ctx, "docs", _smoke_role_result("docs", scenario, round_index, criteria))

    with redirect_stdout(sink):
        cmd_close(argparse.Namespace(cwd=str(cwd), outcome="pass", reason=f"Smoke mission {scenario} completed successfully.", force_close=None))
    closed_manifest = load_json(mdir / "manifest.json", default={}) or {}
    closed_ctx = SimpleNamespace(mission_id=mid, mission_dir=mdir, manifest=closed_manifest)
    report_text = _build_report_text(closed_ctx)
    report_path = mdir / "summary.md"
    report_path.write_text(report_text + "\n", encoding="utf-8")
    payload = {
        "mission_id": mid,
        "scenario": scenario,
        "report_path": str(report_path),
        "loop_count": round_index - 1,
        "status": "pass",
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _save_smoke_result(ctx, role: str, result: dict) -> None:
    """Validate and save a deterministic smoke result."""
    ok, reason = validate_role_result(role, result, ctx.manifest)
    if not ok:
        raise SystemExit(f"invalid smoke result for {role}: {reason}")
    save_role_result(ctx, role, result)


# ── Argument Parser ──


def build_parser():
    p = argparse.ArgumentParser(prog="collabctl", description="Manage /collab mission state.")
    p.add_argument("--cwd")
    p.add_argument("--version", action="store_true", help="Print version and exit")
    sub = p.add_subparsers(dest="command", required=False)

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
    init.add_argument("--model-map", default=None, help='JSON role->model mapping, e.g. \'{"skeptic":"codex","security":"gemini"}\'')
    init.add_argument("--default-model", default="claude", help="Default model for roles not in model-map (default: claude)")
    init.add_argument("--fail-closed", action="store_true", default=False, help="Opt-in fail-closed mode for security-critical use")
    init.add_argument("--dry-run", action="store_true", help="Print the mission plan without creating files")
    init.add_argument("--awaiting-user", type=bool_arg, nargs="?", const=True, default=None)
    init.add_argument("--max-loops", type=int, default=3, help="Maximum implement loops (default: 3)")
    init.add_argument("--domain")
    init.add_argument("--action-type")
    init.add_argument("--risk")
    init.add_argument("--root-cause-clarity", type=int)
    init.add_argument("--fix-path-clarity", type=int)
    init.add_argument("--verification-clarity", type=int)
    init.add_argument("--prior-pattern-match", type=int)
    init.add_argument("--environmental-stability", type=int)
    init.add_argument("--penalty-architecture", action="store_true", default=False)
    init.add_argument("--penalty-security-data", action="store_true", default=False)
    init.add_argument("--penalty-cross-module", action="store_true", default=False)
    init.add_argument("--penalty-ecosystem-churn", action="store_true", default=False)
    init.add_argument("--penalty-weak-observability", action="store_true", default=False)
    init.add_argument("--research-rationale", default="")
    init.add_argument("--source-reviewed", action="append", default=[])
    init.add_argument("--research-artifact", action="append", default=[])
    init.add_argument("--research-completed", action="store_true", default=False)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    # show
    show = sub.add_parser("show", help="Display active mission state")
    show.set_defaults(func=cmd_show)

    # status
    status = sub.add_parser("status", help="Display active mission progress")
    status.add_argument("--json", dest="json_output", action="store_true", help="Machine-readable JSON output")
    status.set_defaults(func=cmd_status)

    # phase
    phase = sub.add_parser("phase", help="Advance to a phase")
    phase.add_argument("phase", choices=ALL_PHASES)
    phase.add_argument("--awaiting-user", type=bool_arg, nargs="?", const=True, default=None)
    phase.add_argument("--skip-role", action="append", default=[], help="Skip an audit role when transitioning to verify")
    phase.add_argument("--force", action="store_true", help="Bypass phase adjacency validation, but not research/audit safety gates")
    phase.set_defaults(func=cmd_phase)

    # buyoff
    buyoff = sub.add_parser("buyoff", help="Record a structured mission buyoff")
    buyoff.add_argument("phase", choices=ALL_PHASES)
    buyoff.add_argument("--kind", default="manual")
    buyoff.add_argument("--objective")
    buyoff.add_argument("--recommendation")
    buyoff.add_argument("--why", default="")
    buyoff.add_argument("--source-reviewed", action="append", default=[])
    buyoff.add_argument("--note", default="")
    buyoff.set_defaults(func=cmd_buyoff)

    # research
    research = sub.add_parser("research", help="Manage research-gate evidence")
    research_sub = research.add_subparsers(dest="research_command", required=True)
    research_add_source = research_sub.add_parser("add-source", help="Add a reviewed research source")
    research_add_source.add_argument("--title", required=True)
    research_add_source.add_argument("--kind", required=True)
    research_add_source.add_argument("--location", required=True)
    research_add_source.add_argument("--notes", default="")
    research_add_source.set_defaults(func=cmd_research)
    research_add_artifact = research_sub.add_parser("add-artifact", help="Add a research artifact")
    research_add_artifact.add_argument("--path", required=True)
    research_add_artifact.add_argument("--kind", required=True)
    research_add_artifact.add_argument("--notes", default="")
    research_add_artifact.set_defaults(func=cmd_research)
    research_complete = research_sub.add_parser("complete", help="Mark required research complete")
    research_complete.set_defaults(func=cmd_research)
    research_show = research_sub.add_parser("show", help="Show the current research gate")
    research_show.set_defaults(func=cmd_research)

    # progress
    progress = sub.add_parser("progress", help="Append a progress message")
    progress.add_argument("--message")
    progress.add_argument("--message-file")
    progress.set_defaults(func=cmd_progress)

    # close
    close = sub.add_parser("close", help="Close mission as pass or abort")
    close.add_argument("outcome", choices=["pass", "abort"])
    close.add_argument("--reason")
    close.add_argument("--force-close", metavar="REASON", help="Override scope violations with reason")
    close.set_defaults(func=cmd_close)

    # ledger-trim
    trim = sub.add_parser("ledger-trim", help="Trim ledger to most recent N entries")
    trim.add_argument("--keep", type=int, default=200)
    trim.set_defaults(func=cmd_ledger_trim)

    # timeline
    timeline = sub.add_parser("timeline", help="Display role dispatch/completion timeline")
    timeline.set_defaults(func=cmd_timeline)

    # report
    report = sub.add_parser("report", help="Produce consolidated mission report")
    report.set_defaults(func=cmd_report)

    # run
    run = sub.add_parser("run", help="Live mission controller: advance phases and wait for role results")
    run.add_argument("--watch", action="store_true", help="Poll until new results arrive, the mission closes, or timeout is reached")
    run.add_argument("--timeout-seconds", type=float, default=0.0, help="Stop waiting after N seconds when --watch is used")
    run.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval in seconds for --watch")
    run.add_argument("--json", dest="json_output", action="store_true", help="Machine-readable JSON output")
    run.set_defaults(func=cmd_run)

    # smoke
    smoke = sub.add_parser("smoke", help="Run a deterministic end-to-end mission smoke scenario")
    smoke.add_argument("--scenario", choices=["clean-pass", "audit-loop"], default="clean-pass")
    smoke.set_defaults(func=cmd_smoke)

    # migrate
    migrate = sub.add_parser("migrate", help="Migrate mission state to the latest schema")
    migrate.set_defaults(func=cmd_migrate)

    # verify
    verify = sub.add_parser("verify", help="Verify plugin installation")
    verify.add_argument("--install-only", action="store_true", help="Check files only, skip git requirement")
    verify.set_defaults(func=cmd_verify)

    # capabilities
    capabilities = sub.add_parser("capabilities", help="Display enforcement contract and role capabilities")
    capabilities.add_argument("--json", dest="json_output", action="store_true", help="Machine-readable JSON output")
    capabilities.add_argument("--role", dest="role_filter", help="Show capabilities for a specific role")
    capabilities.set_defaults(func=cmd_capabilities)

    # locate
    locate = sub.add_parser("locate", help="Print all known install locations")
    locate.set_defaults(func=cmd_locate)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        version_file = Path(__file__).resolve().parent.parent / "VERSION"
        if version_file.exists():
            print(version_file.read_text(encoding="utf-8").strip())
        else:
            print("unknown")
        return 0

    if not args.command:
        parser.print_help()
        return 1

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
