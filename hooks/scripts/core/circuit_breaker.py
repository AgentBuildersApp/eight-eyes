"""Circuit breaker for fail-closed hook crash handling.

Implements a layered state machine based on:
- Erlang/OTP: retry with intensity limits
- Netflix: circuit breaker pattern
- NIST 800-53 SI-17: fail-safe with operator notification
- Aircraft flight control: never trap the operator

State progression: HEALTHY -> RETRY -> CIRCUIT_OPEN -> AWAITING_USER
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Circuit breaker configuration
MAX_RETRIES = 2
RETRY_DELAYS = [0.1, 0.5]  # seconds: 100ms, 500ms


class HookCircuitBreaker:
    """Per-hook circuit breaker with retry and escalation."""

    def __init__(self, hook_name: str, failure_mode: str = "deny"):
        """
        hook_name: identifier for this hook
        failure_mode: what to do when circuit opens
            - "deny": block the action (pre_tool)
            - "block": reject and retry (subagent_stop)
            - "warn": allow but persist warning (stop)
        """
        self.hook_name = hook_name
        self.failure_mode = failure_mode
        self._retry_count = 0

    def execute_with_resilience(
        self,
        main_fn: Callable[[], int],
        ctx_loader: Callable[[], Any],
        on_deny: Optional[Callable[[str], None]] = None,
        on_escalate: Optional[Callable[[Any, str], None]] = None,
    ) -> int:
        """Execute main_fn with retry + circuit breaker + escalation.

        main_fn: the hook's _main() function
        ctx_loader: callable that returns MissionContext (for manifest access)
        on_deny: called when failure_mode="deny" and circuit opens
        on_escalate: called to set awaiting_user with reason

        Returns: exit code (0 = success/allow, nonzero per platform)
        """
        # First attempt
        first_exc: Optional[Exception] = None
        try:
            return main_fn()
        except Exception as exc:
            first_exc = exc

        # Check if fail_closed is enabled
        try:
            ctx = ctx_loader()
            if not ctx or not ctx.manifest.get("fail_closed"):
                # Not fail-closed -- use legacy fail-open behavior
                return self._fail_open_legacy(first_exc, ctx)
        except Exception:
            # Can't even load context -- fail open (can't determine policy)
            self._log_error(first_exc)
            return 0

        # Fail-closed mode: retry with backoff
        last_exc = first_exc
        for i, delay in enumerate(RETRY_DELAYS):
            time.sleep(delay)
            self._retry_count = i + 1
            try:
                result = main_fn()
                # Success on retry -- log the recovery
                self._log_recovery(ctx, i + 1)
                return result
            except Exception as retry_exc:
                last_exc = retry_exc

        # All retries exhausted -- circuit is open
        return self._circuit_open(ctx, last_exc, on_deny, on_escalate)

    def _circuit_open(self, ctx: Any, exc: Exception, on_deny: Any, on_escalate: Any) -> int:
        """Handle circuit-open state based on failure_mode."""
        import sys

        from .engine import append_ledger, atomic_write_json, utc_now

        reason = f"{self.hook_name} crashed after {MAX_RETRIES} retries: {exc}"

        # Log to ledger
        try:
            append_ledger(ctx, {
                "kind": "circuit_open",
                "hook": self.hook_name,
                "error": str(exc)[:200],
                "retries_attempted": self._retry_count,
                "failure_mode": self.failure_mode,
                "tool_use_id": f"circuit:{self.hook_name}:{int(time.time())}",
            })
        except Exception:
            pass

        if self.failure_mode == "deny":
            # pre_tool: block the action
            print(
                f"[collab] FAIL-CLOSED: {self.hook_name} blocking action "
                f"after {MAX_RETRIES} retries",
                file=sys.stderr,
            )
            if on_deny:
                on_deny(reason)
            return 0  # on_deny already printed the deny JSON

        elif self.failure_mode == "block":
            # subagent_stop: reject result, escalate to user
            print(
                f"[collab] FAIL-CLOSED: {self.hook_name} escalating to user "
                f"after {MAX_RETRIES} retries",
                file=sys.stderr,
            )
            if on_escalate:
                on_escalate(ctx, reason)
            else:
                self._default_escalate(ctx, reason)
            return 0

        elif self.failure_mode == "warn":
            # stop: allow exit but persist warning
            print(
                f"[collab] FAIL-CLOSED WARNING: {self.hook_name} crashed, "
                f"allowing exit with warning",
                file=sys.stderr,
            )
            try:
                ctx.manifest.setdefault("crash_warnings", []).append({
                    "hook": self.hook_name,
                    "error": str(exc)[:200],
                    "ts": utc_now(),
                })
                ctx.manifest["updated_at"] = utc_now()
                atomic_write_json(ctx.manifest_path, ctx.manifest)
            except Exception:
                pass
            return 0

        return 0  # Unknown mode -- fail open as absolute last resort

    def _default_escalate(self, ctx: Any, reason: str) -> None:
        """Set awaiting_user with crash reason."""
        from .engine import atomic_write_json, utc_now

        try:
            ctx.manifest["awaiting_user"] = True
            ctx.manifest["awaiting_user_reason"] = reason
            ctx.manifest["updated_at"] = utc_now()
            atomic_write_json(ctx.manifest_path, ctx.manifest)
        except Exception:
            pass

    def _fail_open_legacy(self, exc: Exception, ctx: Any) -> int:
        """Legacy fail-open behavior with ledger write attempt."""
        self._log_error(exc)
        try:
            if ctx:
                from .engine import append_ledger

                append_ledger(ctx, {
                    "kind": "hook_error",
                    "hook": self.hook_name,
                    "error": str(exc)[:200],
                    "tool_use_id": f"error:{self.hook_name}:{int(time.time())}",
                })
        except Exception:
            pass
        return 0

    def _log_error(self, exc: Exception) -> None:
        import sys

        print(f"[collab] {self.hook_name} hook error: {exc}", file=sys.stderr)
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)

    def _log_recovery(self, ctx: Any, retry_num: int) -> None:
        """Log successful retry recovery to ledger."""
        try:
            from .engine import append_ledger

            append_ledger(ctx, {
                "kind": "hook_recovery",
                "hook": self.hook_name,
                "retry_number": retry_num,
                "tool_use_id": f"recovery:{self.hook_name}:{int(time.time())}",
            })
        except Exception:
            pass


__all__ = ["HookCircuitBreaker", "MAX_RETRIES", "RETRY_DELAYS"]
