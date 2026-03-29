#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collab_common import (
    ALL_COLLAB_ROLES,
    append_ledger,
    hook_context,
    is_active_manifest,
    load_active_context,
    print_json,
    role_from_agent_type,
    safe_rel,
)


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    cwd = Path(payload.get("cwd") or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx or not is_active_manifest(ctx.manifest):
        return 0

    role = role_from_agent_type(payload.get("agent_type"))
    if role not in ALL_COLLAB_ROLES:
        return 0

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    tool_response = payload.get("tool_response", {}) or {}

    entry = {
        "mission_id": ctx.mission_id,
        "agent_type": payload.get("agent_type"),
        "tool_name": tool,
        "tool_use_id": payload.get("tool_use_id"),
    }

    if tool in {"Write", "Edit", "MultiEdit"}:
        fp = (
            tool_input.get("file_path")
            or tool_input.get("path")
            or tool_response.get("filePath")
            or tool_response.get("file_path")
        )
        rel = str(fp)
        try:
            rel = safe_rel(Path(fp).resolve(strict=False), ctx.project_root)
        except Exception:
            pass
        entry.update({
            "kind": "file_mutation",
            "paths": [rel],
            "success": bool(tool_response.get("success", True)),
        })
    elif tool == "Bash":
        entry.update({
            "kind": "bash_execution",
            "command": str(tool_input.get("command") or ""),
            "success": bool(tool_response.get("success", True)),
            "exit_code": (
                tool_response.get("exitCode")
                if isinstance(tool_response, dict)
                else None
            ),
        })
    else:
        return 0

    append_ledger(ctx, entry)
    print_json(hook_context(
        "PostToolUse",
        f"[COLLAB] Recorded {tool} evidence for {payload.get('agent_type')}.",
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
