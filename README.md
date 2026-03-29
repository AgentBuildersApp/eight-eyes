# eight-eyes

![eight-eyes](docs/images/header.png)

> Multi-agent code review for Claude Code, Copilot CLI, and Codex CLI.

You ask an agent to review its own work. It says "looks good." You ask a second agent. It also says "looks good" -- because it read the first agent's summary and anchored on the same framing. Meanwhile an auth bypass sits in plain sight because nobody looked at the code like an attacker would.

`eight-eyes` splits a code change into eight constrained roles -- implementer, test writer, skeptic, security auditor, performance profiler, accessibility checker, docs writer, and verifier -- each aimed at a different failure surface. The skeptic never sees the implementer's narrative. The implementer cannot run Bash. The security auditor cannot edit files. These are not suggestions in a system prompt. They are hook-enforced walls: the `PreToolUse` hook intercepts tool calls before execution and blocks anything outside the role's scope.

Prompts define how a model should behave. Hooks decide what it is allowed to do. That distinction is why this works.

## What It Catches

A single `/8eyes` mission on a JWT auth refactor surfaces findings like these across its eight roles:

```
skeptic       needs_changes  "Token refresh endpoint is untested. If the refresh
                              token is expired the user hits a bare 500 — no
                              redirect, no retry, no error message."

security      needs_changes  "jwt.decode() uses algorithms=['HS256'] but the key
                              is an RSA public key. An attacker can forge tokens
                              by signing with the public key as an HMAC secret.
                              See CVE-2016-10555."

performance   approve        "No N+1 patterns. Token validation adds ~2ms per
                              request — within budget."

accessibility approve        "Login error states have aria-live regions and
                              visible focus indicators. Passes axe-core audit."

verifier      needs_changes  "Criterion 'refresh token rotation on use' — NOT MET.
                              /auth/refresh returns a new access token but reuses
                              the same refresh token. Evidence: curl output shows
                              identical refresh_token in response."
```

Each finding includes file paths, line numbers, and concrete evidence. The verifier runs only the commands you approved at init time -- it cannot invent its own.

## When This Matters

---

### Solo Developer

- You wrote the code and reviewed it yourself. `eight-eyes` gives you eight reviewers who didn't write it and can't see each other's notes.

- You're refactoring a module the rest of your app depends on. The verifier runs your acceptance criteria against the actual code — confidence is not proof.

- You're upgrading a major dependency and the migration guide says "most apps won't need changes." The skeptic reviews blind without your optimism, the performance role benchmarks before and after, and the test writer checks whether your tests actually cover the changed APIs.

- You're burning through tech debt and don't trust yourself to avoid regressions. `eight-eyes` scopes the implementer to the paths you choose while the skeptic and verifier independently confirm nothing adjacent broke.

---

### Teams

- Your team's code reviews are mostly "LGTM." The skeptic reviews blind — no author narrative, no ability to edit files, only to find problems.

- A new developer is merging their first PR. `eight-eyes` produces structured findings with file paths and evidence in minutes instead of waiting days for a senior to find time.

- Your style guide lives in a wiki nobody reads during review. Put it in `REVIEW.md` — the skeptic, security, and verifier check against it every time.

- Six PRs are waiting for review on Friday afternoon. `/8eyes` gives each one the same structured eight-role review regardless of how tired the team is.

---

### Security-Critical Work

- You're building auth or payment flows where a missed edge case has real consequences. The security role reviews like an external auditor — read-only, approved scan commands only, cannot "fix" things.

- You need to verify no credentials leak into logs, error messages, or API responses. The security role scans for exactly this and cannot accidentally hide the problem by editing the code.

- You're changing how API keys or PII are accessed. The security role checks for exposure in the diff while the verifier confirms the new access pattern works — neither sees the other's findings.

---

### Quality Gates

- You're shipping to a client and need evidence that security, accessibility, and testing actually happened. Every role writes structured results with evidence. The mission ledger is an auditable record.

- You want a review gate in CI beyond linting. `eight-eyes` produces machine-readable JSON — a CI step can check per-role pass/fail status and block on specific findings.

- You're preparing a release candidate. The verifier checks each criterion, the skeptic looks for things that break in production but pass in dev, the docs role confirms your changelog is current.

---

### AI-Assisted Development

- Your team uses AI coding agents but nobody reviews the output with adversarial intent. `eight-eyes` reviews AI-generated code like a junior developer's first PR — except it can't be talked out of its concerns.

- You used Copilot to generate a module and something feels off. The skeptic reviews blind — no knowledge of what the AI was asked to do. The security role checks for patterns AI generators frequently get wrong: permissive defaults, missing validation, hardcoded fallbacks.

- One AI writes code and another reviews it, but they keep agreeing. `eight-eyes` breaks this because the skeptic literally cannot see the author's narrative. Agreement requires independent evidence.

---

### Legacy & Migration

- You inherited a codebase with no tests and a framework two versions behind. Run `/8eyes` with `--tdd` — the test writer creates tests for current behavior before you touch it, then the verifier confirms they still pass after.

- You're changing your database schema. The verifier runs migration scripts with approved commands, the security role checks for data exposure through new API fields, the skeptic asks whether rollback is possible.

- You're replacing an internal API with a third-party service. The performance role benchmarks against the old implementation, the test writer covers the contract boundary, the verifier confirms the switchover end to end.

---

### Open Source

- You receive a PR from a contributor you've never worked with and don't have time to trace every code path. Eight roles split the review surface so security, performance, correctness, and accessibility are each examined independently.

- You're cutting a release that other projects depend on. The docs role checks that API docs match actual exports, the verifier runs your compatibility suite, the skeptic looks for breaking changes the tests don't cover.

---

### Compliance & Audit

- You need to show an auditor that code changes go through documented review. `eight-eyes` produces timestamped, structured evidence per role — append-only ledger, every tool action recorded. This is a machine-readable audit trail, not a checkbox.

- You keep shipping accessibility regressions. The accessibility role runs your approved tools — semantic HTML, keyboard nav, contrast — every time, not just when someone remembers.

- Security-sensitive changes need a second set of eyes. The security role's structured findings with severity ratings and CVE references give the compliance team what they need without scheduling a meeting.

---

### Education

- You wrote your first feature in a new framework and want real feedback, not a tutorial. Eight roles review your code the way experienced practitioners would — findings link to specific lines, not generic advice.

- You're mentoring a junior developer. Run `/8eyes` on their project — the structured findings become a teaching syllabus across eight perspectives, not just yours.

---

### Custom Roles

- Your project needs i18n verification. Define `--custom-role "name=i18n-checker,scope=read_only,commands=grep -r 'hardcoded string' src/"` — it runs alongside the other roles with the same enforcement.

- You have license compliance requirements. A custom role runs your license checker, can't edit files, and reports in the same schema. The verifier confirms findings are addressed before close.

- You need every API endpoint to match the OpenAPI spec. A custom verifier runs your contract tests, the skeptic reviews the spec for ambiguity, the docs role updates it if the implementation diverged.

---

### High-Stakes Environments

- You're refactoring a payment processor. The implementer is scoped to `src/payments/` — `src/fraud/` is a denied path. The security auditor runs `snyk test` before close. The blind skeptic verifies fraud logic is unchanged.

- You're updating a patient portal UI. The implementer can reach `frontend/` but not `backend/`, `db/`, or `config/secrets`. The security auditor scans for PHI patterns. The verifier runs a regex sweep for SSN and DOB in test fixtures.

- A junior dev and an AI are adding OAuth2 login. The implementer can write to `src/auth/oauth/` but `src/middleware/` is denied — nobody can modify authentication middleware. The security auditor runs `semgrep --config=auth` and must find zero issues.

- Your nightly CI job updates dependencies. The verifier must pass unit, integration, and e2e suites — not just unit tests. The security auditor blocks on moderate-or-higher vulnerabilities.

- Two developers run AI agents simultaneously on different tasks. Each mission scopes its implementer to different paths. Shared critical files are denied to both — no merge conflicts in files that matter.

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
python3 install.py          # auto-detects installed platforms
```

---

Then, from your project directory:

```bash
/8eyes:collab Refactor auth to use JWT
```

This initializes a mission, sets scope boundaries, and launches the eight roles through the phase flow. When it finishes, you get a structured result from each role with findings, evidence, and a pass/needs_changes/abort recommendation.

### After the Mission

The coordinator collects all eight result files under your Git state directory. To inspect the current mission:

```bash
python3 scripts/collabctl.py show
```

This prints the manifest, phase, results, and any outstanding findings as JSON. If audit or verification flagged issues, the flow loops back automatically. If everything passed, close the mission:

```bash
python3 scripts/collabctl.py close pass
```

`/8eyes` is the public entrypoint. `collabctl` is the state-management CLI underneath.

## How It Works

### The 8 Roles

| Role | What it catches | How it is enforced |
|------|------------------|-------------------|
| `implementer` | Incorrect implementation, missed requirements, off-scope edits | `Write`/`Edit` limited to `allowed_paths`; `Bash` denied; isolated worktree |
| `test-writer` | Missing tests, weak edge coverage, brittle contracts | `Write`/`Edit` limited to `test_paths`; `Bash` denied; isolated worktree |
| `skeptic` | Anchoring bias, rollback risk, hidden coupling | Read-only tools only; `Bash` limited to read-only inspection; blind-review context |
| `security` | Auth bypass, injection, secrets exposure, fail-open logic | Read-only tools; `Bash` limited to read-only plus `security_scan_commands` |
| `performance` | N+1 queries, algorithmic blowups, latency regressions | Read-only tools; `Bash` limited to read-only plus `benchmark_commands`; isolated worktree |
| `accessibility` | Keyboard traps, missing labels, semantic and contrast failures | Read-only tools; `Bash` limited to read-only plus `a11y_commands` |
| `docs` | Stale setup steps, undocumented behavior, reader dead ends | `Write`/`Edit` limited to `doc_paths`; `Bash` denied; isolated worktree |
| `verifier` | Confidence without proof, untested acceptance criteria | Read-only tools; `Bash` limited to read-only plus `verification_commands`; isolated worktree |

### The Phase Flow

```text
plan -> implement -> test -> audit -> verify -> docs -> close
                          ^                    |
                          |---- loop on failure|
```

`plan` defines the mission: objective, allowed paths, acceptance criteria, and approved commands. `implement` makes the code change. `test` writes or updates tests, and in `--tdd` mode must happen before implementation. `audit` runs skeptic, security, performance, and accessibility in parallel, each writing a structured result. `verify` checks every acceptance criterion using only approved commands. `docs` updates user-facing material. `close` marks the mission `pass` or `abort`.

Legacy sequential phases remain supported: `review`, `security`, `performance`, and `accessibility` can still be driven one at a time when a workflow needs them.

### Blind Review

The skeptic sees the objective, acceptance criteria, scope rules, and changed paths -- but not the implementer's narrative or summary. This is implemented by context shaping at the `SubagentStart` hook, not by trust. The skeptic forms an independent opinion because it literally does not have the implementer's framing in its context window.

### Why Hooks, Not Just Prompts

A prompt can ask the security auditor to stay read-only. The hook prevents writes even if the prompt is ignored, overridden, or the model decides it knows better. This matters because the failure mode of prompt-only enforcement is silent: the model drifts out of scope and nobody notices until the damage is in the diff.

The hook layer intercepts at four points:

| Hook | What it enforces |
|------|-----------------|
| `SubagentStart` | Injects role context, blind-review barriers, REVIEW.md criteria |
| `PreToolUse` | Blocks out-of-scope writes, unapproved Bash, path violations |
| `PostToolUse` | Records every tool action to the evidence ledger |
| `SubagentStop` | Requires structured result block before the role can finish |

An additional `Stop` hook prevents the session from ending while required audit results are still missing.

## Why These Roles

Each role is modeled on how experienced practitioners narrow their failure surface:

- **Test writer** -- Kent Beck's test-first discipline, Google's testing guidance on size and scope, Martin Fowler's test pyramid.
- **Security auditor** -- Trail of Bits' adversarial methodology, OWASP Top 10:2025, Cure53-style severity framing.
- **Performance profiler** -- Brendan Gregg's USE Method, Google SRE production thinking, perf-sheriff regression discipline.
- **Accessibility checker** -- WebAIM Million 2025, Deque axe-core, WCAG 2.1 AA, Apple Human Interface Guidelines.
- **Docs writer** -- Stripe's task-oriented quality bar, Google's developer documentation style guide, Divio/Diataxis.

## Configuration

### collabctl CLI Reference

| Command | What it does |
|---------|---------------|
| `init` | Creates a mission manifest. Accepts `--objective`, `--allowed-path`, `--denied-path`, `--test-path`, `--doc-path`, `--criterion`, `--verify-command`, `--security-command`, `--benchmark-command`, `--a11y-command`, `--tdd`, `--timeout-hours`, `--max-loops`, `--dry-run`, and `--custom-role`. |
| `show` | Prints the active manifest and file locations as JSON. |
| `phase <name>` | Advances the mission with transition validation. Use `--force` to bypass phase rules. |
| `progress` | Appends a progress note to the mission log. |
| `close pass\|abort` | Closes the active mission and clears the active pointer. |
| `ledger-trim` | Archives older ledger entries, keeps the most recent N. |
| `migrate` | Migrates the active mission to the latest schema version. |
| `verify` | Verifies repo layout, hooks, commands, adapters, and installer. |

### Custom Roles

`--custom-role` adds a manifest-defined role without changing the core engine. Supported scope types: `read_only`, `write_allowed`, `write_test`, and `write_doc`.

```bash
python3 scripts/collabctl.py init \
  --objective "Run lint review" \
  --allowed-path src \
  --criterion "Lint review completes" \
  --verify-command "pytest -q" \
  --custom-role "name=linter,scope=read_only,commands=eslint src/"
```

Custom roles participate in wildcard `collab-*` handling, inherit mission context, and use a generic result schema requiring `role`, `summary`, and either `status` or `recommendation`.

### TDD Mode

`--tdd` changes the phase order from `plan -> implement -> test` to `plan -> test -> implement`. The hook layer then blocks implementer writes until the current loop has a valid `test-writer` result.

### REVIEW.md

If a project root contains `REVIEW.md`, the first 2000 characters are injected into skeptic, security, and verifier context at subagent start. Use it for project-specific review criteria, operational constraints, or release gates.

## Architecture

![Architecture](docs/images/architecture.png)

Mission state lives under the Git common directory, not the working tree. That keeps the coordinator, the root checkout, and any isolated worktrees pointed at the same manifest, ledger, snapshots, and per-role result files.

Prompts and hooks play different roles. Prompts tell a model how to behave. Hooks decide what it is allowed to do. The prompt can ask the skeptic to stay read-only; the hook can prevent writes even if the prompt is ignored.

Worktree isolation is used where incidental writes or tool artifacts would otherwise leak across roles.

## Platform Support

| Platform | Status | Scope Enforcement |
|----------|--------|-------------------|
| Claude Code | Full (GA) | Hook-level (all tools) |
| Copilot CLI | Full (GA) | Hook-level (all tools) |
| Codex CLI | Experimental | Hook-level (Bash only), prompt-level (Write/Edit) |

## Testing

96 tests cover all 8 roles, scope enforcement, result validation, lifecycle management, state handling, schema migration, the collabctl CLI, parallel audit phase, TDD hook enforcement, and mission resilience (timeout, stale warning, failure tracking, REVIEW.md, dry-run). Stdlib only, no external dependencies.

```bash
python3 -m pytest tests/ -q
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/8eyes` does nothing | Platform adapter not installed | Run `python3 install.py` inside a Git repo |
| `No active /collab mission.` | No mission initialized or previous one closed | Start with `/8eyes ...` or `collabctl init ...` |
| Implementer writes denied | File outside `allowed_paths` or under `denied_paths` | Add `--allowed-path` entries at init time |
| Test writer cannot edit source | `test-writer` limited to `test_paths` | Move source edits to `implement` phase |
| Docs writer blocked on a Markdown file | File outside `doc_paths` | Add `--doc-path` at init |
| Implementer blocked in TDD mode | No current-epoch `test-writer` result | Complete the `test` phase first |
| Bash denied for audit role | Command not in read-only set or allowlist | Add via `--security-command`, `--benchmark-command`, `--a11y-command`, or `--verify-command` |
| Phase transition rejected | Illegal move for current mode | Follow the phase table, or `--force` to override |
| Stop hook blocks session exit during audit | Missing audit results | Wait for all four roles or set `awaiting_user=true` |
| Stale mission warning | Mission older than 12 hours | Run `collabctl show`, then continue, close, or reinitialize |
| Codex CLI does not block write/edit scope drift | Current Codex hooks enforce Bash only | Treat Codex as experimental; inspect ledger and verifier output |

## Requirements

- Python 3.10+
- Git
- One or more: Claude Code, Copilot CLI, Codex CLI

## License

MIT
