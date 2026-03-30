# Trust Model

eight-eyes enforces review integrity through 10 layers of defense. Each layer
is implemented by a different mechanism -- hooks, state files, schema validation,
or CLI guards -- so that a failure in one layer does not compromise the others.

---

## The 10 Layers

### Layer 1: Isolated Context

Each subagent receives only role-specific context at startup. The skeptic sees
the objective, acceptance criteria, scope rules, and changed paths -- but never
the implementer's narrative or summary. This is not a prompt instruction; it is
a structural constraint enforced by the `SubagentStart` hook which shapes the
context window before the model runs.

**Enforcement**: `collab_subagent_start.py` builds per-role context strings.
The skeptic's `build_subagent_context()` call omits implementer claims.

**What it prevents**: Anchoring bias. When a reviewer sees the author's
explanation first, they tend to evaluate the explanation rather than the code.
Isolated context forces independent evaluation.

### Layer 2: Scoped Tools

The `PreToolUse` hook intercepts every tool call before execution and checks
whether the calling role is permitted to use that tool with those arguments.
Write roles (implementer, test-writer, docs) can only write to their designated
path sets. Read-only roles (skeptic, security, performance, accessibility,
verifier) cannot use Write, Edit, or MultiEdit at all.

**Enforcement**: `collab_pre_tool.py` matches role + tool + path against the
manifest's scope rules. Denied calls never reach the tool.

**What it prevents**: Scope drift. A model deciding to "fix" an issue it found
during review, or an implementer editing files outside the approved change set.

### Layer 3: Approved Command Allowlists

Bash commands for read-only roles are matched against explicit allowlists. The
baseline set includes read-only inspection tools (git diff, grep, cat, ls, find
without -exec). Role-specific commands (security scans, benchmarks, a11y audits,
verification tests) must be declared at mission init time.

Shell operators -- pipes, redirects, command chains, and command substitution --
cause an immediate denial regardless of the base command. This prevents bypasses
like `cat file.py | python3` or `grep x; rm -rf /`.

**Enforcement**: `command_matches_any()` and `command_is_approved_extra()` in
`roles.py`. The `_has_shell_operator()` check runs before any pattern matching.

**What it prevents**: Command injection. A model constructing a Bash command
that chains an approved read-only tool with a destructive operation.

### Layer 4: Explicit Phase Transitions

The mission lifecycle follows a defined phase graph: plan -> implement -> test ->
audit -> verify -> docs -> close. Transitions outside this graph are rejected by
`collabctl phase`. Forced transitions require `--force` and are logged as
`force_override` events in the ledger.

**Enforcement**: `collabctl.py` phase transition validation with
`PHASE_TRANSITIONS` table. TDD mode changes the graph to enforce test-first.

**What it prevents**: Phase skipping. A model or coordinator jumping from plan
directly to verify, bypassing implementation and audit.

### Layer 5: Append-Only Ledger

Every tool call, phase transition, role dispatch, role completion, hook error,
scope violation revert, and force override is recorded as a JSON line in the
NDJSON ledger. Entries include timestamps, role identity, tool names, file paths,
and outcomes. The ledger is append-only -- there is no API to delete or modify
existing entries.

**Enforcement**: `append_ledger()` in `engine.py` with file-lock protection.
Deduplication by `tool_use_id` prevents double-recording.

**What it prevents**: Evidence tampering. The ledger provides an auditable
record of everything that happened during the mission, independent of what
any individual role claims in its result.

### Layer 6: Stale Mission Warnings

The `SessionStart` hook checks the mission's `created_at` timestamp and warns
when the mission is older than 12 hours. This catches missions that were started
and forgotten, or where context has gone stale.

**Enforcement**: `collab_session_start.py` timestamp comparison. The
`timeout_hours` manifest field (default 24) allows the stop hook to release
a timed-out mission.

**What it prevents**: Stale context drift. A model continuing a mission days
later with outdated understanding of the codebase state.

### Layer 7: Stop-Hook Blocking

The session cannot exit while required role results are missing for the current
phase. During audit phase, all four audit roles must complete before the session
can end. This prevents a coordinator from declaring "done" before the work is
actually done.

**Enforcement**: `collab_stop.py` checks `load_role_result()` for each required
role in the current phase. Missing results trigger `stop_block()`.

**What it prevents**: Premature completion. A coordinator ending the session
without collecting the evidence that the mission's objective requires.

### Layer 8: Result Validation

When a subagent finishes, the `SubagentStop` hook validates the result block
against the role's expected schema. The implementer must provide changed_paths,
artifacts, and tests_run. The verifier must report on every acceptance criterion.
The security role must list scan_commands_run. Invalid results are rejected and
the subagent must retry.

**Enforcement**: `validate_role_result()` in `roles.py` with per-role schema
checks. The `role_failure_counts` manifest field tracks consecutive failures
and escalates to `awaiting_user` after 3.

**What it prevents**: Fabricated or incomplete results. A model producing a
"pass" result without actually checking anything, or omitting required evidence
fields.

### Layer 9: Compensating Reverts

If a read-only role somehow produces a file mutation (possible if a model
finds a prompt injection path around the PreToolUse hook), the `PostToolUse`
hook detects the write and automatically reverts it. Tracked files are restored
via `git checkout --`; untracked files are deleted. The revert is recorded in
the ledger as a `scope_violation_reverted` event.

**Enforcement**: `collab_post_tool.py` checks role type after each
Write/Edit/MultiEdit and runs compensating git operations.

**What it prevents**: Residual mutations by read-only roles. Even if the
pre-tool check is somehow bypassed, the post-tool layer catches and reverts
the damage.

### Layer 10: Fail-Closed Circuit Breaker

When `fail_closed` mode is enabled, security-critical hooks (pre_tool and
subagent_stop) deny on crash rather than allowing. A circuit breaker with
retry logic (2 retries with backoff) prevents transient failures from
triggering denial. After 3 consecutive failures, the circuit trips and the
mission pauses for user intervention.

**Enforcement**: `core/circuit_breaker.py` (planned) with per-hook state
files. The stop hook uses a fail-SAFE variant that allows exit but writes a
crash warning for the next session.

**What it prevents**: Silent bypass of security controls via hook crashes.
Without this layer, a crashing pre_tool hook silently grants unrestricted
tool access. With it, the crash triggers denial.

---

## Defense Depth Summary

| Threat | Layers That Catch It |
|--------|---------------------|
| Model edits files outside scope | L2 (PreToolUse deny) + L9 (compensating revert) |
| Model runs destructive Bash command | L3 (command allowlist) + L2 (shell operator block) |
| Model fabricates review findings | L8 (result validation) + L1 (isolated context) |
| Reviewer anchors on author's narrative | L1 (blind review context shaping) |
| Coordinator skips audit phase | L4 (phase transition rules) + L7 (stop blocking) |
| Hook crash bypasses scope enforcement | L10 (fail-closed circuit breaker) |
| Evidence of violations is erased | L5 (append-only ledger) |
| Mission goes stale without notice | L6 (stale mission warning) |

---

## Failure Mode Classification

Hooks are classified by the consequence of their crash:

| Classification | Meaning | Hooks |
|---------------|---------|-------|
| **FAIL-CLOSED** | Crash creates a security bypass. Deny on failure. | pre_tool, subagent_stop |
| **FAIL-OPEN** | Crash degrades quality or observability. Allow on failure. | session_start, post_tool, subagent_start, pre_compact |
| **FAIL-SAFE** | Crash vs. trapping the user. Allow exit, warn loudly. | stop |

The classification rule: if a hook crash creates a path that bypasses a security
or integrity control, the hook must be fail-closed. If it degrades quality without
creating a bypass, fail-open is acceptable. The stop hook is a special case where
the trap-the-user failure mode is worse than the lose-blocking failure mode.
