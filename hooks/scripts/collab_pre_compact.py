#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collab_common import atomic_write_json, load_active_context, utc_now


def main() -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
