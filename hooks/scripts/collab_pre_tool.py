#!/usr/bin/env python3
"""PreToolUse hook enforcing role-specific tool scope for collab subagents."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _custom_role_write_paths(role_config: dict, manifest: dict) -> list[str]:
    """Return normalized custom-role write scopes from manifest config."""
    from collab_common import custom_role_scope_type

    for key in ("write_paths", "allowed_paths", "paths"):
        value = role_config.get(key)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    scope_type = custom_role_scope_type(role_config)
    if scope_type == "write_allowed":
        return [str(item).strip() for item in manifest.get("allowed_paths", []) if str(item).strip()]
    if scope_type == "write_test":
        return [str(item).strip() for item in manifest.get("test_paths", []) if str(item).strip()]
    if scope_type == "write_doc":
        return [str(item).strip() for item in manifest.get("doc_paths", []) if str(item).strip()]
    return []


def _custom_role_commands(role_config: dict) -> list[str]:
    """Return approved Bash commands for a custom role."""
    for key in ("approved_commands", "commands", "bash_commands"):
        value = role_config.get(key)
        if isinstance(value, list):
            commands: list[str] = []
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("command"), str):
                    commands.append(item["command"].strip())
                elif isinstance(item, str):
                    commands.append(item.strip())
            return [command for command in commands if command]
    return []


def _custom_role_bash_policy(role_config: dict, manifest: dict) -> str:
    """Resolve a custom role's bash policy, failing closed on unknown values."""
    raw = role_config.get("bash_policy", role_config.get("bash"))
    if role_config.get("no_bash") is True:
        return "none"
    if isinstance(raw, bool):
        return "read-only" if raw else "none"
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"none", "disabled", "off"}:
            return "none"
        if value in {"approved", "approved-only"}:
            return "approved"
        if value in {"read-only", "readonly"}:
            return "read-only"
        return "none"
    if _custom_role_write_paths(role_config, manifest):
        return "none"
    return "read-only"


from core.circuit_breaker import HookCircuitBreaker

_breaker = HookCircuitBreaker("collab_pre_tool", failure_mode="deny")


def _on_deny(reason: str) -> None:
    """Block the tool call when the pre_tool hook crashes in fail-closed mode."""
    from collab_common import pretool_deny, print_json

    print_json(pretool_deny(
        f"[COLLAB] FAIL-CLOSED: Hook error -- action blocked for safety. "
        f"{reason[:100]}"
    ))


# Legacy _fail_open retained as comment for reference:
# def _fail_open(exc: Exception) -> int:
#     print(f"[collab] collab_pre_tool hook error: {exc}", file=sys.stderr)
#     traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
#     try:
#         from collab_common import load_active_context, append_ledger
#         ctx = load_active_context(Path(".").resolve())
#         if ctx:
#             append_ledger(ctx, {"kind": "hook_error", "hook": __file__, "error": str(exc)})
#             if ctx.manifest.get("fail_closed"):
#                 from collab_common import pretool_deny, print_json
#                 print_json(pretool_deny(f"Hook error in fail-closed mode: {exc}"))
#                 return 0
#     except Exception:
#         pass
#     return 0


def _main() -> int:
    from collab_common import (
        ALL_COLLAB_ROLES,
        READ_ONLY_BASH_PATTERNS,
        READ_ONLY_ROLES,
        append_ledger,
        approved_a11y_commands,
        approved_benchmark_commands,
        approved_security_commands,
        approved_verification_commands,
        command_is_approved_extra,
        command_matches_any,
        custom_role_config,
        custom_role_scope_type,
        is_active_manifest,
        load_active_context,
        load_role_result,
        normalize_path,
        path_is_allowed,
        path_is_in_doc_paths,
        path_is_in_test_paths,
        pretool_deny,
        print_json,
        role_from_agent_type,
    )

    payload = json.loads(sys.stdin.read() or "{}")
    cwd = Path(payload.get("cwd") or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx or not is_active_manifest(ctx.manifest):
        return 0

    role = role_from_agent_type(payload.get("agent_type"))
    custom_role = None

    # Unknown collab-* agent: deny (fail-closed)
    if role is not None and role not in ALL_COLLAB_ROLES:
        custom_role = custom_role_config(ctx.manifest, role)
        if custom_role is None:
            agent_type = payload.get("agent_type")
            append_ledger(ctx, {
                "kind": "unrecognized_agent",
                "agent_type": agent_type,
                "tool_use_id": f"unrecognized:{payload.get('tool_use_id', 'unknown')}",
            })
            print_json(pretool_deny(
                f"Unrecognized collab agent type '{agent_type}'. "
                f"Register as a custom role or use a built-in role name."
            ))
            return 0

    # Not a collab agent at all -- no enforcement
    if role is None:
        return 0

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    # ==================================================================
    # WRITE ROLES: implementer, test-writer, docs
    # ==================================================================

    if role == "implementer":
        # Bash: DENIED (hard block)
        if tool == "Bash":
            print_json(pretool_deny(
                "[COLLAB] Implementer is edit-only. Bash is disabled for this role."
            ))
            return 0
        # Write/Edit/MultiEdit: allowed_paths only
        if tool in {"Write", "Edit", "MultiEdit"}:
            candidate = tool_input.get("file_path") or tool_input.get("path")
            if not candidate:
                print_json(pretool_deny(
                    "[COLLAB] Missing file path in write/edit tool input."
                ))
                return 0
            if (
                ctx.manifest.get("tdd_mode")
                and ctx.manifest.get("phase") == "implement"
                and not load_role_result(ctx, "test-writer")
            ):
                print_json(pretool_deny(
                    "[COLLAB] TDD mode: tests must be written before implementation. "
                    "The test phase must complete first."
                ))
                return 0
            ok, reason = path_is_allowed(
                normalize_path(str(candidate), cwd),
                ctx.manifest,
                ctx.project_root,
            )
            if not ok:
                print_json(pretool_deny(
                    f"[COLLAB] Out-of-scope mutation denied: {reason}"
                ))
            return 0
        return 0

    if role == "test-writer":
        # Bash: DENIED (hard block)
        if tool == "Bash":
            print_json(pretool_deny(
                "[COLLAB] Test-writer is edit-only. Bash is disabled for this role."
            ))
            return 0
        # Write/Edit/MultiEdit: test_paths only
        if tool in {"Write", "Edit", "MultiEdit"}:
            candidate = tool_input.get("file_path") or tool_input.get("path")
            if not candidate:
                print_json(pretool_deny(
                    "[COLLAB] Missing file path in write/edit tool input."
                ))
                return 0
            ok, reason = path_is_in_test_paths(
                str(candidate), ctx.manifest, ctx.project_root,
            )
            if not ok:
                print_json(pretool_deny(
                    f"[COLLAB] Test-writer write denied: {reason}"
                ))
            return 0
        return 0

    if role == "docs":
        # Bash: DENIED (hard block)
        if tool == "Bash":
            print_json(pretool_deny(
                "[COLLAB] Docs is edit-only. Bash is disabled for this role."
            ))
            return 0
        # Write/Edit/MultiEdit: doc_paths only
        if tool in {"Write", "Edit", "MultiEdit"}:
            candidate = tool_input.get("file_path") or tool_input.get("path")
            if not candidate:
                print_json(pretool_deny(
                    "[COLLAB] Missing file path in write/edit tool input."
                ))
                return 0
            ok, reason = path_is_in_doc_paths(
                str(candidate), ctx.manifest, ctx.project_root,
            )
            if not ok:
                print_json(pretool_deny(
                    f"[COLLAB] Docs write denied: {reason}"
                ))
            return 0
        return 0

    # ==================================================================
    # READ-ONLY ROLES: skeptic, security, performance, accessibility, verifier
    # ==================================================================

    if role in READ_ONLY_ROLES:
        # ALL writes DENIED
        if tool in {"Write", "Edit", "MultiEdit"}:
            print_json(pretool_deny(
                f"[COLLAB] {role.capitalize()} is read-only. "
                f"File mutation tools are disabled."
            ))
            return 0

        # Bash: read-only patterns + role-specific approved commands
        if tool == "Bash":
            cmd = str(tool_input.get("command") or "").strip()

            # Read-only patterns always allowed
            if command_matches_any(cmd, READ_ONLY_BASH_PATTERNS):
                return 0

            # Role-specific extra commands
            if role == "skeptic":
                # Skeptic: read-only patterns only, no extras
                print_json(pretool_deny(
                    "[COLLAB] Skeptic may only run approved read-only review "
                    "commands like git diff, git status, rg, grep, sed -n, "
                    "cat, head, tail, ls, or find."
                ))
                return 0

            if role == "security":
                approved = approved_security_commands(ctx.manifest)
                if command_is_approved_extra(cmd, approved):
                    return 0
                approved_list = (
                    "; ".join(approved) if approved
                    else "(no approved security scan commands configured)"
                )
                print_json(pretool_deny(
                    f"[COLLAB] Security command not approved. "
                    f"Allowed security commands: {approved_list}. "
                    f"Read-only inspection commands are also allowed."
                ))
                return 0

            if role == "performance":
                approved = approved_benchmark_commands(ctx.manifest)
                if command_is_approved_extra(cmd, approved):
                    return 0
                approved_list = (
                    "; ".join(approved) if approved
                    else "(no approved benchmark commands configured)"
                )
                print_json(pretool_deny(
                    f"[COLLAB] Performance command not approved. "
                    f"Allowed benchmark commands: {approved_list}. "
                    f"Read-only inspection commands are also allowed."
                ))
                return 0

            if role == "accessibility":
                approved = approved_a11y_commands(ctx.manifest)
                if command_is_approved_extra(cmd, approved):
                    return 0
                approved_list = (
                    "; ".join(approved) if approved
                    else "(no approved a11y commands configured)"
                )
                print_json(pretool_deny(
                    f"[COLLAB] Accessibility command not approved. "
                    f"Allowed a11y commands: {approved_list}. "
                    f"Read-only inspection commands are also allowed."
                ))
                return 0

            if role == "verifier":
                approved = approved_verification_commands(ctx.manifest)
                if command_is_approved_extra(cmd, approved):
                    return 0
                approved_list = (
                    "; ".join(approved) if approved
                    else "(no approved verification commands configured)"
                )
                print_json(pretool_deny(
                    f"[COLLAB] Verifier command not approved. "
                    f"Allowed verification commands: {approved_list}. "
                    f"Read-only inspection commands are also allowed."
                ))
                return 0

        return 0

    if custom_role is not None:
        if tool in {"Write", "Edit", "MultiEdit"}:
            candidate = tool_input.get("file_path") or tool_input.get("path")
            if not candidate:
                print_json(pretool_deny(
                    "[COLLAB] Missing file path in write/edit tool input."
                ))
                return 0
            scope_type = custom_role_scope_type(custom_role)
            write_paths = _custom_role_write_paths(custom_role, ctx.manifest)
            if scope_type == "write_test":
                ok, reason = path_is_in_test_paths(
                    str(candidate), ctx.manifest, ctx.project_root,
                )
            elif scope_type == "write_doc":
                ok, reason = path_is_in_doc_paths(
                    str(candidate), ctx.manifest, ctx.project_root,
                )
            elif not write_paths:
                print_json(pretool_deny(
                    f"[COLLAB] {role} has no writable paths configured. File mutation tools are disabled."
                ))
                return 0
            else:
                custom_manifest = dict(ctx.manifest)
                custom_manifest["allowed_paths"] = write_paths
                ok, reason = path_is_allowed(
                    normalize_path(str(candidate), cwd),
                    custom_manifest,
                    ctx.project_root,
                )
            if not ok:
                print_json(pretool_deny(
                    f"[COLLAB] {role} write denied: {reason}"
                ))
            return 0

        if tool == "Bash":
            cmd = str(tool_input.get("command") or "").strip()
            approved = _custom_role_commands(custom_role)
            bash_policy = _custom_role_bash_policy(custom_role, ctx.manifest)
            if bash_policy != "none" and command_matches_any(cmd, READ_ONLY_BASH_PATTERNS):
                return 0
            if approved and command_is_approved_extra(cmd, approved):
                return 0
            if bash_policy == "none":
                print_json(pretool_deny(
                    f"[COLLAB] {role} Bash is disabled by manifest custom_roles scope."
                ))
                return 0
            approved_list = "; ".join(approved) if approved else "(no approved commands configured)"
            print_json(pretool_deny(
                f"[COLLAB] {role} command not approved. "
                f"Allowed commands: {approved_list}. "
                f"Read-only inspection commands are also allowed."
            ))
            return 0

    return 0


def main() -> int:
    from collab_common import load_active_context

    return _breaker.execute_with_resilience(
        main_fn=_main,
        ctx_loader=lambda: load_active_context(Path(".").resolve()),
        on_deny=_on_deny,
    )


if __name__ == "__main__":
    raise SystemExit(main())
