# Hook Failure Policy

Canonical reference for the failure mode of every hook in eight-eyes. Each hook
has a documented failure classification, behavior, and rationale grounded in
safety-critical systems engineering.

---

## Failure Classifications

| Classification | Behavior on Crash | When to Use |
|---------------|------------------|-------------|
| **FAIL-CLOSED** | Deny the operation. Retry up to 2 times with backoff. After 3 failures, trip circuit breaker and set awaiting_user. | Hook crash creates a path that bypasses a security or integrity control. |
| **FAIL-OPEN** | Log the error and allow the operation to proceed. Record hook_error in ledger. | Hook crash degrades quality or observability but does not create a security bypass. |
| **FAIL-SAFE** | Allow the operation (session exit). Write crash warning for next session. Record in circuit breaker for observability. | Blocking the operation (trapping the user) is a worse failure mode than allowing it. |

---

## Per-Hook Policy

### collab_session_start.py

| Field | Value |
|-------|-------|
| Event | SessionStart |
| Classification | FAIL-OPEN |
| Behavior | Log error, return 0. Session starts without mission context injection. |
| Consequence of crash | Session operates without mission summary in context. Quality degrades. |
| Security impact | None. Missing context does not grant additional access. |
| Rationale | Context injection is advisory. The model operates with less information, not more privilege. Kubernetes readiness principle: mark as not-ready but do not kill. |

### collab_pre_tool.py

| Field | Value |
|-------|-------|
| Event | PreToolUse |
| Classification | FAIL-CLOSED (when manifest.fail_closed=true) |
| Behavior | Retry up to 2x. On exhaustion, deny tool call and trip circuit breaker. |
| Consequence of crash (fail-open) | Model gains unrestricted tool access. Scope enforcement is silently bypassed. |
| Consequence of crash (fail-closed) | Tool call is denied. Model cannot proceed until hook recovers or user intervenes. |
| Security impact | HIGH. This hook is the primary enforcement layer for role-scoped tool access. |
| Rationale | Trail of Bits principle: the safe failure mode for a security control is denial. If the scope enforcer crashes, the model must not gain unrestricted access. The error path is what an attacker (or a drifting model) would exploit. |
| Backward compat | When fail_closed=false (default), existing fail-open behavior is unchanged. |

### collab_post_tool.py

| Field | Value |
|-------|-------|
| Event | PostToolUse |
| Classification | FAIL-OPEN |
| Behavior | Log error, return 0. Tool call result is not recorded in ledger. |
| Consequence of crash | Missing ledger entry. Compensating revert may not execute for read-only role violations. |
| Security impact | Low. The pre-tool hook already denied unauthorized calls. Post-tool is a second layer. |
| Rationale | Google SRE error budget principle. Evidence recording is valuable but its loss does not create a bypass. The pre-tool layer is the primary defense. |

### collab_subagent_start.py

| Field | Value |
|-------|-------|
| Event | SubagentStart |
| Classification | FAIL-OPEN |
| Behavior | Log error, return 0. Subagent starts without role-specific context. |
| Consequence of crash | Subagent operates without mission context, blind-review barriers, or REVIEW.md criteria. |
| Security impact | Low. Missing context means less guidance, not more access. The pre-tool hook still enforces scope. |
| Rationale | Aircraft degradation principle. The system degrades from full-context to no-context gracefully. The subagent may produce lower-quality results but cannot exceed its tool permissions. |

### collab_subagent_stop.py

| Field | Value |
|-------|-------|
| Event | SubagentStop |
| Classification | FAIL-CLOSED (when manifest.fail_closed=true) |
| Behavior | Retry up to 2x. On exhaustion, block role completion and trip circuit breaker. |
| Consequence of crash (fail-open) | Role "completes" with no validated result. Unvalidated data enters the mission state. |
| Consequence of crash (fail-closed) | Role cannot finish. Subagent must retry producing a valid result. |
| Security impact | MEDIUM-HIGH. The result validation contract is what ensures each role actually did its job. Without validation, a model can produce a fabricated "approve" result. |
| Rationale | NIST 800-53 SI-17 fail-safe procedures. An unvalidated result is an unknown. The system must not proceed with unknown inputs to its trust decisions. |
| Backward compat | When fail_closed=false (default), existing fail-open behavior is unchanged. |

### collab_pre_compact.py

| Field | Value |
|-------|-------|
| Event | PreCompact |
| Classification | FAIL-OPEN |
| Behavior | Log error, return 0. Snapshot is not captured. |
| Consequence of crash | Missing state snapshot before context compaction. Recovery point is lost. |
| Security impact | None. Snapshots are a convenience for crash recovery. |
| Rationale | This hook runs async. Its failure is invisible to the user and does not affect the active session. SRE error budget: acceptable loss. |

### collab_stop.py

| Field | Value |
|-------|-------|
| Event | Stop |
| Classification | FAIL-SAFE |
| Behavior | Log error, write .hook_crash_warning.json, allow session exit. |
| Consequence of crash | Session exits without checking for missing results. Required audit results may be absent. |
| Security impact | Low-Medium. The mission state persists on disk. The next session will detect the incomplete state via session_start. |
| Rationale | Aircraft flight control principle: never trap the pilot. The stop hook is advisory -- it reminds the user that work is pending. Trapping the user in a session they cannot exit is a strictly worse outcome than allowing exit with a warning. The mission's on-disk state is the durable record, not the session. |

---

## Circuit Breaker Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| max_failures | 3 | Consecutive failures before circuit trips |
| reset_timeout_seconds | 300 | Time before half-open probe (5 minutes) |
| retry_delays | [0.1, 0.5] | Seconds to wait between retries (2 retries) |

Circuit breaker state is persisted per-hook in `<mission_dir>/.circuit_breakers/<hook_name>.json`.

---

## Decision Tree for New Hooks

When adding a new hook to eight-eyes, use this decision tree to classify it:

```
Does the hook enforce a security or integrity control?
  YES -> Does its crash create a bypass of that control?
    YES -> FAIL-CLOSED (with retry + circuit breaker)
    NO  -> FAIL-OPEN (defense-in-depth, another layer catches it)
  NO  -> Does blocking the operation trap the user?
    YES -> FAIL-SAFE (allow + warn)
    NO  -> FAIL-OPEN
```
