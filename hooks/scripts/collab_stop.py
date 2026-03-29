#!/usr/bin/env python3
"""Stop hook blocking session exit while collab mission work is pending."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collab_common import (
    is_active_manifest,
    load_active_context,
    load_role_result,
    print_json,
    stop_block,
)


def mission_timed_out(manifest: dict) -> bool:
    """Return True when the mission age exceeds timeout_hours.

    A *timeout_hours* of 0 or negative disables the timeout (returns False).
    """
    created = manifest.get("created_at", "")
    timeout_h = manifest.get("timeout_hours", 24)
    if not created or not isinstance(timeout_h, (int, float)) or timeout_h <= 0:
        return False
    try:
        created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > created_dt + timedelta(hours=timeout_h)
    except (ValueError, TypeError):
        return False


def main() -> int:
    """Block session exit while mission phases have unfinished work."""
    payload = json.loads(sys.stdin.read() or "{}")
    cwd = Path(payload.get("cwd") or ".").resolve()
    ctx = load_active_context(cwd)
    if not ctx or not is_active_manifest(ctx.manifest):
        return 0

    if bool(payload.get("stop_hook_active")):
        return 0

    if bool(ctx.manifest.get("awaiting_user")):
        return 0

    if mission_timed_out(ctx.manifest):
        return 0

    phase = ctx.manifest.get("phase")
    pending: list[str] = []

    # -- plan phase: waiting on user approval ------------------------------
    if phase == "plan":
        pending.append("user approval or explicit close")

    # -- implement phase: implementer must finish --------------------------
    elif phase == "implement":
        if not load_role_result(ctx, "implementer"):
            pending.append("implementer result")

    # -- test phase: test-writer must finish --------------------------------
    elif phase == "test":
        if not load_role_result(ctx, "test-writer"):
            pending.append("test-writer result")

    # -- audit phase: all four audit reviewers must finish ------------------
    elif phase == "audit":
        for role in ("skeptic", "security", "performance", "accessibility"):
            if not load_role_result(ctx, role):
                pending.append(f"{role} result")

    # -- review phase: implementer + skeptic must be done -------------------
    elif phase == "review":
        if not load_role_result(ctx, "implementer"):
            pending.append("implementer result")
        if not load_role_result(ctx, "skeptic"):
            pending.append("skeptic result")

    # -- security phase: security result pending ---------------------------
    elif phase == "security":
        if not load_role_result(ctx, "security"):
            pending.append("security result")

    # -- performance phase: performance result pending ----------------------
    elif phase == "performance":
        if not load_role_result(ctx, "performance"):
            pending.append("performance result")

    # -- accessibility phase: accessibility result pending ------------------
    elif phase == "accessibility":
        if not load_role_result(ctx, "accessibility"):
            pending.append("accessibility result")

    # -- verify phase: prior results + verifier must be done ---------------
    elif phase == "verify":
        if not load_role_result(ctx, "implementer"):
            pending.append("implementer result")
        if not load_role_result(ctx, "skeptic"):
            pending.append("skeptic result")
        if not load_role_result(ctx, "verifier"):
            pending.append("verifier result")

    # -- docs phase: docs result pending -----------------------------------
    elif phase == "docs":
        if not load_role_result(ctx, "docs"):
            pending.append("docs result")

    if pending:
        print_json(stop_block(
            f"[COLLAB] Mission {ctx.mission_id} is still active in "
            f"phase '{phase}'. Pending: {', '.join(pending)}. "
            f"If you need to pause for the user, set awaiting_user=true "
            f"with collabctl. If the mission is done, close it with "
            f"collabctl close pass|abort."
        ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
