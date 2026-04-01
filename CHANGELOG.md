# Changelog

## 5.0.0-alpha

### Enforcement Model

- Machine-readable enforcement contract (`spec/enforcement.yaml`) defines gate classes, failure modes, and platform coverage for every hook.
- `collabctl capabilities` command to inspect the enforcement model. Supports `--json` and `--role <name>`.
- Gate class semantics clearly separate hard gates (PreToolUse, SubagentStop) from recovery hooks, lifecycle hooks, and observability hooks.

### Role Specifications

- Canonical role definitions in `spec/roles/builtin_roles.yaml` for all 8 built-in roles.
- Compiled JSON cache (`roles_compiled.json`) for fast runtime loading.
- Role specs include scope mode, bash policy, blind-from rules, result schemas, phase assignments, and per-platform support.

### Operator Visibility

- `collabctl status --json` for machine-readable mission progress.
- Enhanced `status` output shows planned, completed, pending, and skipped roles with fail-closed state and loop count.

### Custom Roles

- Custom `read_only` roles now receive the same compensating revert as built-in read-only roles.
- Revert events include `revert_mode` (tracked_checkout, untracked_delete) and `revert_success` for audit trails.
- Ledger entries distinguish built-in vs custom role type.

### Adapter Parity

- Parity tests verify installer output matches committed manifests for Copilot CLI and Codex CLI.
- Enforcement contract platform matrix verified against actual adapter hook registrations.
- Codex PostToolUse and SessionStart correctly classified as degraded.

### Coordinator

- `skills/collab/SKILL.md` rewritten with explicit trust boundaries, aligned phase model, and current result schema references.
