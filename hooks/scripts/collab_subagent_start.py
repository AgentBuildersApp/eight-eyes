#!/usr/bin/env python3
"""SubagentStart hook injecting mission context into collab subagent roles."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REVIEW_CONTEXT_ROLES = {"skeptic", "security", "verifier"}


def review_md_context(ctx, role: str) -> str:
    """Return REVIEW.md context for selected reviewer roles."""
    if role not in REVIEW_CONTEXT_ROLES:
        return ""
    review_path = ctx.project_root / "REVIEW.md"
    if not review_path.exists():
        return ""
    try:
        review_text = review_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not review_text:
        return ""
    return "\n\nREVIEW.md:\n" + review_text[:2000]


def _fail_open(exc: Exception) -> int:
    """Log hook failures and fail open instead of crashing the session."""
    print(f"[collab] collab_subagent_start hook error: {exc}", file=sys.stderr)
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
    """Inject role-specific mission context into the SubagentStart hook."""
    from collab_common import (
        ALL_COLLAB_ROLES,
        DEFAULT_DETAIL_LEVELS,
        build_subagent_context,
        custom_role_config,
        hook_context,
        is_active_manifest,
        load_active_context,
        print_json,
        role_from_agent_type,
    )

    payload = json.loads(sys.stdin.read() or "{}")
    cwd = Path(payload.get("cwd") or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx or not is_active_manifest(ctx.manifest):
        return 0

    role = role_from_agent_type(payload.get("agent_type"))
    if role is None:
        return 0

    if role not in ALL_COLLAB_ROLES and custom_role_config(ctx.manifest, role) is None:
        return 0

    detail_level = DEFAULT_DETAIL_LEVELS.get(role, 2)
    context = build_subagent_context(ctx, role, detail_level=detail_level)
    context += review_md_context(ctx, role)

    # Record role dispatch timing and model
    from collab_common import append_ledger, atomic_write_json, file_lock, load_json, utc_now

    model = (ctx.manifest.get("model_map") or {}).get(role) or (ctx.manifest.get("model_map") or {}).get("default", "claude")
    with file_lock(ctx.mission_dir / ".manifest.lock"):
        manifest = load_json(ctx.manifest_path, default=ctx.manifest)
        assignments = manifest.setdefault("role_assignments", {})
        assignments[role] = {"started_at": utc_now(), "model": model, "phase": manifest.get("phase")}
        manifest["role_assignments"] = assignments
        atomic_write_json(ctx.manifest_path, manifest)
        ctx.manifest = manifest
    append_ledger(ctx, {
        "kind": "role_dispatched",
        "role": role,
        "phase": ctx.manifest.get("phase"),
        "model": model,
    })

    print_json(hook_context("SubagentStart", context))
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
