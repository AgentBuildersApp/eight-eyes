# Hook Map

Mapping of each hook script to its event, matcher, output schema, and
decision control.

---

## Hook Scripts

| Hook Script | Event | Matcher | Purpose |
|------------|-------|---------|---------|
| `collab_session_start.py` | `SessionStart` | `startup\|resume\|clear\|compact` | Inject manifest state into context on session start or resume |
| `collab_pre_tool.py` | `PreToolUse` | `Write\|Edit\|MultiEdit\|Bash` | Enforce role permissions before tool execution |
| `collab_post_tool.py` | `PostToolUse` | `Write\|Edit\|MultiEdit\|Bash` | Record tool usage in ledger after execution |
| `collab_subagent_start.py` | `SubagentStart` | `collab-*` (all 8 built-in roles plus custom roles) | Set up role context, worktree isolation, blind review stripping |
| `collab_subagent_stop.py` | `SubagentStop` | `collab-*` (all 8 built-in roles plus custom roles) | Collect results, clean up worktrees, write result files |
| `collab_pre_compact.py` | `PreCompact` | (none) | Snapshot mission state before context compaction |
| `collab_stop.py` | `Stop` | (none) | Clean up worktrees and finalize ledger on session end |

---

## Output Schemas

### SessionStart (`collab_session_start.py`)

No decision output.  Injects state by appending to the assistant context.

```json
{
  "instructions": "Collab mission in progress. Current phase: <phase>. Run collabctl show for full state."
}
```

### PreToolUse (`collab_pre_tool.py`)

Returns a decision that can allow or block the tool call.

**Allow**:
```json
{
  "decision": "allow",
  "reason": "Tool call within role scope"
}
```

**Block**:
```json
{
  "decision": "block",
  "reason": "<explanation of why the call was blocked>"
}
```

Decision logic by role:

| Role | Write/Edit/MultiEdit | Bash |
|------|---------------------|------|
| implementer | Allow if target path is in `allowed_paths` | Block always |
| test-writer | Allow if target path is in `test_paths` | Block always |
| skeptic | Block always | Allow if command is read-only |
| security | Block always | Allow if command is read-only or in `security_scan_commands` |
| performance | Block always | Allow if command is read-only or in `benchmark_commands` |
| accessibility | Block always | Allow if command is read-only or in `a11y_commands` |
| docs | Allow if target path is in `doc_paths` | Block always |
| verifier | Block always | Allow if command is read-only or is a test runner |
| coordinator | Allow always | Allow always |

### PostToolUse (`collab_post_tool.py`)

No decision output.  Appends a ledger entry.  For read-only roles,
unauthorized writes are automatically reverted via `git checkout` (tracked
files) or `unlink` (untracked files).  The revert is recorded as a
`scope_violation_reverted` event in the ledger.

```json
{
  "timestamp": "<ISO-8601>",
  "event": "tool_use",
  "role": "<active-role>",
  "tool": "<tool-name>",
  "target": "<file-path-or-command>",
  "outcome": "allowed"
}
```

### SubagentStart (`collab_subagent_start.py`)

No decision output.  Side effects:
- Creates worktree for roles that require isolation
- Sets `COLLAB_ROLE` and `COLLAB_MISSION_ID` environment variables
- For `collab-skeptic`: strips implementation diffs from injected context
- Records role dispatch timing and model identity to manifest (`role_assignments`) and ledger (`role_dispatched` event)

### SubagentStop (`collab_subagent_stop.py`)

No decision output.  Side effects:
- Reads the role's result from the subagent output
- Writes result to `.git/claude-collab/results/<role>.json`
- Cleans up worktree if the role used isolation
- Records role completion timing (duration, finding count) to manifest (`role_assignments`) and ledger (`role_completed` event)

### PreCompact (`collab_pre_compact.py`)

No decision output.  Runs async.  Side effects:
- Writes a full state snapshot to `manifest.json`
- Appends a `compact_snapshot` event to the ledger

### Stop (`collab_stop.py`)

No decision output.  Side effects:
- Removes any remaining worktrees
- Appends a `session_stop` event to the ledger
- Does NOT close the mission (the mission survives across sessions)

---

## Fail-Open / Fail-Closed Behavior

All hooks use a double-defense `_fail_open` pattern: if the hook itself
raises an exception (missing manifest, malformed JSON, etc.), the default
behavior is to allow the tool call so the session is not bricked by a hook
bug.  When `manifest.fail_closed` is `true`, the `PreToolUse` hook inverts
this default and denies on error instead.  Hook errors are always recorded
as `hook_error` events in the ledger regardless of mode.
