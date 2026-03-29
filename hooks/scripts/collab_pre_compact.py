#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fail_open(exc: Exception) -> int:
    """Log hook failures and fail open instead of crashing the session."""
    print(f"[collab] collab_pre_compact hook error: {exc}", file=sys.stderr)
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
    from collab_common import atomic_write_json, load_active_context, utc_now

    payload = json.loads(sys.stdin.read() or "{}")
    cwd = Path(payload.get("cwd") or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx:
        return 0

    snapshot_dir = ctx.mission_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    progress_path = ctx.mission_dir / "progress.md"
    snap = {
        "captured_at": utc_now(),
        "source": (
            payload.get("trigger")
            or payload.get("hook_event_name")
            or "PreCompact"
        ),
        "manifest": ctx.manifest,
        "progress_tail": (
            progress_path.read_text(encoding="utf-8").splitlines()[-20:]
            if progress_path.exists()
            else []
        ),
    }

    ts_slug = utc_now().replace(":", "").replace("-", "")
    atomic_write_json(snapshot_dir / f"{ts_slug}.json", snap)
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
