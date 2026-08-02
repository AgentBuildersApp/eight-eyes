"""Core engine for the collab-plugin hook system.

Provides shared state management, atomic file I/O, NDJSON ledger operations,
cross-platform file locking, and mission context resolution used by all hooks.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

RESULT_BEGIN = "COLLAB_RESULT_JSON_BEGIN"
RESULT_END = "COLLAB_RESULT_JSON_END"
COLLAB_PREFIX = "collab-"
SHARED_STATE_DIRNAME = "claude-collab"
RESEARCH_FACTOR_KEYS = (
    "root_cause_clarity",
    "fix_path_clarity",
    "verification_clarity",
    "prior_pattern_match",
    "environmental_stability",
)
RESEARCH_PENALTY_WEIGHTS = {
    "architecture_irreversible": -3,
    "security_auth_billing_data": -3,
    "cross_module_integration": -2,
    "ecosystem_churn": -2,
    "weak_observability": -2,
}


class CollabError(RuntimeError):
    """Raised when a collab-plugin operation fails."""

    pass


@dataclass(slots=True)
class MissionContext:
    """Resolved state for the currently active collab mission."""

    cwd: Path
    project_root: Path
    git_common_dir: Path
    state_root: Path
    active_path: Path
    mission_id: str
    mission_dir: Path
    manifest_path: Path
    manifest: Dict[str, Any]


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string with 'Z' suffix."""
    return (
        _dt.datetime.now(tz=_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def print_json(obj: Dict[str, Any]) -> None:
    """Write obj as a single JSON line to stdout."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))


def hook_context(event_name: str, text: str) -> Dict[str, Any]:
    """Build a hook response dict that injects text as additional context."""
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }


def pretool_deny(reason: str) -> Dict[str, Any]:
    """Build a PreToolUse hook response that denies the tool call."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def stop_block(reason: str) -> Dict[str, Any]:
    """Build a response that blocks the hook event."""
    return {"decision": "block", "reason": reason}


def repo_git(args: List[str], cwd: Path) -> str:
    """Run a git command and return stdout. Raises CollabError on failure."""
    import subprocess

    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CollabError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def resolve_worktree_root(cwd: Path) -> Path:
    """Return the absolute git worktree root for cwd."""
    return Path(repo_git(["rev-parse", "--show-toplevel"], cwd)).resolve()


def resolve_project_root(cwd: Path) -> Path:
    """Return the git worktree root, or cwd for non-git audits."""
    try:
        return resolve_worktree_root(cwd)
    except CollabError:
        return cwd.resolve()


def resolve_git_common_dir(cwd: Path) -> Path:
    """Return the absolute git common directory for cwd."""
    out = repo_git(["rev-parse", "--git-common-dir"], cwd)
    path = Path(out)
    if not path.is_absolute():
        path = (cwd / path).resolve()
    return path


def _non_git_state_root(project_root: Path) -> Path:
    """Return a stable Codex-owned state root for a non-git project root."""
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    slug = "".join(ch if ch.isalnum() else "-" for ch in project_root.name.lower()).strip("-")
    if not slug:
        slug = "root"
    digest = hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()[:16]
    return codex_home / "collab-state" / "non-git" / f"{slug}-{digest}" / SHARED_STATE_DIRNAME


def collab_state_root_for(cwd: Path) -> Path:
    """Return the shared collab state directory for git or non-git cwd."""
    try:
        return resolve_git_common_dir(cwd) / SHARED_STATE_DIRNAME
    except CollabError:
        return _non_git_state_root(resolve_project_root(cwd))


def state_root_for(cwd: Path) -> Path:
    """Return the shared collab state directory for cwd."""
    return collab_state_root_for(cwd)


def active_pointer_path(state_root: Path) -> Path:
    """Return the path to the active mission pointer file."""
    return state_root / "active.json"


def load_json(path: Path, default: Optional[Any] = None) -> Any:
    """Load and parse a JSON file, returning default if missing or corrupt."""
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(f"[collab] warning: failed to load JSON from {path}: {exc}", file=sys.stderr)
        return default


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically write content to path via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmpname, path)
    finally:
        try:
            if os.path.exists(tmpname):
                os.unlink(tmpname)
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, obj: Any) -> None:
    """Atomically write obj as pretty-printed JSON to path."""
    atomic_write_text(
        path,
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


@contextlib.contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    """Cross-platform file lock. Uses fcntl on Unix, msvcrt on Windows."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            import time

            for attempt in range(50):
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except (OSError, PermissionError):
                    time.sleep(0.02 * (attempt + 1))
            else:
                print(
                    f"[collab] warning: lock contention on {lock_path}, "
                    "falling back to blocking lock after 50 attempts",
                    file=sys.stderr,
                )
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, PermissionError) as exc:
                    print(f"[collab] warning: failed to unlock {lock_path}: {exc}", file=sys.stderr)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def load_active_context(cwd: Path) -> Optional[MissionContext]:
    """Resolve the active mission context from cwd, or None."""
    project_root = resolve_project_root(cwd)
    try:
        git_common = resolve_git_common_dir(cwd)
        state_root = git_common / SHARED_STATE_DIRNAME
    except CollabError:
        state_root = _non_git_state_root(project_root)
        git_common = state_root
    active_path = active_pointer_path(state_root)
    active = load_json(active_path, default=None)
    if not active or not isinstance(active, dict) or not active.get("mission_id"):
        return None
    mission_id = active["mission_id"]
    mission_dir = state_root / "missions" / mission_id
    manifest_path = mission_dir / "manifest.json"
    manifest = load_json(manifest_path, default=None)
    if not isinstance(manifest, dict):
        return None
    return MissionContext(
        cwd=cwd.resolve(),
        project_root=project_root,
        git_common_dir=git_common,
        state_root=state_root,
        active_path=active_path,
        mission_id=mission_id,
        mission_dir=mission_dir,
        manifest_path=manifest_path,
        manifest=manifest,
    )


def is_active_manifest(manifest: Dict[str, Any]) -> bool:
    """Return True if manifest represents an active non-terminal mission."""
    return manifest.get("status") == "active" and manifest.get("phase") not in {
        "pass",
        "abort",
    }


def clamp_score(value: int) -> int:
    """Clamp a Stage 0 factor score to the supported 0..2 range."""
    return max(0, min(2, int(value)))


def compute_confidence_score(factors: Dict[str, int], penalties: Dict[str, int]) -> int:
    """Return the clamped 0..10 confidence score for Stage 0 classification."""
    total = sum(int(factors.get(key, 0)) for key in RESEARCH_FACTOR_KEYS)
    total += sum(int(value) for value in penalties.values())
    return max(0, min(10, total))


def determine_research_mode(score: int) -> str:
    """Map a confidence score to skip/targeted/broad research mode."""
    if score >= 8:
        return "skip"
    if score >= 5:
        return "targeted"
    return "broad"


def apply_research_overrides(classification: Dict[str, Any], score: int) -> tuple[str, List[str]]:
    """Apply deterministic policy overrides to the base research mode."""
    mode = determine_research_mode(score)
    reasons: List[str] = []
    action_type = str(classification.get("action_type") or "").strip().lower()
    risk = str(classification.get("risk") or "").strip().lower()
    penalties = classification.get("penalties") or {}
    factors = classification.get("factors") or {}
    domain = str(classification.get("domain") or "").strip().lower()
    previous_failure = bool(classification.get("previous_failure"))

    def escalate(target: str, reason: str) -> None:
        nonlocal mode
        order = {"skip": 0, "targeted": 1, "broad": 2}
        if reason not in reasons:
            reasons.append(reason)
        if order[target] > order[mode]:
            mode = target

    if risk == "medium":
        escalate("targeted", "medium_risk_requires_targeted_research")
    if risk == "high":
        escalate("broad", "high_risk_requires_broad_research")
    if risk == "critical":
        escalate("broad", "critical_risk_requires_broad_research")

    security_penalty = int(penalties.get("security_auth_billing_data", 0)) < 0
    if security_penalty or domain in {"security", "data", "auth", "billing"}:
        escalate("targeted", "security_sensitive_work_requires_targeted_research")

    if previous_failure:
        escalate("targeted", "previous_failed_attempt_requires_targeted_research")

    if action_type == "architecture":
        architecture_broad = (
            risk in {"medium", "high", "critical"}
            or int(penalties.get("cross_module_integration", 0)) < 0
            or security_penalty
            or int(factors.get("verification_clarity", 0)) <= 0
        )
        if architecture_broad:
            escalate("broad", "architecture_change_requires_broad_research")
        else:
            escalate("targeted", "architecture_change_requires_targeted_research")

    return mode, reasons


def recommendation_for_mode(mode: str) -> str:
    """Return the default plan recommendation for a research mode."""
    if mode == "skip":
        return "approve"
    if mode in {"targeted", "broad"}:
        return "approve_with_research"
    return "block"


def build_research_gate(
    *,
    domain: str | None = None,
    action_type: str | None = None,
    risk: str | None = None,
    factors: Dict[str, int] | None = None,
    penalties: Dict[str, int] | None = None,
    rationale: str = "",
    sources_reviewed: List[Dict[str, Any]] | None = None,
    research_artifacts: List[Dict[str, Any]] | None = None,
    research_completed: bool = False,
    previous_failure: bool = False,
    created_at: str | None = None,
) -> Dict[str, Any]:
    """Build a normalized research_gate structure."""
    created = created_at or utc_now()
    if not domain or not action_type or not risk or factors is None or penalties is None:
        return {
            "status": "incomplete",
            "domain": domain,
            "action_type": action_type,
            "risk": risk,
            "confidence": {"total": None, "factors": {}, "penalties": {}},
            "research_mode": None,
            "override_reasons": [],
            "rationale": rationale,
            "sources_reviewed": list(sources_reviewed or []),
            "research_artifacts": list(research_artifacts or []),
            "research_completed": False,
            "recommendation": "block",
            "created_at": created,
            "updated_at": created,
        }

    normalized_factors = {
        key: clamp_score(int(factors.get(key, 0)))
        for key in RESEARCH_FACTOR_KEYS
    }
    normalized_penalties = {
        key: int(penalties.get(key, 0))
        for key in RESEARCH_PENALTY_WEIGHTS
    }
    total = compute_confidence_score(normalized_factors, normalized_penalties)
    research_mode, override_reasons = apply_research_overrides(
        {
            "domain": domain,
            "action_type": action_type,
            "risk": risk,
            "factors": normalized_factors,
            "penalties": normalized_penalties,
            "previous_failure": previous_failure,
        },
        total,
    )
    status = "complete" if research_mode == "skip" else ("complete" if research_completed else "incomplete")
    return {
        "status": status,
        "domain": domain,
        "action_type": action_type,
        "risk": risk,
        "confidence": {
            "total": total,
            "factors": normalized_factors,
            "penalties": normalized_penalties,
        },
        "research_mode": research_mode,
        "override_reasons": override_reasons,
        "rationale": rationale,
        "sources_reviewed": list(sources_reviewed or []),
        "research_artifacts": list(research_artifacts or []),
        "research_completed": bool(research_completed),
        "recommendation": recommendation_for_mode(research_mode),
        "created_at": created,
        "updated_at": created,
    }


def get_research_gate(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Return the manifest research_gate object or an incomplete default."""
    gate = manifest.get("research_gate")
    if isinstance(gate, dict):
        return gate
    return build_research_gate()


def research_is_required(gate: Dict[str, Any]) -> bool:
    """Return True when research evidence is required before implementation."""
    mode = gate.get("research_mode")
    if mode in {"targeted", "broad"}:
        return True
    return mode != "skip"


def latest_buyoff(manifest: Dict[str, Any], phase: str, kind: str | None = None) -> Dict[str, Any] | None:
    """Return the most recent buyoff matching phase/kind, if any."""
    buyoffs = manifest.get("buyoffs", [])
    if not isinstance(buyoffs, list):
        return None
    for item in reversed(buyoffs):
        if not isinstance(item, dict):
            continue
        if item.get("phase") != phase:
            continue
        if kind is not None and item.get("kind") != kind:
            continue
        return item
    return None


def plan_buyoff_is_satisfied(manifest: Dict[str, Any]) -> tuple[bool, str]:
    """Return whether the latest plan/research_gate buyoff matches the gate mode."""
    buyoff = latest_buyoff(manifest, "plan", "research_gate")
    if not buyoff:
        return False, "plan buyoff missing."
    recommendation = str(buyoff.get("recommendation") or "").strip()
    gate = get_research_gate(manifest)
    mode = gate.get("research_mode")
    if mode == "skip":
        if recommendation != "approve":
            return False, "plan buyoff recommendation must be 'approve' for skip research."
        return True, "plan buyoff satisfied."
    if mode in {"targeted", "broad"}:
        if recommendation != "approve_with_research":
            return False, "plan buyoff recommendation must be 'approve_with_research' when research is required."
        return True, "plan buyoff satisfied."
    return False, "research gate incomplete."


def plan_buyoff_exists(manifest: Dict[str, Any]) -> bool:
    """Return True when a structured plan/research_gate buyoff is present."""
    return latest_buyoff(manifest, "plan", "research_gate") is not None


def research_is_satisfied(manifest: Dict[str, Any]) -> tuple[bool, str]:
    """Return whether the manifest satisfies the research gate."""
    gate = get_research_gate(manifest)
    mode = gate.get("research_mode")
    if not mode:
        return False, "research gate incomplete."
    buyoff_ok, buyoff_reason = plan_buyoff_is_satisfied(manifest)
    if not buyoff_ok:
        return False, buyoff_reason

    rationale = str(gate.get("rationale") or "").strip()
    confidence = gate.get("confidence") or {}
    total = confidence.get("total")
    sources = gate.get("sources_reviewed") or []
    artifacts = gate.get("research_artifacts") or []

    if mode == "skip":
        if total is None:
            return False, "skip research requires a confidence score."
        if not rationale:
            return False, "skip research requires rationale."
        if not gate.get("recommendation"):
            return False, "skip research requires recommendation."
        return True, "skip research satisfied."

    if not gate.get("research_completed"):
        return False, f"{mode} research required but research_completed is false."
    if mode == "targeted":
        if len(sources) < 1:
            return False, "targeted research required but no reviewed sources recorded."
        if not rationale:
            return False, "targeted research required but rationale is missing."
        return True, "targeted research satisfied."

    if mode == "broad":
        if len(sources) < 2:
            return False, "broad research required but fewer than two reviewed sources are recorded."
        if not artifacts and not rationale:
            return False, "broad research required but no artifact or evidence summary is recorded."
        return True, "broad research satisfied."

    return False, "research gate mode is invalid."


def spec_hash(project_root: Path, spec_path: str) -> Optional[str]:
    """Compute SHA-256 hex digest of a spec file, or None if missing."""
    if not spec_path:
        return None
    path = (project_root / spec_path).resolve()
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def changed_paths_from_ledger(ctx: MissionContext) -> List[str]:
    """Return deduplicated file paths from all ledger entries."""
    ledger = ctx.mission_dir / "ledger.ndjson"
    if not ledger.exists():
        return []
    paths: List[str] = []
    seen: set[str] = set()
    with ledger.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for path in row.get("paths", []):
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
    return paths


def _update_ledger_summary(ctx: MissionContext) -> None:
    """Refresh the cached ledger summary JSON for quick changed-path lookups."""
    ledger = ctx.mission_dir / "ledger.ndjson"
    entry_count = 0
    changed_paths: List[str] = []
    seen: set[str] = set()
    if ledger.exists():
        with ledger.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_count += 1
                for path in row.get("paths", []):
                    if isinstance(path, str) and path not in seen:
                        seen.add(path)
                        changed_paths.append(path)
    atomic_write_json(
        ctx.mission_dir / "ledger-summary.json",
        {
            "entry_count": entry_count,
            "changed_paths": changed_paths,
            "last_updated": utc_now(),
        },
    )


def changed_paths_from_summary(ctx: MissionContext) -> List[str]:
    """Return cached changed paths, falling back to a full ledger scan."""
    summary = load_json(ctx.mission_dir / "ledger-summary.json", default=None)
    if not isinstance(summary, dict):
        return changed_paths_from_ledger(ctx)
    changed_paths = summary.get("changed_paths")
    if not isinstance(changed_paths, list) or not all(isinstance(path, str) for path in changed_paths):
        return changed_paths_from_ledger(ctx)
    return changed_paths


def recent_progress(ctx: MissionContext, limit: int = 12) -> List[str]:
    """Return the last limit lines from progress.md."""
    progress_path = ctx.mission_dir / "progress.md"
    if not progress_path.exists():
        return []
    lines = progress_path.read_text(encoding="utf-8").splitlines()
    return lines[-limit:]


def append_ledger(ctx: MissionContext, entry: Dict[str, Any]) -> None:
    """Append entry to the NDJSON ledger, deduplicating by tool_use_id."""
    ledger = ctx.mission_dir / "ledger.ndjson"
    lock = ctx.mission_dir / ".ledger.lock"
    row = dict(entry)
    row.setdefault("ts", utc_now())
    ctx.mission_dir.mkdir(parents=True, exist_ok=True)
    with file_lock(lock):
        seen_ids: set[str] = set()
        if ledger.exists():
            with ledger.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tool_use_id = existing.get("tool_use_id")
                    if tool_use_id:
                        seen_ids.add(tool_use_id)
        if row.get("tool_use_id") and row["tool_use_id"] in seen_ids:
            return
        with ledger.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        _update_ledger_summary(ctx)


def result_file(ctx: MissionContext, role: str) -> Path:
    """Return the path to the result JSON file for role."""
    return ctx.mission_dir / "results" / f"{role}.json"


def load_role_result(
    ctx: MissionContext,
    role: str,
) -> Optional[Dict[str, Any]]:
    """Load a role result, returning None if missing or stale epoch."""
    result = load_json(result_file(ctx, role), default=None)
    if not isinstance(result, dict):
        return None
    current_epoch = ctx.manifest.get("loop_epoch", 0)
    result_epoch = result.get("_epoch", 0)
    if result_epoch != current_epoch:
        return None
    return result


def save_role_result(
    ctx: MissionContext,
    role: str,
    result: Dict[str, Any],
) -> None:
    """Save a role result atomically with current loop epoch stamp."""
    stamped = {**result, "_epoch": ctx.manifest.get("loop_epoch", 0)}
    path = result_file(ctx, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(ctx.mission_dir / ".results.lock"):
        atomic_write_json(path, stamped)


def extract_result_block(text: str) -> Optional[Dict[str, Any]]:
    """Extract the last COLLAB_RESULT_JSON_BEGIN/END block from text."""
    if not text:
        return None
    try:
        start_marker = text.rindex(RESULT_BEGIN)
        start = start_marker + len(RESULT_BEGIN)
        end = text.index(RESULT_END, start)
        block = text[start:end].strip()
        return json.loads(block)
    except (ValueError, json.JSONDecodeError):
        return None


def format_manifest_slim(ctx: MissionContext) -> str:
    """Lazily resolve the slim manifest formatter from roles.py."""
    from .roles import format_manifest_slim as _format_manifest_slim

    return _format_manifest_slim(ctx)


__all__ = [
    "COLLAB_PREFIX",
    "CollabError",
    "MissionContext",
    "RESEARCH_FACTOR_KEYS",
    "RESEARCH_PENALTY_WEIGHTS",
    "RESULT_BEGIN",
    "RESULT_END",
    "SHARED_STATE_DIRNAME",
    "active_pointer_path",
    "apply_research_overrides",
    "append_ledger",
    "atomic_write_json",
    "atomic_write_text",
    "build_research_gate",
    "changed_paths_from_ledger",
    "changed_paths_from_summary",
    "clamp_score",
    "compute_confidence_score",
    "determine_research_mode",
    "extract_result_block",
    "file_lock",
    "format_manifest_slim",
    "get_research_gate",
    "hook_context",
    "is_active_manifest",
    "latest_buyoff",
    "load_active_context",
    "load_json",
    "load_role_result",
    "plan_buyoff_is_satisfied",
    "plan_buyoff_exists",
    "pretool_deny",
    "print_json",
    "recent_progress",
    "recommendation_for_mode",
    "repo_git",
    "research_is_required",
    "research_is_satisfied",
    "collab_state_root_for",
    "resolve_git_common_dir",
    "resolve_project_root",
    "resolve_worktree_root",
    "result_file",
    "save_role_result",
    "spec_hash",
    "state_root_for",
    "stop_block",
    "utc_now",
]
