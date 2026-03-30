#!/usr/bin/env python3
"""SessionStart hook injecting active mission summary into session context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fail_open(exc: Exception) -> int:
    """Log hook failures and fail open instead of crashing the session."""
    print(f"[collab] collab_session_start hook error: {exc}", file=sys.stderr)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    try:
        from collab_common import load_active_context, append_ledger
        ctx = load_active_context(Path(".").resolve())
        if ctx:
            append_ledger(ctx, {"kind": "hook_error", "hook": __file__, "error": str(exc)})
    except Exception:
        pass  # Double-defense: if ledger write fails, still fail-open
    return 0


def _main() -> int:
    """Inject a compact mission summary into the SessionStart hook context."""
    payload = json.loads(sys.stdin.read() or "{}")
    cwd = Path(payload.get("cwd") or ".").resolve()

    import subprocess

    try:
        git_common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        state_dir = Path(git_common) / "claude-collab"
        if not state_dir.is_absolute():
            state_dir = (cwd / state_dir).resolve()
        if not state_dir.exists():
            return 0
    except Exception:
        return 0

    from collab_common import format_manifest_slim, hook_context, load_active_context, print_json

    ctx = load_active_context(cwd)
    if not ctx or ctx.manifest.get("status") != "active":
        return 0
    summary = format_manifest_slim(ctx)
    created = ctx.manifest.get("created_at", "")
    if created:
        try:
            created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > created_dt + timedelta(hours=12):
                summary = "[COLLAB WARNING] Mission is over 12 hours old.\n" + summary
        except (ValueError, TypeError):
            pass
    print_json(hook_context("SessionStart", summary))
    return 0


def main() -> int:
    try:
        return _main()
    except Exception as exc:
        return _fail_open(exc)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _fail_open(exc)
        raise SystemExit(0)

