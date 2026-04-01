# eight-eyes

[![CI](https://github.com/AgentBuildersApp/eight-eyes/actions/workflows/test.yml/badge.svg)](https://github.com/AgentBuildersApp/eight-eyes/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.0.0--alpha-orange.svg)](VERSION)
[![stdlib only](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](#requirements)

![eight-eyes](docs/images/header.png)

> AI agents agree with each other. That's the problem.

### The Failure Nobody Talks About

You ask an AI agent to review code it just wrote. It says *"looks good."* You ask a second agent. It reads the first agent's summary, anchors on the same framing, and also says *"looks good."*

Meanwhile:

```
  jwt.decode(token, public_key, algorithms=["HS256"])
  #                 ^^^^^^^^^^              ^^^^^^^
  #  RSA public key used as HMAC secret → attacker forges any token
  #  CVE-2016-10555 — sitting in plain sight
```

**Nobody caught it** because every reviewer saw the same narrative, used the same tools, and had the same incentives. This is how AI agents fail — not by crashing, but by agreeing.

### The Fix

**eight-eyes** splits a code change into eight constrained roles — each aimed at a different failure surface. The skeptic never sees the implementer's narrative. The security auditor cannot edit files. The implementer cannot run Bash.

These aren't suggestions in a system prompt. They are **hook-enforced walls** that intercept tool calls before execution. If the model ignores the prompt, the hook still blocks the action.

---

## What It Catches

A single `/8eyes` mission on a JWT auth refactor surfaces findings like these — independently and in parallel:

| Role | Verdict | Finding |
|------|---------|---------|
| **skeptic** | needs_changes | Token refresh endpoint is untested. If the refresh token is expired, the user hits a bare 500 — no redirect, no retry, no error message. |
| **security** | needs_changes | `jwt.decode()` uses `algorithms=['HS256']` but the key is an RSA public key. An attacker can forge tokens by signing with the public key as an HMAC secret. See CVE-2016-10555. |
| **performance** | approve | No N+1 patterns. Token validation adds ~2ms per request — within budget. |
| **accessibility** | approve | Login error states have `aria-live` regions and visible focus indicators. Passes axe-core audit. |
| **verifier** | needs_changes | Criterion "refresh token rotation on use" — NOT MET. `/auth/refresh` returns a new access token but reuses the same refresh token. Evidence: curl output shows identical `refresh_token` in response. |

Each finding includes file paths, line numbers, and concrete evidence. The verifier runs only the commands you approved at init time — it cannot invent its own.

---

## Why Hook-Enforcement Changes Everything

### The Prompt Problem

When you rely on prompts to enforce constraints:

```
Prompt: "Please stay read-only"

├── Model ignores it → Writes happen → Hidden vulnerability
└── Model forgets  → Writes happen → Silent drift
```

**The failure mode is silent.** You don't know the model drifted until the damage is in the diff.

### The Hook Solution

eight-eyes intercepts at the tool layer — before the action executes:

```
Hook: PreToolUse blocks write

├── Model tries anything → Write denied → Audit log captures attempt
└── Model compliant     → Write allowed → Enforced by architecture
```

### The Four Enforcement Points

| Hook | When it fires | What it enforces |
|------|--------------|-----------------|
| `SubagentStart` | Role begins | Injects role context and blind-review barriers. The skeptic physically cannot see the implementer's summary. |
| `PreToolUse` | Before any tool call | Blocks out-of-scope writes and unapproved commands before execution. |
| `PostToolUse` | After any tool call | Auto-reverts unauthorized writes for read-only roles. |
| `SubagentStop` | Role ends | Requires a structured result block with evidence. Missing or invalid results are rejected. |

**The difference:** Prompts can be overridden. Hooks cannot.

The full enforcement model — gate classes, failure modes, and per-platform coverage — is defined in `spec/enforcement.yaml` and inspectable at any time:

```bash
python3 scripts/collabctl.py capabilities
```

---

## Quick Start

### Claude Code

```bash
claude plugin marketplace add AgentBuildersApp/eight-eyes
claude plugin install 8eyes@8eyes-marketplace
```

### GitHub Copilot CLI

```bash
copilot plugin marketplace add AgentBuildersApp/eight-eyes
copilot plugin install 8eyes@8eyes-marketplace
```

### OpenAI Codex CLI

```bash
git clone https://github.com/AgentBuildersApp/eight-eyes.git
cd eight-eyes
python3 install.py --platform codex_cli
```

### Manual install (all platforms)

```bash
git clone https://github.com/AgentBuildersApp/eight-eyes.git
cd eight-eyes
python3 install.py
```

Then run your first mission:

```bash
/8eyes:collab Refactor auth to use JWT
```

This initializes a mission, sets scope boundaries, and launches the eight roles through the phase flow. When it finishes, you get a structured result from each role with findings, evidence, and a pass/needs_changes/abort recommendation.

### Verify & Manage

```bash
python3 scripts/collabctl.py --version          # Check installed version
python3 scripts/collabctl.py verify --install-only  # Verify without a git repo
python3 scripts/collabctl.py locate              # Show all install locations
python3 install.py --uninstall                   # Clean removal
```

### Platform Notes

| Platform | Python | Notes |
|----------|--------|-------|
| macOS / Linux | `python3` on PATH | Symlinks to home directory. No `sudo` needed. |
| Windows | `python3` or `python` | Symlinks with copy fallback. File locking uses `msvcrt`. |
| CI / Docker | 3.10 through 3.13 | Zero dependencies. No `pip install` step. Ensure `git` is in the image. |

---

## What's New in 5.0

### Verifiable enforcement

Previous versions told you what was enforced. Now you can verify it yourself:

```bash
python3 scripts/collabctl.py capabilities
```

```
Hook               Gate Class     Failure Mode     Claude    Copilot   Codex
PreToolUse         hard_gate      deny             supported supported degraded
SubagentStop       hard_gate      block            supported supported —
PostToolUse        recovery       fail_open        supported supported degraded
Stop               lifecycle      warn             supported supported supported
SessionStart       lifecycle      fail_open        supported supported degraded
SubagentStart      lifecycle      fail_open        supported supported —
PreCompact         observability  async_fail_open  supported —         —
```

Every hook has an explicit gate class, failure mode, and per-platform support level. `--json` gives you machine-readable output for CI. This is the enforcement contract — not a README claim, but an inspectable artifact that tests are written against.

### Machine-readable mission status

```bash
python3 scripts/collabctl.py status --json
```

Returns structured JSON with planned roles, completed roles with outcomes, pending roles, skipped roles, fail-closed state, and loop count. Build dashboards, integrate with CI, or pipe to `jq` — mission state is no longer trapped in text output.

### Custom roles are first-class

In 4.x, a manifest-defined `read_only` custom role silently bypassed PostToolUse audit and revert handling. If your custom auditor accidentally wrote a file, nothing caught it.

In 5.0, custom roles receive the same compensating revert as built-in roles. Write attempts are reverted. Revert events are ledgered with `revert_mode` (tracked checkout vs untracked delete) and `revert_success` status. The audit trail distinguishes built-in from custom role type.

### Platform coverage you can test against

Platform support is no longer a table in a README. It is a machine-readable matrix in `spec/enforcement.yaml`, verified by parity tests that run against the actual adapter manifests. If a hook is marked "supported" for Copilot, the Copilot adapter manifest includes it — and a test asserts that. If Codex says "degraded," every surface agrees.

---

## When This Matters

### Solo Developer

You wrote the code and reviewed it yourself. `eight-eyes` gives you eight reviewers who didn't write it and can't see each other's notes. The verifier runs your acceptance criteria against the actual code — confidence is not proof.

### Security-Critical Work

You're building auth or payment flows where a missed edge case has real consequences. The security role reviews like an external auditor — read-only, approved scan commands only. It cannot "fix" things and accidentally hide the vulnerability.

### AI-Assisted Development

Your team uses AI coding agents but nobody reviews the output with adversarial intent. `eight-eyes` reviews AI-generated code like a junior developer's first PR — except it can't be talked out of its concerns. The skeptic literally cannot see the author's narrative.

---

## The 8 Roles

| Role | What it catches | How it is enforced |
|------|------------------|-------------------|
| `implementer` | Incorrect implementation, missed requirements | Writes limited to `allowed_paths`; no Bash |
| `test-writer` | Missing tests, weak edge coverage | Writes limited to `test_paths`; no Bash |
| `skeptic` | Anchoring bias, rollback risk, hidden coupling | Read-only; blind review (no implementer context) |
| `security` | Auth bypass, injection, secrets exposure | Read-only + approved scan commands |
| `performance` | N+1 queries, algorithmic blowups | Read-only + approved benchmark commands |
| `accessibility` | Keyboard traps, missing labels, contrast failures | Read-only + approved a11y commands |
| `docs` | Stale docs, undocumented behavior | Writes limited to `doc_paths`; no Bash |
| `verifier` | Confidence without proof | Read-only + approved verification commands |

### The Phase Flow

```
plan → implement → test → audit → verify → docs → close
                        ↑                    |
                        └── loop on failure ─┘
```

During `audit`, the skeptic, security, performance, and accessibility roles run **in parallel**. If any returns `needs_changes`, the mission loops back to `implement` automatically.

### Blind Review

The skeptic sees the objective, acceptance criteria, and changed paths — but **not** the implementer's narrative or summary. This is enforced by context shaping at the hook level. The skeptic forms an independent opinion because it does not have the implementer's framing in its context window.

---

## Architecture

![Architecture](docs/images/architecture.png)

Mission state lives under the Git common directory, not the working tree. That keeps the coordinator, the root checkout, and any isolated worktrees pointed at the same manifest, ledger, and per-role result files.

Worktree isolation is used where incidental writes or tool artifacts would otherwise leak across roles.

---

## Configuration

### Model Routing

Route specific roles to different model backends:

```bash
/8eyes:collab Refactor auth --model-map '{"skeptic":"claude-opus-4-20250514","security":"claude-opus-4-20250514"}'
```

### Custom Roles

Add roles without changing the core engine:

```bash
python3 scripts/collabctl.py init \
  --objective "Run lint review" \
  --allowed-path src \
  --custom-role "name=linter,scope=read_only,commands=eslint src/"
```

### TDD Mode

`--tdd` changes the phase order to `plan → test → implement`. The hook layer blocks implementer writes until a test-writer result exists.

### REVIEW.md

Drop a `REVIEW.md` in your project root with review criteria. It is automatically injected into the skeptic, security, and verifier context:

```markdown
## Review Criteria
- All API endpoints must validate input before processing
- No credentials in logs, error messages, or API responses
- Database queries must use parameterized statements
- Frontend changes must pass axe-core accessibility audit
```

### CLI Reference

| Command | What it does |
|---------|-------------|
| `init` | Creates a mission with objective, scope, and acceptance criteria |
| `show` | Prints the active mission state as JSON |
| `status` | Shows role progress with timing and model identity. `--json` for machine-readable output |
| `timeline` | Chronological role dispatch and completion table |
| `report` | Consolidated findings across all roles |
| `phase <name>` | Advances the mission to the next phase |
| `close pass\|abort` | Closes the mission with scope verification |
| `verify` | Checks installation. `--install-only` skips git requirement. |
| `capabilities` | Displays the enforcement model: hook semantics, gate classes, and per-platform coverage. `--role <name>` filters to one role. `--json` for machine-readable output |
| `locate` | Prints all known install locations per platform |
| `--version` | Prints the installed version |

---

## Platform Support

| Platform | Status | Scope Enforcement |
|----------|--------|-------------------|
| Claude Code | Full (GA) | Hook-level (all tools) |
| Copilot CLI | Full (GA) | Hook-level (all tools) |
| Codex CLI | Experimental | Hook-level (Bash only), prompt-level (Write/Edit) |

## Testing

152 tests. Stdlib only. No external dependencies.

```bash
python3 -m pytest tests/ -q
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `/8eyes` does nothing | Run `python3 install.py` inside a Git repo |
| Implementer writes denied | Add `--allowed-path` entries at init |
| Bash denied for audit role | Add via `--security-command`, `--benchmark-command`, etc. |
| Phase transition rejected | Follow the phase table, or `--force` to override |
| `close` blocked by scope violation | Use `--force-close "reason"` to override |
| Verify fails outside git repo | Use `--install-only` flag |

## Requirements

- Python 3.10+
- Git
- One or more: Claude Code, Copilot CLI, Codex CLI

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on adding custom roles or platform adapters.

## License

MIT
