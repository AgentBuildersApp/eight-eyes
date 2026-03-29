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


def resolve_git_common_dir(cwd: Path) -> Path:
    """Return the absolute git common directory for cwd."""
    out = repo_git(["rev-parse", "--git-common-dir"], cwd)
    path = Path(out)
    if not path.is_absolute():
        path = (cwd / path).resolve()
    return path


def state_root_for(cwd: Path) -> Path:
    """Return the shared collab state directory for cwd."""
    return resolve_git_common_dir(cwd) / SHARED_STATE_DIRNAME


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
    try:
        project_root = resolve_worktree_root(cwd)
        git_common = resolve_git_common_dir(cwd)
    except CollabError:
        return None
    state_root = git_common / SHARED_STATE_DIRNAME
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
    "RESULT_BEGIN",
    "RESULT_END",
    "SHARED_STATE_DIRNAME",
    "active_pointer_path",
    "append_ledger",
    "atomic_write_json",
    "atomic_write_text",
    "changed_paths_from_ledger",
    "changed_paths_from_summary",
    "extract_result_block",
    "file_lock",
    "format_manifest_slim",
    "hook_context",
    "is_active_manifest",
    "load_active_context",
    "load_json",
    "load_role_result",
    "pretool_deny",
    "print_json",
    "recent_progress",
    "repo_git",
    "resolve_git_common_dir",
    "resolve_worktree_root",
    "result_file",
    "save_role_result",
    "spec_hash",
    "state_root_for",
    "stop_block",
    "utc_now",
]
