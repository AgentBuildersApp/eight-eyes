---
name: 8eyes
description: Start a failure-aware multi-agent code review mission
argument-hint: <objective>
disable-model-invocation: false
allowed-tools: [Bash, Agent, Read, Glob, Grep]
---

# 8eyes Orchestrator

The user invoked this command with: `$ARGUMENTS`

This command is an execution workflow, not a prompt suggestion. You MUST
follow the steps below in order. Do not collapse this into a single-agent
review. Do not skip the Agent tool. Do not skip progress reporting.

## Required Workflow

1. FIRST ACTION: run this via the Bash tool:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py init --objective "$ARGUMENTS"`
   If the objective is missing, stop and ask the user for it instead of inventing one.
2. Immediately run:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py status`
   Show the mission ID, objective, current phase, and the planned role order.
3. Present the plan and WAIT for explicit user approval before spawning any subagent.
4. Advance to implement, then spawn a NAMED `collab-implementer` subagent via
   the Agent tool. When it completes:
   - show a phase progress summary
   - show the per-role result block:
     - Role: `implementer`
     - Recommendation
     - Key findings
5. Advance to test, then spawn a NAMED `collab-test-writer` subagent via the
   Agent tool. When it completes:
   - show a phase progress summary
   - show the per-role result block:
     - Role: `test-writer`
     - Recommendation
     - Key findings
6. Advance to audit, then spawn these NAMED subagents in PARALLEL via the Agent tool:
   - `collab-skeptic`
   - `collab-security`
   - `collab-performance`
   - `collab-accessibility`
   Wait for all four results. After EACH role completes, show:
   - role name
   - recommendation
   - key findings
   After all four complete, show a combined audit progress summary.
7. Advance to verify, then spawn a NAMED `collab-verifier` subagent via the
   Agent tool. When it completes:
   - show a phase progress summary
   - show the per-role result block:
     - Role: `verifier`
     - Recommendation
     - Key findings
8. Finish with a final summary that reports findings per role and the overall recommendation.

## Looping Rule

If any role reports `needs_changes`, `fail`, `abort`, or `blocked`, you MUST
loop back to implement instead of ending the review:

1. Show which role requested changes and the key findings that triggered the loop.
2. Re-run implement by spawning a NAMED `collab-implementer` subagent via the Agent tool.
3. Re-run the downstream phases in order:
   - `collab-test-writer`
   - the parallel audit roles
   - `collab-verifier`
4. After each rerun phase, show the same progress summary and per-role findings block.
5. Repeat until every role is green or you must stop and ask the user.

## Status Reporting Requirements

- After EACH phase, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/collabctl.py status`
  and show a progress summary with:
  - current phase
  - completed roles
  - pending roles
- After EACH role completes, show:
  - role name
  - recommendation
  - key findings
- The final response MUST include per-role findings for:
  - `implementer`
  - `test-writer`
  - `skeptic`
  - `security`
  - `performance`
  - `accessibility`
  - `verifier`

