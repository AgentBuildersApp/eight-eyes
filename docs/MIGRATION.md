# Migration Guide

## v3 -> v4

```bash
python3 scripts/collabctl.py migrate
```

Run the command from the repository that owns the active mission. The migrator upgrades older manifests in place and chains `v1 -> v2 -> v3 -> v4` automatically when needed.

### What Changes

- `schema_version` moves to `4`.
- New manifest fields:
  - `model_map` -- per-role model backend selection.
  - `phase_started_at` -- timestamp of when the current phase began.
  - `role_assignments` -- per-role timing, model, outcome, and finding count.
  - `planned_roles` -- roles planned for this mission at init time.
  - `skipped_roles` -- roles explicitly skipped via `--skip-role`.
  - `fail_closed` -- when true, hooks deny on error instead of allowing.
  - `git_baseline` -- git status captured at init for close-time scope verification.
- New ledger events: `role_dispatched`, `role_completed`, `hook_error`, `scope_violation_reverted`, `force_override`.
- New CLI commands: `collabctl timeline` (role dispatch/completion timing), `collabctl report` (consolidated findings across roles).
- New CLI flags: `--model-map`, `--default-model`, `--fail-closed`, `--skip-role`, `--force-close`.
- Close-time scope verification compares git diff against `allowed_paths` and blocks if out-of-scope files were modified. Use `--force-close` to override.
- `PostToolUse` now performs compensating reverts for read-only role write violations.

### Breaking Changes

- Audit roles (skeptic, security, performance, accessibility) are required before the mission can transition to `verify`. Use `--skip-role <name>` to explicitly bypass a role.
- No other breaking changes. Existing v3 missions migrate cleanly.

---

## v2 -> v3

```bash
python3 scripts/collabctl.py migrate
```

Run the command from the repository that owns the active mission. The migrator upgrades older manifests in place and chains `v1 -> v2 -> v3` automatically when needed.

### What Changes

- `schema_version` moves to `3`.
- New manifest fields: `tdd_mode`, `custom_roles`, `timeout_hours`, `role_failure_counts`, `loop_epoch`, `max_loops`, and `loop_count`.
- Result freshness is epoch-aware, so stale role results from an earlier loop no longer satisfy the current mission state.
- `audit` becomes the recommended composite review phase after `test`, while legacy sequential `review`, `security`, `performance`, and `accessibility` phases remain available.
- `collabctl init` gains `--tdd`, `--custom-role`, `--timeout-hours`, and `--dry-run`.
- Session state adds a slim startup summary, stale-mission warning behavior, and a cached `ledger-summary.json` for changed-path lookups.
- `/8eyes` becomes the public command surface, and v3 ships Copilot CLI and Codex CLI adapters plus the cross-platform `install.py`.

### Breaking Changes

None - v3 is backward compatible with v2 missions.

