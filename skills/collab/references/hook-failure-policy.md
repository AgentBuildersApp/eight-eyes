# Hook Failure Behavior

What happens when a hook encounters an error during execution.

## Default Mode (fail_closed = false)

All hooks allow the action to proceed on error. The error is logged to the ledger.

## Strict Mode (fail_closed = true)

Security-critical hooks deny the action on error. Other hooks still allow.

| Hook | Default Mode | Strict Mode | What Happens on Error |
|------|-------------|-------------|----------------------|
| `session_start` | Allow | Allow | Session starts without mission context |
| `pre_tool` | Allow | **Deny** | Tool call blocked or allowed depending on mode |
| `post_tool` | Allow | Allow | Tool action not recorded in ledger |
| `subagent_start` | Allow | Allow | Subagent starts without role context |
| `subagent_stop` | Allow | **Deny** | Result validation skipped or blocked depending on mode |
| `pre_compact` | Allow | Allow | State snapshot not captured |
| `stop` | Allow | Allow + warn | Session exits, warning persisted for next session |

Hooks that deny on error will retry twice before escalating. If all retries fail, the user is notified.
