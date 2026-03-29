#!/usr/bin/env python3
"""SubagentStart hook injecting mission context into collab subagent roles."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def main() -> int:
    """Inject role-specific mission context into the SubagentStart hook."""
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
    print_json(hook_context("SubagentStart", context))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
