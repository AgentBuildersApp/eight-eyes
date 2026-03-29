# Changelog

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

### Wave 5: Marketing-Grade Docs and Distribution

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
