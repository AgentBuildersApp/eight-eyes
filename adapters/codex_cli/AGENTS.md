# Eight-Eyes Multi-Agent Code Review

When the user invokes `/8eyes` or requests multi-agent review:

1. Run: `python3 scripts/collabctl.py init --objective "<objective>" --allowed-path src --criterion "<criteria>"`
2. Follow the phase workflow: `plan -> implement -> test -> audit -> verify -> docs -> close`
3. Dispatch `collab-*` agents as subagents for each phase

## Scope Enforcement

Note: On Codex CLI, `PreToolUse` hooks only intercept Bash commands. Write/Edit scope enforcement relies on `SessionStart` context injection. The `PostToolUse` hook audits all tool calls after execution. If a scope violation is detected post-hoc, it will be logged to the violation ledger.

## Roles

- `collab-implementer`: Implements code changes for an active `/collab` mission.
- `collab-test-writer`: Writes tests for code changes in an active `/collab` mission.
- `collab-skeptic`: Performs a blind review of an active `/collab` mission.
- `collab-security`: Audits code changes for security vulnerabilities in an active `/collab` mission.
- `collab-performance`: Profiles code changes for performance issues in an active `/collab` mission.
- `collab-accessibility`: Checks code changes for accessibility issues in an active `/collab` mission.
- `collab-docs`: Updates documentation for verified changes in an active `/collab` mission.
- `collab-verifier`: Verifies an active `/collab` mission against acceptance criteria and approved test commands.
