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

## Manifest Schema (v3)

```json
{
  "schema_version": 3,
  "mission_id": "<uuid>",
  "objective": "<user-provided objective string>",
  "created_at": "<ISO-8601>",
  "updated_at": "<ISO-8601>",

  "phase": "<current phase name>",
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
    "CHANGELOG.md",
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
| `schema_version` | int | Always `3` for this version |
| `mission_id` | string | UUID v4, generated at init |
| `objective` | string | The user's original objective text |
| `created_at` | string | ISO-8601 timestamp of mission creation |
| `updated_at` | string | ISO-8601 timestamp of last state change |
| `phase` | string | Current phase name |
| `phase_history` | array | Ordered list of phase transitions |
| `roles` | array | Required roles for this mission |
| `optional_roles` | array | Optional roles the coordinator chose to include |
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
- `audit` requires 4 role results for the current epoch before the stop hook
  will allow the mission to pause or end:
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
   closing ledger entry.

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
{"timestamp":"2026-03-28T10:00:00Z","event":"mission_created","mission_id":"...","objective":"..."}
{"timestamp":"2026-03-28T10:01:00Z","event":"phase_enter","phase":"plan"}
{"timestamp":"2026-03-28T10:02:00Z","event":"phase_exit","phase":"plan","result":"pass"}
{"timestamp":"2026-03-28T10:02:01Z","event":"phase_enter","phase":"implement"}
{"timestamp":"2026-03-28T10:05:00Z","event":"tool_use","role":"implementer","tool":"Write","target":"src/auth.ts","outcome":"allowed"}
{"timestamp":"2026-03-28T10:10:00Z","event":"role_complete","role":"implementer","status":"pass"}
{"timestamp":"2026-03-28T10:10:01Z","event":"phase_exit","phase":"implement","result":"pass"}
{"timestamp":"2026-03-28T10:30:00Z","event":"mission_closed","status":"passed"}
```

The ledger can be trimmed with `collabctl ledger-trim --before <date>` to
remove old entries and keep the file compact.
