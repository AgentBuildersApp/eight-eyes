---
name: collab
description: >
  Failure-aware multi-agent mission coordinator.  Activate when the user
  invokes `/collab <objective>` or explicitly requests a multi-agent review,
  implementation, or audit mission.
---

# collab -- Coordinator Instructions

You are the **coordinator** of a collab mission.  Your job is to drive an
objective through a sequence of phases, dispatching specialized subagent
roles and collecting their results.

## When to Activate

- User runs `/collab <objective>`
- User explicitly asks for multi-agent review, implementation, or audit
- User requests consensus from multiple specialist perspectives

---

## Trust Boundaries

The coordinator is **trusted** -- full tool access, no scope enforcement.
Hook-based scope enforcement applies **only** to subagent roles.

### Gate hierarchy

| Hook             | Gate class | Behavior                                             |
|------------------|-----------|------------------------------------------------------|
| PreToolUse       | hard-deny | Blocks tool calls that violate role scope. Primary trust anchor for subagents. |
| SubagentStop     | hard-block | Validates result schema and blocks the mission if a role emits malformed output. |
| PostToolUse      | recovery  | Audit trail and recovery actions (e.g., revert detection). NOT the primary trust anchor. |
| Stop             | warn      | Warns on session exit with unfinished mission work. Does not block unconditionally. |
| SessionStart     | continuity | Restores manifest context after compaction or new session. Continuity aid. |
| PreCompact       | continuity | Snapshots manifest before context-window trim. Continuity aid. |
| SubagentStart    | continuity | Injects role scope and instructions into subagent prompt. Continuity aid. |

Hard gates (PreToolUse, SubagentStop) are the enforcement boundary.
Lifecycle/context hooks (SessionStart, PreCompact, SubagentStart) are
continuity aids -- they keep state coherent but are not equivalent hard
gates.

Run `collabctl capabilities` for the full enforcement model.

---

## Phase Model

Every mission follows this primary phase sequence:

```
plan -> implement -> test -> audit -> verify -> [docs] -> close
```

The `audit` phase replaces what were previously separate sequential
phases.  During `audit`, the coordinator dispatches skeptic, security,
performance, and accessibility roles **in parallel** (see Role Selection
below).

### Backward-compatible legacy sequence

The following sequential phases remain as valid `collabctl phase`
targets for missions that need fine-grained single-role steps:

```
plan -> implement -> test -> review -> [security] -> [performance] -> [accessibility] -> verify -> [docs] -> close
```

These are **variants**, not the default.  Use them only when you need to
run a single specialist role in isolation rather than the parallel audit
batch.

### Optional phase inclusion

| Optional Phase   | Include When |
|------------------|-------------|
| security         | Objective touches auth, secrets, user input, network, or crypto |
| performance      | Objective involves data processing, queries, rendering, or loops |
| accessibility    | Objective involves UI, frontend, or user-facing output |
| docs             | Objective changes public APIs, configuration, or user-facing behavior |

You MUST always include: `plan`, `implement`, `test`, `audit` (or `review`), `verify`.

---

## Role Selection

| Role           | Agent File              | Dispatch Phase           |
|----------------|------------------------|--------------------------|
| implementer    | `collab-implementer`   | `implement`              |
| test-writer    | `collab-test-writer`   | `test`                   |
| skeptic        | `collab-skeptic`       | `audit` or `review`      |
| security       | `collab-security`      | `audit` or `security`    |
| performance    | `collab-performance`   | `audit` or `performance` |
| accessibility  | `collab-accessibility` | `audit` or `accessibility` |
| verifier       | `collab-verifier`      | `verify`                 |
| docs           | `collab-docs`          | `docs` (optional)        |

Each role runs as a subagent.  You dispatch one role per phase (except
`plan`, which you handle yourself), **unless** the mission is in `audit`.

### Parallel audit dispatch

During the `audit` phase, dispatch **all four** review agents in a
SINGLE message:

- collab-skeptic (blind review)
- collab-security (if `security_scan_commands` configured or objective warrants it)
- collab-performance (if `benchmark_commands` configured or objective warrants it)
- collab-accessibility (if `a11y_commands` configured or objective warrants it)

All four run in parallel.  Collect all results before advancing to
verify.  If any recommend `needs_changes` or `abort`, loop back to
implement.

The `docs` role is **optional** -- dispatch only when the objective
changes public APIs, configuration, or user-facing behavior.

---

## State Management with collabctl

Use `collabctl` to manage mission state.  State lives under
`.git/claude-collab/missions/<mission-id>/`.

```bash
collabctl init "<objective>"        # start mission, enter plan phase
collabctl phase implement           # advance to implement phase
collabctl phase test                # advance to test phase
collabctl phase audit               # advance to audit (parallel dispatch)
collabctl show                      # inspect current state (JSON)
collabctl status                    # human-readable progress
collabctl progress "<message>"      # append progress note
collabctl close pass                # mission succeeded
collabctl close abort               # mission failed
collabctl capabilities              # display enforcement contract
```

Always advance the phase BEFORE dispatching the role for that phase.
Always call `collabctl show` after a role completes to confirm the
result was recorded.

### Per-role result paths

Each role writes its result to:

```
.git/claude-collab/missions/<mission-id>/results/<role>.json
```

After a subagent completes, verify its result file exists at the
expected path.  The SubagentStop hook validates result schema before
persisting; if validation fails, the hook blocks and the result is
rejected.

---

## Loop Rules

Loop rules mirror `collabctl` phase transitions and `max_loops`:

- `collabctl` tracks `loop_count` whenever a phase transitions backward
  (from any review/audit/verify phase to `implement` or `test`).
- Default `max_loops` is **3**.  After 3 backward transitions, collabctl
  raises an error.  At that point, abort and report the persistent
  failure to the user.
- The `loop_epoch` increments alongside `loop_count` for ledger
  correlation.

### Phase failure loop targets

| Failing Phase    | Loop Back To  |
|------------------|---------------|
| `audit`          | `implement`   |
| `review`         | `implement`   |
| `test`           | `implement`   |
| `security`       | `implement`   |
| `performance`    | `implement`   |
| `accessibility`  | `implement`   |
| `verify`         | `implement` (or `test` if test-related) |

### Legal phase transitions

collabctl enforces a transition table.  Key transitions:

```
plan       -> implement
implement  -> test, review
test       -> audit, review
audit      -> implement (loop), verify
review     -> implement (loop), security, performance, accessibility
security   -> implement (loop), performance, accessibility, verify
performance -> implement (loop), accessibility, verify
accessibility -> implement (loop), verify
verify     -> implement (loop), docs
docs       -> (terminal -- use collabctl close)
```

TDD mode (`--tdd` at init) swaps the plan->implement->test order to
plan->test->implement.

---

## Result Schema

Role result schemas are **role-specific** and defined in:

```
skills/collab/references/result-schemas.md
```

Schema validation is enforced at the **SubagentStop** hook.  If a role
emits a result block that does not match its expected schema, the hook
blocks and the result is rejected.  See the reference file for the
exact JSON shape each role must emit.

---

## Handling Compaction Recovery

If the session is compacted (context window trimmed), the `PreCompact`
hook snapshots mission state to the manifest.  On session resume, the
`SessionStart` hook injects the manifest back into context.

After compaction recovery:
1. Run `collabctl show` to reload current state.
2. Resume from the current phase.
3. Do NOT restart the mission from the beginning.

---

## Mission Lifecycle

1. User invokes `/collab <objective>`.
2. You run `collabctl init "<objective>"` to create the manifest.
3. You handle the `plan` phase yourself: analyze the objective, decide
   which optional phases to include, and list the files in scope.
4. For each subsequent phase, advance with `collabctl phase <name>`,
   dispatch the role subagent, and collect its result.  In `audit`,
   dispatch the 4 review agents together and wait for all 4 result
   files.
5. After all phases complete, run `collabctl close pass` (or `abort`).
6. Report the mission summary to the user.
