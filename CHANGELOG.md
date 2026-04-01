# Changelog

## 5.0.0-alpha

**Theme: Verifiable enforcement.** You no longer have to trust the README when it says "hooks enforce scope." Now you can inspect, test, and integrate against a machine-readable enforcement contract.

### Inspect what's enforced

`collabctl capabilities` shows every hook, its gate class, failure mode, and per-platform support. `--json` for CI consumption. `--role <name>` to filter to a single role. The enforcement contract lives in `spec/enforcement.yaml` — an inspectable artifact, not prose.

### Machine-readable mission status

`collabctl status --json` returns structured JSON with planned, completed, pending, and skipped roles plus fail-closed state and loop count. Text output also now categorizes roles by status instead of a flat list.

### Custom roles are first-class

Custom `read_only` roles defined in the manifest now receive the same PostToolUse compensating revert as built-in read-only roles. Revert events include `revert_mode` and `revert_success` for audit trails. Ledger entries distinguish built-in from custom role type.

### Platform coverage is tested

Adapter parity tests verify that installer output matches committed manifests for Copilot CLI and Codex CLI. The enforcement contract's platform matrix is verified against actual adapter hook registrations. If a hook is marked "degraded" for Codex, all surfaces agree.

### Canonical role specifications

All 8 built-in roles are defined in `spec/roles/builtin_roles.yaml` with scope mode, bash policy, blind-from rules, result schemas, phase assignments, and per-platform support. Compiled JSON for fast runtime loading.

### Coordinator alignment

`skills/collab/SKILL.md` rewritten with explicit trust boundaries, aligned phase model (`plan → implement → test → audit → verify → [docs] → close`), and current result schema references.

### No breaking changes

v5 is backward-compatible with v4 missions. The `spec/` directory is additive. Run `collabctl migrate` to upgrade.
