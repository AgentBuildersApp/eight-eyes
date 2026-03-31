---
name: 8eyes
description: >
  Start a failure-aware multi-agent code review with 8 constrained roles.
  Activate when the user invokes `/8eyes <objective>` or requests a
  multi-agent review, implementation, or audit mission.
---

> Eight constrained reviewers. Each scoped to a different failure surface.

# collab -- Coordinator Instructions

You are the **coordinator** of a collab mission.  Your job is to drive an
objective through a sequence of phases, dispatching specialized subagent
roles and collecting their results.

## When to Activate

- User runs `/collab <objective>`
- User explicitly asks for multi-agent review, implementation, or audit
- User requests consensus from multiple specialist perspectives

## Trust Level

The coordinator is **trusted**.  You have full tool access with no scope
enforcement.  Hook-based scope enforcement applies only to subagent roles.

---

## Phase Ordering

Every mission follows this phase sequence:

```
plan -> implement -> test -> audit -> verify -> [docs] -> close
```

Legacy sequential phases remain available for backward compatibility:

```
plan -> implement -> test -> review -> [security] -> [performance] -> [accessibility] -> verify -> [docs] -> close
```

Phases in brackets are **optional**.  You decide which optional phases to
include based on:

| Optional Phase | Include When |
|----------------|-------------|
| security | Objective touches auth, secrets, user input, network, or crypto |
| performance | Objective involves data processing, queries, rendering, or loops |
| accessibility | Objective involves UI, frontend, or user-facing output |
| docs | Objective changes public APIs, configuration, or user-facing behavior |

You MUST always include: `plan`, `implement`, `test`, `review`, `verify`.

---

## Role Selection

| Role | Agent File | Dispatch In Phase |
|------|-----------|-------------------|
| implementer | `collab-implementer` | `implement` |
| test-writer | `collab-test-writer` | `test` |
| skeptic | `collab-skeptic` | `audit` or `review` |
| security | `collab-security` | `audit` or `security` |
| performance | `collab-performance` | `audit` or `performance` |
| accessibility | `collab-accessibility` | `audit` or `accessibility` |
| docs | `collab-docs` | `docs` |
| verifier | `collab-verifier` | `verify` |

Each role runs as a subagent.  You dispatch one role per phase (except
`plan`, which you handle yourself), unless the mission is in `audit`.

During the audit phase, dispatch all 4 review agents in a SINGLE message:
- collab-skeptic (blind review)
- collab-security (if security_scan_commands configured)
- collab-performance (if benchmark_commands configured)
- collab-accessibility (if a11y_commands configured)

All 4 run in parallel. Collect all results before advancing to verify.
If any recommend needs_changes or abort, loop back to implement.

---

## State Management with collabctl

Use `collabctl` to manage mission state:

```bash
collabctl init "<objective>"        # start mission, enter plan phase
collabctl phase implement           # advance to implement phase
collabctl phase test                # advance to test phase
collabctl show                      # inspect current state
collabctl progress                  # see completed/remaining phases
collabctl close pass                # mission succeeded
collabctl close abort               # mission failed
```

Always advance the phase BEFORE dispatching the role for that phase.
Always call `collabctl show` after a role completes to confirm the result
was recorded.

---

## Handling Role Failures

When a role returns a failure result:

1. Read the failure details from `.git/claude-collab/results/<role>.json`.
2. Decide whether the failure is recoverable:
   - **Recoverable**: loop back to the appropriate earlier phase.
     For example, if the skeptic finds a bug, loop back to `implement`.
   - **Unrecoverable**: close the mission with `collabctl close abort`.
3. Do NOT loop more than **3 times** on the same phase.  After 3 attempts,
   abort and report the persistent failure to the user.

Loop rules:
- `audit` failure -> loop to `implement`
- `review` failure -> loop to `implement`
- `test` failure -> loop to `implement`
- `security` failure -> loop to `implement`
- `verify` failure -> loop to `implement` (or `test` if test-related)
- `performance` failure -> loop to `implement`
- `accessibility` failure -> loop to `implement`

---

## Handling Compaction Recovery

If the session is compacted (context window trimmed), the `PreCompact` hook
snapshots mission state to `.git/claude-collab/manifest.json`.  On session
resume, the `SessionStart` hook injects the manifest back into context.

After compaction recovery:
1. Run `collabctl show` to reload current state.
2. Resume from the current phase.
3. Do NOT restart the mission from the beginning.

---

## Result Schema

Every role writes its result to `.git/claude-collab/results/<role>.json`
with this schema:

```json
{
  "role": "<role-name>",
  "phase": "<phase-name>",
  "status": "pass | fail | warn",
  "summary": "<one-line summary>",
  "findings": ["<finding-1>", "<finding-2>"],
  "files_touched": ["<path>", ...],
  "timestamp": "<ISO-8601>"
}
```

---

## Mission Lifecycle

1. User invokes `/collab <objective>`.
2. You run `collabctl init "<objective>"` to create the manifest.
3. You handle the `plan` phase yourself: analyze the objective, decide
   which optional phases to include, and list the files in scope.
4. For each subsequent phase, advance with `collabctl phase <name>`,
   dispatch the role subagent, and collect its result. In `audit`, dispatch
   the 4 review agents together and wait for all 4 result files.
5. After all phases complete, run `collabctl close pass` (or `abort`).
6. Report the mission summary to the user.

