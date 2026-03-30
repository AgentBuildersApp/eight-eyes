#!/usr/bin/env python3
"""SubagentStop hook validating and persisting collab role result blocks."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def save_manifest(ctx) -> None:
    """Persist manifest updates made by the subagent stop hook."""
    from collab_common import atomic_write_json, utc_now

    ctx.manifest["updated_at"] = utc_now()
    atomic_write_json(ctx.manifest_path, ctx.manifest)


from core.circuit_breaker import HookCircuitBreaker

_breaker = HookCircuitBreaker("collab_subagent_stop", failure_mode="block")


# Legacy _fail_open retained as comment for reference:
# def _fail_open(exc: Exception) -> int:
#     print(f"[collab] collab_subagent_stop hook error: {exc}", file=sys.stderr)
#     traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
#     try:
#         from collab_common import load_active_context, append_ledger
#         ctx = load_active_context(Path(".").resolve())
#         if ctx:
#             append_ledger(ctx, {"kind": "hook_error", "hook": __file__, "error": str(exc)})
#     except Exception:
#         pass
#     return 0


def _main() -> int:
    from collab_common import (
        ALL_COLLAB_ROLES,
        RESULT_BEGIN,
        RESULT_END,
        append_ledger,
        custom_role_config,
        extract_result_block,
        is_active_manifest,
        load_active_context,
        print_json,
        role_from_agent_type,
        save_role_result,
        stop_block,
        validate_role_result,
    )

    payload = json.loads(sys.stdin.read() or "{}")
    cwd = Path(payload.get("cwd") or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx or not is_active_manifest(ctx.manifest):
        return 0

    if bool(payload.get("stop_hook_active")):
        return 0

    role = role_from_agent_type(payload.get("agent_type"))
    if role is None:
        return 0
    custom_role = None
    if role not in ALL_COLLAB_ROLES:
        if (custom_role := custom_role_config(ctx.manifest, role)) is None:
            print(
                f"[collab] Unrecognized collab agent_type '{payload.get('agent_type')}', "
                f"skipping result handling",
                file=sys.stderr,
            )
            return 0

    result = extract_result_block(
        str(payload.get("last_assistant_message") or "")
    )
    ok, reason = validate_role_result(role, result or {}, ctx.manifest, custom_role=custom_role)
    if not ok:
        failure_counts = ctx.manifest.setdefault("role_failure_counts", {})
        failure_counts[role] = int(failure_counts.get(role, 0) or 0) + 1
        if failure_counts[role] >= 3:
            ctx.manifest["awaiting_user"] = True
            ctx.manifest["awaiting_user_reason"] = (
                f"{role} failed validation {failure_counts[role]} times: {reason}"
            )
        save_manifest(ctx)
        print_json(stop_block(
            f"[COLLAB] {payload.get('agent_type')} cannot finish yet: {reason}. "
            f"End with a valid {role} result block between "
            f"{RESULT_BEGIN} and {RESULT_END}."
        ))
        return 0

    failure_counts = ctx.manifest.setdefault("role_failure_counts", {})
    if failure_counts.get(role):
        failure_counts[role] = 0
        save_manifest(ctx)

    save_role_result(ctx, role, result)

    # Record role completion with timing (under file lock for race safety)
    from collab_common import atomic_write_json, file_lock, load_json, utc_now

    with file_lock(ctx.mission_dir / ".manifest.lock"):
        manifest = load_json(ctx.manifest_path, default=ctx.manifest)
        assignments = manifest.get("role_assignments", {})
        role_info = assignments.get(role, {})
        started = role_info.get("started_at")
        duration = None
        if started:
            from datetime import datetime, timezone
            try:
                start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                duration = int((datetime.now(timezone.utc) - start_dt).total_seconds())
            except (ValueError, TypeError):
                pass
        role_info["completed_at"] = utc_now()
        if duration is not None:
            role_info["duration_seconds"] = duration
        finding_count = len(result.get("findings", []))
        role_info["finding_count"] = finding_count
        role_info["outcome"] = result.get("status") or result.get("recommendation")
        assignments[role] = role_info
        manifest["role_assignments"] = assignments
        atomic_write_json(ctx.manifest_path, manifest)
        ctx.manifest = manifest

    append_ledger(ctx, {
        "mission_id": ctx.mission_id,
        "agent_type": payload.get("agent_type"),
        "tool_name": "RESULT",
        "tool_use_id": f"result:{payload.get('agent_id')}",
        "kind": "role_completed",
        "role": role,
        "phase": ctx.manifest.get("phase"),
        "outcome": role_info["outcome"],
        "duration_seconds": duration,
        "finding_count": finding_count,
        "model": role_info.get("model", "unknown"),
        "status": result.get("status") or result.get("recommendation"),
    })
    return 0


def main() -> int:
    from collab_common import load_active_context, print_json, stop_block

    def _on_escalate(ctx, reason):
        """Escalate to user when result validation crashes."""
        from collab_common import atomic_write_json, utc_now

        ctx.manifest["awaiting_user"] = True
        ctx.manifest["awaiting_user_reason"] = reason
        ctx.manifest["updated_at"] = utc_now()
        atomic_write_json(ctx.manifest_path, ctx.manifest)
        print_json(stop_block(f"[COLLAB] FAIL-CLOSED: {reason}"))

    return _breaker.execute_with_resilience(
        main_fn=_main,
        ctx_loader=lambda: load_active_context(Path(".").resolve()),
        on_escalate=_on_escalate,
    )


if __name__ == "__main__":
    raise SystemExit(main())

