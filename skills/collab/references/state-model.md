# State Model

All collab state lives under the git common directory so that worktree
checkouts share the same mission state.

---

## Directory Layout

```
<git-common-dir>/claude-collab/
  manifest.json           # mission configuration and current state
  ledger.ndjson           # append-only event log (one JSON object per line)
  results/                # per-role result files
    implementer.json
    test-writer.json
    skeptic.json
    security.json
    performance.json
    accessibility.json
    docs.json
    verifier.json
  worktrees/              # managed by git-worktree, one per isolated role
    collab-implementer/
    collab-test-writer/
    collab-performance/
    collab-docs/
    collab-verifier/
```

The path is resolved at runtime via:

```bash
git rev-parse --git-common-dir
```

This returns `.git` for a normal checkout and the shared `.git` path for
worktree checkouts.

---

## Manifest Schema (v4)

```json
{
  "schema_version": 4,
  "mission_id": "<uuid>",
  "objective": "<user-provided objective string>",
  "created_at": "<ISO-8601>",
  "updated_at": "<ISO-8601>",

  "phase": "<current phase name>",
  "phase_started_at": "<ISO-8601 or null>",
  "phase_history": [
    {
      "phase": "<phase-name>",
      "entered_at": "<ISO-8601>",
      "exited_at": "<ISO-8601 or null>",
      "result": "pass | fail | skip | null"
    }
  ],

  "roles": ["implementer", "test-writer", "skeptic", "..."],
  "optional_roles": ["security", "performance", "accessibility", "docs"],
  "planned_roles": ["implementer", "test-writer", "skeptic", "security"],
  "skipped_roles": [],

  "model_map": {
    "implementer": "claude-sonnet-4-20250514",
    "skeptic": "claude-opus-4-20250514",
    "security": "claude-opus-4-20250514"
  },

  "role_assignments": {
    "skeptic": {
      "started_at": "<ISO-8601>",
      "completed_at": "<ISO-8601 or null>",
      "model": "claude-opus-4-20250514",
      "outcome": "pass | fail | warn | null",
      "duration_seconds": 42,
      "finding_count": 3
    }
  },

  "allowed_paths": [
    "src/**",
    "lib/**"
  ],
  "test_paths": [
    "tests/**",
    "test/**",
    "__tests__/**",
    "*.test.*",
    "*.spec.*"
  ],
  "doc_paths": [
    "docs/**",
    "README.md",
    "CONTRIBUTING.md",
    "*.md"
  ],

  "security_scan_commands": [
    "semgrep",
    "trufflehog",
    "gitleaks",
    "bandit",
    "npm audit",
    "cargo audit"
  ],
  "benchmark_commands": [
    "hyperfine",
    "time",
    "node --prof",
    "py-spy",
    "perf stat"
  ],
  "a11y_commands": [
    "axe",
    "pa11y",
    "lighthouse"
  ],

  "tdd_mode": false,
  "custom_roles": [],
  "timeout_hours": 24,
  "role_failure_counts": {},
  "awaiting_user": true,
  "awaiting_user_reason": "<string or null>",

  "fail_closed": false,
  "git_baseline": "<git-status-snapshot-at-init>",

  "loop_count": 0,
  "loop_epoch": 0,
  "max_loops": 3,

  "status": "active | passed | aborted",
  "abort_reason": "<string or null>"
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | Always `4` for this version |
| `mission_id` | string | UUID v4, generated at init |
| `objective` | string | The user's original objective text |
| `created_at` | string | ISO-8601 timestamp of mission creation |
| `updated_at` | string | ISO-8601 timestamp of last state change |
| `phase` | string | Current phase name |
| `phase_started_at` | string\|null | ISO-8601 timestamp of when the current phase began, null before first transition |
| `phase_history` | array | Ordered list of phase transitions |
| `roles` | array | Required roles for this mission |
| `optional_roles` | array | Optional roles the coordinator chose to include |
| `planned_roles` | array | Roles planned for this mission at init time |
| `skipped_roles` | array | Roles explicitly skipped via `--skip-role` |
| `model_map` | object | Per-role model backend selection (e.g., `{"skeptic": "claude-opus-4-20250514"}`) |
| `role_assignments` | object | Per-role timing, model, outcome, duration, and finding count |
| `allowed_paths` | array | Glob patterns for implementer write access |
| `test_paths` | array | Glob patterns for test-writer write access |
| `doc_paths` | array | Glob patterns for docs role write access |
| `security_scan_commands` | array | Commands the security role may run via Bash |
| `benchmark_commands` | array | Commands the performance role may run via Bash |
| `a11y_commands` | array | Commands the accessibility role may run via Bash |
| `tdd_mode` | bool | When true, plan goes to test before implement |
| `custom_roles` | array | User-defined roles with custom scopes |
| `timeout_hours` | int | Hours before the mission times out (0 = disabled) |
| `role_failure_counts` | object | Per-role count of consecutive validation failures |
| `awaiting_user` | bool | True when mission is paused for user input |
| `awaiting_user_reason` | string | Reason for awaiting user, null when not awaiting |
| `loop_count` | int | Global loop-back counter |
| `loop_epoch` | int | Monotonic epoch incremented on each loop-back |
| `max_loops` | int | Maximum loops before forced abort |
| `fail_closed` | bool | When true, hooks deny on error instead of allowing (default false) |
| `git_baseline` | string | Git status snapshot captured at init for close-time scope verification |
| `status` | string | Mission status: active, passed, or aborted |
| `abort_reason` | string | Reason for abort, null if not aborted |

---

## Lifecycle Rules

1. **Creation**: `collabctl init` creates the manifest, sets phase to
   `plan`, and writes the first ledger entry.

2. **Phase transitions**: `collabctl phase <name>` updates `manifest.phase`,
   appends to `phase_history`, increments `loop_count` if revisiting a
   phase, and appends a ledger entry.

### Audit Phase

- `audit` is the recommended composite review phase after `test`.
- Audit roles (skeptic, security, performance, accessibility) are required
  before the mission can transition to `verify`.  To explicitly skip a role,
  pass `--skip-role <name>` at init or during the audit phase.  Skipped roles
  are recorded in `manifest.skipped_roles`.
- `audit` requires results for all non-skipped audit roles for the current
  epoch before the stop hook will allow the mission to pause or end:
  - `skeptic.json`
  - `security.json`
  - `performance.json`
  - `accessibility.json`
- If any audit result recommends `needs_changes` or `abort`, the coordinator
  loops back to `implement` (or `test` when the mission is in TDD mode).
- The legacy sequential phases `review`, `security`, `performance`, and
  `accessibility` remain supported for backward compatibility.

3. **Loop limits**: If `loop_count >= max_loops`, the coordinator must
   abort.  The collabctl `phase` command enforces this and will refuse the
   transition when the limit is exceeded.

### TDD Mode

When `tdd_mode` is true, the phase transition table changes so that
`plan` goes directly to `test` (not `implement`), and `test` goes to
`implement`.  This enforces the test-first workflow.  The pre-tool hook
additionally blocks implementer writes until a `test-writer` result exists
for the current epoch.

### Mission Timeout

When `timeout_hours` is set (default 24), the stop hook allows the session
to exit once the mission age exceeds this threshold, even if work is still
pending.  Set to 0 to disable the timeout.

### Role Failure Tracking

The `role_failure_counts` object tracks consecutive validation failures
per role.  After 3 consecutive failures from the same role, the mission
is automatically set to `awaiting_user=true` so the user can intervene.

4. **Closing**: `collabctl close pass` or `collabctl close abort` sets
   `status`, records the final `phase_history` entry, and appends a
   closing ledger entry.  At close time, scope verification compares the
   current `git diff` against `allowed_paths` to confirm no out-of-scope
   files were modified.  If violations are found, `close` is blocked.  Use
   `--force-close` with a reason to override; the override is logged to the
   ledger and `progress.md`.

5. **Persistence across sessions**: The manifest and ledger persist in
   `.git/claude-collab/`.  A new session picks up from where the last one
   left off via the `SessionStart` hook.

6. **Compaction safety**: The `PreCompact` hook writes a full snapshot to
   the manifest so that state is recoverable even if the context window is
   trimmed.

---

## Role Result Contract

Every role writes its result to `.git/claude-collab/results/<role>.json`.

```json
{
  "role": "<role-name>",
  "phase": "<phase-name>",
  "status": "pass | fail | warn",
  "summary": "<one-line summary of what the role did>",
  "findings": [
    "<finding-1>",
    "<finding-2>"
  ],
  "files_touched": [
    "<relative-path>"
  ],
  "timestamp": "<ISO-8601>"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `role` | yes | The role name (must match agent name without `collab-` prefix) |
| `phase` | yes | The phase this result belongs to |
| `status` | yes | `pass` (no issues), `fail` (blocking issues), `warn` (non-blocking issues) |
| `summary` | yes | One-line human-readable summary |
| `findings` | yes | Array of strings, one per finding.  Empty array if no findings |
| `files_touched` | yes | Array of file paths.  Empty array for read-only roles |
| `timestamp` | yes | ISO-8601 timestamp of result creation |

---

## Ledger Format

The ledger is a newline-delimited JSON (NDJSON) file.  Each line is a
self-contained JSON object.

```json
{"ts":"2026-03-28T10:00:00Z","kind":"mission_created","mission_id":"...","objective":"..."}
{"ts":"2026-03-28T10:01:00Z","kind":"phase_enter","phase":"plan"}
{"ts":"2026-03-28T10:02:00Z","kind":"phase_exit","phase":"plan","result":"pass"}
{"ts":"2026-03-28T10:02:01Z","kind":"phase_enter","phase":"implement"}
{"ts":"2026-03-28T10:05:00Z","kind":"tool_use","role":"implementer","tool":"Write","target":"src/auth.ts","outcome":"allowed"}
{"ts":"2026-03-28T10:08:00Z","kind":"role_dispatched","role":"implementer","phase":"implement","model":"claude-sonnet-4-20250514"}
{"ts":"2026-03-28T10:10:00Z","kind":"role_completed","role":"implementer","phase":"implement","outcome":"pass","duration_seconds":120,"finding_count":0,"model":"claude-sonnet-4-20250514"}
{"ts":"2026-03-28T10:10:01Z","kind":"phase_exit","phase":"implement","result":"pass"}
{"ts":"2026-03-28T10:15:00Z","kind":"hook_error","hook":"collab_pre_tool","error":"FileNotFoundError: manifest.json"}
{"ts":"2026-03-28T10:20:00Z","kind":"scope_violation_reverted","role":"skeptic","paths":["src/auth.ts"]}
{"ts":"2026-03-28T10:25:00Z","kind":"force_override","phase_from":"audit","phase_to":"verify"}
{"ts":"2026-03-28T10:30:00Z","kind":"mission_closed","status":"passed"}
```

### Event Reference

| Kind | Fields | Description |
|------|--------|-------------|
| `mission_created` | mission_id, objective | Mission initialized |
| `phase_enter` | phase | Entered a new phase |
| `phase_exit` | phase, result | Exited a phase with result |
| `tool_use` | role, tool, target, outcome | Tool call recorded |
| `role_dispatched` | role, phase, model | Role subagent launched with model identity |
| `role_completed` | role, phase, outcome, duration_seconds, finding_count, model | Role finished with timing and result data |
| `hook_error` | hook, error | A hook raised an exception |
| `scope_violation_reverted` | role, paths | Out-of-scope writes reverted by PostToolUse |
| `force_override` | phase_from, phase_to | `--force` used to bypass phase rules |
| `mission_closed` | status | Mission closed as passed or aborted |

The ledger can be trimmed with `collabctl ledger-trim --before <date>` to
remove old entries and keep the file compact.

