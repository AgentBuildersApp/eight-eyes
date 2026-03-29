# Smoke Test Checklist

Manual end-to-end test for validating the collab plugin on a real Claude
Code session.  Run through each step in order.  Every step must pass.

---

## Prerequisites

- [ ] Claude Code CLI installed and working
- [ ] Python 3.10+ available as `python3` (or `python` on Windows)
- [ ] Git repository initialized in the test directory
- [ ] Plugin installed: `claude plugin add ./collab`
- [ ] Plugin visible in `claude plugin list`

---

## 1. Mission Initialization

- [ ] Run `/collab Add input validation to the signup form`
- [ ] Verify `collabctl init` created `.git/claude-collab/manifest.json`
- [ ] Verify `manifest.json` has `schema_version: 2`
- [ ] Verify `ledger.ndjson` has a `mission_created` entry
- [ ] Verify phase is `plan`

## 2. Plan Phase (Coordinator)

- [ ] Coordinator analyzes the objective and selects roles
- [ ] Coordinator sets `allowed_paths`, `test_paths`, `doc_paths` in manifest
- [ ] Coordinator advances to `implement` via `collabctl phase implement`
- [ ] Verify `phase_history` in manifest has a `plan` entry with result `pass`

## 3. Implementer Role

- [ ] `SubagentStart` hook fires for `collab-implementer`
- [ ] Worktree created under `.git/claude-collab/worktrees/`
- [ ] Implementer can write to files matching `allowed_paths`
- [ ] Implementer CANNOT write to files outside `allowed_paths` (hook blocks)
- [ ] Implementer CANNOT use Bash (hook blocks)
- [ ] `SubagentStop` hook fires, result written to `results/implementer.json`
- [ ] Worktree cleaned up after completion
- [ ] Ledger has `tool_use` and `role_complete` entries

## 4. Test-Writer Role

- [ ] Phase advanced to `test` via `collabctl phase test`
- [ ] `SubagentStart` hook fires for `collab-test-writer`
- [ ] Worktree created for test-writer
- [ ] Test-writer can write to files matching `test_paths`
- [ ] Test-writer CANNOT write to files outside `test_paths` (hook blocks)
- [ ] Test-writer CANNOT use Bash (hook blocks)
- [ ] Result written to `results/test-writer.json`
- [ ] Worktree cleaned up

## 5. Skeptic Role (Blind Review)

- [ ] Phase advanced to `review` via `collabctl phase review`
- [ ] `SubagentStart` hook fires for `collab-skeptic`
- [ ] Skeptic does NOT receive implementer diffs in its initial context
- [ ] Skeptic can use Bash with read-only commands (`grep`, `cat`, `git log`)
- [ ] Skeptic CANNOT use Bash with write commands (`rm`, `mv`, `git commit`)
- [ ] Skeptic CANNOT use Write/Edit/MultiEdit (hook blocks)
- [ ] Result written to `results/skeptic.json`
- [ ] Skeptic findings do NOT reference specific implementer commit messages

## 6. Security Role (Optional)

- [ ] Phase advanced to `security` via `collabctl phase security`
- [ ] Security role can use Bash with read-only commands
- [ ] Security role can use commands from `security_scan_commands`
- [ ] Security role CANNOT use Write/Edit/MultiEdit (hook blocks)
- [ ] Result written to `results/security.json` with severity per finding

## 7. Performance Role (Optional)

- [ ] Phase advanced to `performance` via `collabctl phase performance`
- [ ] Worktree created for performance role
- [ ] Performance role can use Bash with read-only commands
- [ ] Performance role can use commands from `benchmark_commands`
- [ ] Performance role CANNOT use Write/Edit/MultiEdit (hook blocks)
- [ ] Result written to `results/performance.json`
- [ ] Worktree cleaned up

## 8. Accessibility Role (Optional)

- [ ] Phase advanced to `accessibility` via `collabctl phase accessibility`
- [ ] Accessibility role can use Bash with read-only commands
- [ ] Accessibility role can use commands from `a11y_commands`
- [ ] Accessibility role CANNOT use Write/Edit/MultiEdit (hook blocks)
- [ ] Result written to `results/accessibility.json` with WCAG references

## 9. Verifier Role

- [ ] Phase advanced to `verify` via `collabctl phase verify`
- [ ] Worktree created for verifier
- [ ] Verifier can use Bash with test runner commands
- [ ] Verifier CANNOT use Write/Edit/MultiEdit (hook blocks)
- [ ] Verifier runs actual tests and reports pass/fail evidence
- [ ] Result written to `results/verifier.json`
- [ ] Worktree cleaned up

## 10. Docs Role (Optional)

- [ ] Phase advanced to `docs` via `collabctl phase docs`
- [ ] Worktree created for docs role
- [ ] Docs role can write to files matching `doc_paths`
- [ ] Docs role CANNOT write outside `doc_paths` (hook blocks)
- [ ] Docs role CANNOT use Bash (hook blocks)
- [ ] Result written to `results/docs.json`
- [ ] Worktree cleaned up

## 11. Mission Close

- [ ] `collabctl close pass` sets `status` to `passed` in manifest
- [ ] Ledger has `mission_closed` entry
- [ ] All worktrees removed
- [ ] Coordinator reports summary to user

---

## Failure Scenarios

### 12. Loop-Back on Review Failure

- [ ] Configure skeptic to report `status: fail`
- [ ] Coordinator loops back to `implement` phase
- [ ] `loop_count.review` incremented in manifest
- [ ] After 3 loops, coordinator aborts with `collabctl close abort`

### 13. Compaction Recovery

- [ ] Start a mission and reach `review` phase
- [ ] Trigger context compaction
- [ ] Verify `PreCompact` hook wrote snapshot to manifest
- [ ] Start new session
- [ ] Verify `SessionStart` hook injected manifest state
- [ ] Run `collabctl show` -- phase should be `review`
- [ ] Resume mission from `review` (not from beginning)

### 14. Permission Enforcement

- [ ] While implementer is active, attempt to write to a path NOT in `allowed_paths`
- [ ] Verify hook returns `{"decision": "block"}`
- [ ] Verify the write did NOT occur
- [ ] Check ledger for blocked event (if logged)

### 15. Cross-Platform (Windows)

- [ ] Run on Windows with `python` (not `python3`)
- [ ] Verify file locking uses `msvcrt` without errors
- [ ] Verify all paths use forward slashes in manifest
- [ ] Verify worktree creation and cleanup works on NTFS

---

## Result

- [ ] All 15 sections pass: **SMOKE TEST PASSED**
- [ ] Any section fails: document the failure, file an issue
