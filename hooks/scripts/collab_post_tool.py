#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fail_open(exc: BaseException) -> int:
    """Log hook failures and always allow the tool call to proceed."""
    print(f"[collab] collab_post_tool hook error: {exc}", file=sys.stderr)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    return 0


def _main() -> int:
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


def main() -> int:
    try:
        return _main()
    except BaseException as exc:
        return _fail_open(exc)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        _fail_open(exc)
        raise SystemExit(0)
