# Migration Guide

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
