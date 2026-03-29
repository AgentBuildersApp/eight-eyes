# Changelog

## 4.0.0 - 2026-03-29

### Observability

- Added `role_dispatched` and `role_completed` lifecycle events to the NDJSON ledger with timing, model identity, and finding counts.
- Added `collabctl timeline` command showing a chronological role-by-role table with phase, model, status, duration, and findings.
- Added `collabctl report` command producing a consolidated mission report with verdict, severity-grouped findings, and hook health.
- Enhanced `collabctl status` to show model, duration, and distinguish "running" from "pending" roles.
- All hook `_fail_open` handlers now attempt a `hook_error` ledger write (double-defense pattern) before failing open.

### Multi-Model Routing

- Added `model_map` manifest field and `--model-map` / `--default-model` CLI flags for per-role model backend selection.
- Model identity is recorded in `role_assignments` and displayed in timeline, report, and status output.

### Security

- Removed generic `awk` from `READ_ONLY_BASH_PATTERNS` and tightened `sed` to print-only commands (`sed -n '<N>p'`, `sed -n '/<pat>/p'`).
- Changed all `except BaseException` to `except Exception` across all 7 hook scripts, preventing `SystemExit(0)` from hitting the error logger.
- Audit roles (skeptic, security, performance, accessibility) are now mandatory before the `verify` phase transition. Use `--skip-role` to explicitly skip with validation.
- Tightened result validation: `severity` and `issue` required per finding for skeptic and reviewer roles; `evidence` required for verifier criteria.
- Added `--force` audit trail logged to both ledger and progress.md.
- `--skip-role` validated against actual audit role names only.
- Unrecognized `collab-*` agent types are now denied (fail-closed) instead of allowed through.
- Added opt-in `--fail-closed` mode for security-critical deployments.

### Defense-in-Depth (Cross-Platform)

- Added PostToolUse compensating revert: read-only roles that write files have those writes automatically reverted via `git checkout` (tracked) or `unlink` (untracked).
- Added close-time scope verification: `collabctl close pass` checks `git diff` against allowed paths and blocks on violations. Override with `--force-close <reason>`.
- Git baseline captured at `collabctl init` for accurate close-time comparison.

### Performance

- Wrapped manifest read-modify-write in `file_lock` in both `collab_subagent_start.py` and `collab_subagent_stop.py` to prevent concurrent update races.

### Accessibility

- Timeline table fits within 80 columns.
- Report findings include severity labels.
- "No active mission" messages now suggest running `collabctl init`.

### Platform Fixes

- Added missing `subagentStart` hook to Copilot CLI adapter.
- Codex CLI positioned as experimental with documented limitations section.

### Schema

- Schema v3 to v4 migration is backward-compatible via additive `setdefault()`. New fields: `model_map`, `phase_started_at`, `role_assignments`, `planned_roles`, `skipped_roles`, `fail_closed`, `git_baseline`.

### Tests

- 98 to 130 tests (32 new tests covering all v4 features).

## 3.0.0 - 2026-03-28

### Wave 0: State-Model Hardening

- Split the hook runtime into `core/engine.py`, `core/roles.py`, `core/paths.py`, and `core/contracts.py`, while keeping `hooks/scripts/collab_common.py` as a compatibility shim.
- Added `loop_epoch` result freshness and the schema migration path to v3.
- Extended wildcard `collab-*` handling so manifest-defined custom roles participate in hook enforcement and result handling.

### Wave 1: Token Efficiency

- Added three-tier subagent context construction plus slim `SessionStart` summaries.
- Added `ledger-summary.json` caching for changed-path lookups.
- Moved role result schemas into a shared reference file and reduced duplicated mission context.

### Wave 2: Parallel Audit, TDD, and Mission Resilience

- Added the recommended `audit` phase with parallel skeptic, security, performance, and accessibility review.
- Added `--tdd`, TDD write gating for the implementer, stale-mission warnings, timeout-aware stop behavior, and `--dry-run`.
- Added role failure tracking and `REVIEW.md` context injection for reviewer roles.

### Wave 3: Custom Roles and /8eyes

- Added `--custom-role` manifest entries, validation, and custom-role context building.
- Added the `/8eyes` command assets and verify checks for the public command surface.

### Wave 4: Multi-Platform Adapters

- Added Copilot CLI and Codex CLI adapters.
- Added the cross-platform `install.py` installer and expanded `collabctl verify` to cover adapters and installer layout.

### Wave 5: Public Documentation and Distribution

- Rewrote the public README around the v3 mission model, platform quickstarts, and hook-enforced architecture.
- Added `CONTRIBUTING.md` and `docs/MIGRATION.md`.
- Added marketplace metadata to the root plugin manifest and CI verification for `collabctl verify`.

## 2.0.0 - 2026-03-28

### Added

- Plugin format (`.claude-plugin/plugin.json`, `hooks/hooks.json`)
- Five new agent roles: test-writer, security, performance, accessibility, and docs
- The `test` phase between implement and review
- Optional phases: security, performance, accessibility, and docs
- Manifest schema v2 with `test_paths`, `doc_paths`, `security_scan_commands`, `benchmark_commands`, and `a11y_commands`
- Cross-platform file locking for Windows (`msvcrt`) and Unix (`fcntl`)
- Full stdlib-only test suite
- CI matrix across Ubuntu, macOS, and Windows on Python 3.10-3.12

### Changed

- Reworked the project from local-only setup into a plugin layout
- Expanded `collabctl phase` to cover the full v2 phase set
- Switched hook commands to `python3` for cross-platform consistency
- Bumped the manifest schema to v2

### Fixed

- Hook commands now resolve through `${CLAUDE_PLUGIN_ROOT}` instead of `$CLAUDE_PROJECT_DIR`

## 1.0.0 - 2026-03-01

### Added

- Initial prototype with three roles: implementer, skeptic, and verifier
- Hook-enforced scope, blind review, and NDJSON ledger state
- `collabctl` mission management CLI
