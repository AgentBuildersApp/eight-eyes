#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fail_open(exc: Exception) -> int:
    """Log hook failures and always allow the tool call to proceed."""
    print(f"[collab] collab_post_tool hook error: {exc}", file=sys.stderr)
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
    from collab_common import (
        ALL_COLLAB_ROLES,
        READ_ONLY_ROLES,
        append_ledger,
        custom_role_config,
        custom_role_scope_type,
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
    if not role:
        return 0
    # Recognize both built-in AND manifest-defined custom roles.
    # Previously only built-in roles were processed; custom roles
    # silently bypassed audit and revert handling (v5.0 WS-3 fix).
    is_recognized = role in ALL_COLLAB_ROLES
    custom_cfg = None
    if not is_recognized:
        custom_cfg = custom_role_config(ctx.manifest, role)
        is_recognized = custom_cfg is not None
    if not is_recognized:
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

    # Compensating revert for read-only roles (built-in AND custom)
    if entry.get("kind") == "file_mutation":
        is_read_only = role in READ_ONLY_ROLES
        if not is_read_only and custom_cfg:
            if custom_role_scope_type(custom_cfg) == "read_only":
                is_read_only = True
        if is_read_only:
            import subprocess
            revert_results = []
            for rel_path in entry.get("paths", []):
                abs_path = (ctx.project_root / rel_path).resolve()
                revert_mode = "failed"
                revert_success = False
                try:
                    check = subprocess.run(
                        ["git", "ls-files", "--error-unmatch", str(rel_path)],
                        cwd=str(ctx.project_root), capture_output=True, text=True, check=False,
                    )
                    if check.returncode == 0:
                        revert_mode = "tracked_checkout"
                        result = subprocess.run(
                            ["git", "checkout", "--", str(rel_path)],
                            cwd=str(ctx.project_root), capture_output=True, check=False,
                        )
                        revert_success = result.returncode == 0
                    else:
                        revert_mode = "untracked_delete"
                        abs_path.unlink(missing_ok=True)
                        revert_success = not abs_path.exists()
                except Exception:
                    pass  # Best-effort revert
                revert_results.append({
                    "path": rel_path,
                    "revert_mode": revert_mode,
                    "revert_success": revert_success,
                })
            append_ledger(ctx, {
                "kind": "scope_violation_reverted",
                "role": role,
                "role_type": "custom" if custom_cfg else "builtin",
                "paths": entry.get("paths", []),
                "revert_results": revert_results,
                "tool_use_id": f"revert:{entry.get('tool_use_id', 'unknown')}",
            })

    print_json(hook_context(
        "PostToolUse",
        f"[COLLAB] Recorded {tool} evidence for {payload.get('agent_type')}.",
    ))
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

