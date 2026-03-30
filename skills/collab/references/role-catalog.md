# Role Catalog

Complete reference for all 8 collab roles.

---

## implementer

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-implementer.md` |
| Failure surface | Incorrect implementation, missed requirements |
| Permissions | Write to `allowed_paths` only, NO Bash |
| Isolation | worktree |
| Dispatched in phase | `implement` |

**Scope rules**: The `PreToolUse` hook checks every `Write`, `Edit`, and
`MultiEdit` call against `manifest.allowed_paths`.  Calls targeting paths
outside that list are blocked.  All `Bash` calls are blocked regardless of
arguments.

**Result schema**: Standard result object.  `files_touched` must list every
file the implementer created or modified.  `status` is `pass` if the
implementation is complete, `fail` if it could not be completed.
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: Always.  Every mission has an `implement` phase.

---

## test-writer

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-test-writer.md` |
| Failure surface | Untested code paths, missing edge cases |
| Permissions | Write to `test_paths` only, NO Bash |
| Isolation | worktree |
| Dispatched in phase | `test` |

**Scope rules**: The `PreToolUse` hook checks every `Write`, `Edit`, and
`MultiEdit` call against `manifest.test_paths`.  Calls targeting paths
outside that list are blocked.  All `Bash` calls are blocked.

**Result schema**: Standard result object.  `findings` should list the test
cases written.  `files_touched` lists the test files created or modified.
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: Always.  Every mission has a `test` phase.

---

## skeptic

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-skeptic.md` |
| Failure surface | "Looks good to me" review bias, hidden coupling |
| Permissions | Read-only, Bash (read-only cmds only) |
| Isolation | none |
| Dispatched in phase | `review` |

**Scope rules**: All `Write`, `Edit`, and `MultiEdit` calls are blocked.
`Bash` calls are allowed only if the command is read-only (e.g., `grep`,
`cat`, `git log`, `git diff`, `find`, `wc`).  Commands that modify state
(`rm`, `mv`, `git commit`, `npm install`, etc.) are blocked.  As a safety net, any writes that bypass the pre-check are automatically
reverted.

**Blind review**: The skeptic does NOT receive the implementer's diff or
commit message in its initial context.  It reads the codebase independently
and reports issues.  This prevents anchoring bias.

**Result schema**: Standard result object.  `findings` lists issues found.
`status` is `pass` if no blocking issues, `fail` if blocking issues exist.
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: Always.  Every mission has a `review` phase.

---

## security

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-security.md` |
| Failure surface | Auth bypass, injection, secrets exposure |
| Permissions | Read-only, Bash (read-only + scan cmds) |
| Isolation | none |
| Dispatched in phase | `security` |

**Scope rules**: All `Write`, `Edit`, and `MultiEdit` calls are blocked.
`Bash` calls are allowed for read-only commands plus commands listed in
`manifest.security_scan_commands` (e.g., `semgrep`, `trufflehog`,
`gitleaks`, `bandit`).  Any writes that bypass the pre-check are automatically reverted.

**Result schema**: Standard result object.  `findings` lists
vulnerabilities.  Each finding should include severity (critical, high,
medium, low).
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: When the objective touches authentication,
authorization, user input handling, secrets management, cryptography, or
network communication.

---

## performance

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-performance.md` |
| Failure surface | O(n^2) algorithms, N+1 queries, memory leaks |
| Permissions | Read-only, Bash (read-only + benchmark cmds) |
| Isolation | worktree |
| Dispatched in phase | `performance` |

**Scope rules**: All `Write`, `Edit`, and `MultiEdit` calls are blocked.
`Bash` calls are allowed for read-only commands plus commands listed in
`manifest.benchmark_commands` (e.g., `hyperfine`, `time`, `node --prof`,
`py-spy`).  Any writes that bypass the pre-check are automatically reverted.

**Result schema**: Standard result object.  `findings` lists performance
concerns with complexity analysis where applicable.
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: When the objective involves data processing, database
queries, rendering, loops over collections, or any operation where
performance matters.

---

## accessibility

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-accessibility.md` |
| Failure surface | Missing ARIA, keyboard nav, contrast failures |
| Permissions | Read-only, Bash (read-only + a11y cmds) |
| Isolation | none |
| Dispatched in phase | `accessibility` |

**Scope rules**: All `Write`, `Edit`, and `MultiEdit` calls are blocked.
`Bash` calls are allowed for read-only commands plus commands listed in
`manifest.a11y_commands` (e.g., `axe`, `pa11y`, `lighthouse`).  Any writes that bypass the pre-check are automatically reverted.

**Result schema**: Standard result object.  `findings` lists accessibility
violations with WCAG criteria references.
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: When the objective involves UI, frontend components,
HTML output, or any user-facing interface.

---

## docs

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-docs.md` |
| Failure surface | Undocumented APIs, stale docs |
| Permissions | Write to `doc_paths` only, NO Bash |
| Isolation | worktree |
| Dispatched in phase | `docs` |

**Scope rules**: The `PreToolUse` hook checks every `Write`, `Edit`, and
`MultiEdit` call against `manifest.doc_paths`.  Calls targeting paths
outside that list are blocked.  All `Bash` calls are blocked.

**Result schema**: Standard result object.  `files_touched` lists the
documentation files created or modified.
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: When the objective changes public APIs, configuration
options, CLI commands, or any user-facing behavior that should be documented.

---

## verifier

| Attribute | Value |
|-----------|-------|
| Agent file | `agents/collab-verifier.md` |
| Failure surface | "Works on my machine", weak evidence |
| Permissions | Read-only, Bash (read-only + verify cmds) |
| Isolation | worktree |
| Dispatched in phase | `verify` |

**Scope rules**: All `Write`, `Edit`, and `MultiEdit` calls are blocked.
`Bash` calls are allowed for read-only commands plus verification commands
(e.g., `npm test`, `pytest`, `cargo test`, `make test`).  The specific
allowed commands are determined by the project type.

**Result schema**: Standard result object.  `findings` lists verification
results.  `status` is `pass` only if all tests pass and the implementation
meets the stated objective.
See `references/result-schemas.md` for the exact field requirements validated by the SubagentStop hook.

**When to invoke**: Always.  Every mission has a `verify` phase.

---

## Result Schema Reference

See `references/result-schemas.md` for the exact JSON schemas validated by the SubagentStop hook.
