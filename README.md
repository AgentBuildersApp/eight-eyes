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

### Solo developer workflows

You are the only developer on the project. You wrote the code, you wrote the tests, you reviewed the diff. Everything looks correct because you are the same person who made every assumption baked into it. `eight-eyes` gives you eight reviewers who did not write the code and cannot see each other's notes.

You are refactoring a module that the rest of your app depends on. You are confident the behavior is preserved, but confidence is not proof. The verifier role runs your acceptance criteria against the actual code and reports what passes and what does not -- with curl output, test results, or whatever commands you approved at init time.

You are upgrading a major dependency -- React 18 to 19, Django 4 to 5, a new ORM version. The change touches dozens of files and the migration guide says "most apps won't need changes." The skeptic reviews blind, without your optimism. The performance role benchmarks before and after. The test writer checks whether your existing tests actually cover the changed APIs.

You have accumulated a backlog of TODO comments and suppressed linter warnings. You want to burn through the tech debt in a focused session but you do not trust yourself to avoid introducing regressions while fixing old ones. `eight-eyes` scopes the implementer to the paths you choose, and the skeptic and verifier independently confirm that each fix did not break something adjacent.

### Team workflows

Your team does code review, but the reviews are mostly "LGTM" with a rubber-stamp approval. The skeptic role exists specifically for this: it reviews the change blind, without the author's narrative, and has no ability to edit files -- only to find problems.

A new developer joined the team last week and is about to merge their first real PR. You want to give thorough feedback without spending two hours on it yourself. `eight-eyes` runs the full eight-role review and produces structured findings with file paths and evidence. You read the summary, add your own context, and the new developer gets a detailed review in minutes instead of days.

Your team has a style guide and architectural conventions, but they live in a wiki that nobody reads during code review. You put the rules in `REVIEW.md` at the project root. The skeptic, security, and verifier roles receive those rules as part of their context and check the change against them every time -- not just when someone remembers.

It is the end of the sprint and there are six PRs waiting for review. Nobody wants to be the bottleneck. You run `/8eyes` across the batch. Each PR gets the same structured multi-role review regardless of how tired the team is on Friday afternoon.

### Security-critical work

You are building authentication, payment flows, or anything where a missed edge case has real consequences. The security role is constrained to read-only tools plus approved scan commands -- it reviews like an external auditor, not a collaborator who might "fix" things and introduce new issues.

You are implementing a secrets management integration and you need to verify that no credentials leak into logs, error messages, or API responses. The security role scans for exactly this. It cannot edit code, so it cannot accidentally "fix" a leak by deleting the log line and hiding the problem.

You store API keys, tokens, or PII and you are about to change how they are accessed. The security role checks for exposure in the diff -- hardcoded values, keys in query strings, tokens in error responses. The verifier confirms that the access pattern works as intended. Neither role sees the other's findings until adjudication, so they cannot anchor on each other's framing.

### Quality gates

You are shipping code to a client and need to show that a security review, accessibility check, and test coverage pass actually happened. Every role writes a structured result with evidence, file paths, and a recommendation. The mission ledger is an auditable record of what was checked and what was found.

You want to add a review gate to your CI pipeline that goes beyond linting and test passes. `eight-eyes` missions produce machine-readable JSON results. The `collabctl show` output gives you a per-role pass/needs_changes/abort status that a CI step can check. If any role flags the change, the pipeline fails with specific findings, not a generic "review required" label.

You are preparing a release candidate and you want a final sweep before tagging. Run `/8eyes` with the release objectives as acceptance criteria. The verifier checks each criterion with the commands you approve. The skeptic looks for things that will break in production but pass in dev. The docs role confirms that CHANGELOG, README, and API docs reflect the actual state of the release.

### AI-assisted development

Your team adopted an AI coding agent and the code it produces is technically correct but nobody is reviewing it with adversarial intent. `eight-eyes` runs the same kind of structured review on AI-generated code that you would run on a junior developer's first PR -- except it cannot be talked out of its concerns.

You used Copilot or Cursor to generate a module and it looks reasonable, but you have a nagging feeling that the generated code is subtly wrong in ways you cannot articulate. The skeptic reviews the change blind -- no knowledge of what the AI was asked to do, just the code on disk and the acceptance criteria. The security role checks for patterns that AI code generators frequently get wrong: overly permissive defaults, missing input validation, hardcoded fallbacks.

You are building an agentic workflow where one AI writes code and another is supposed to review it, but the reviewer keeps agreeing with the author. `eight-eyes` breaks this dynamic because the skeptic literally cannot see the author's narrative. Hook enforcement means agreement requires independent evidence, not social compliance.

### Legacy and migration

You inherited a codebase with no tests, unclear boundaries, and a framework two major versions behind. You need to modernize incrementally without a rewrite. Run `/8eyes` with `--tdd` on each migration step. The test writer creates tests for the current behavior before you touch it. The implementer makes the change. The verifier confirms the tests still pass. The skeptic checks whether the migration introduced hidden coupling.

You are changing your database schema -- adding columns, renaming fields, migrating data. The verifier runs your migration scripts against a test database using the commands you approved. The security role checks that the migration does not expose data through new API fields. The skeptic asks whether rollback is possible and what happens to in-flight requests during the migration.

You are replacing an internal API with a third-party service. The old code is well-understood. The new integration is not. The performance role benchmarks latency and throughput against the old implementation. The test writer covers the contract boundary. The verifier confirms the switchover works end to end.

### Open source

You maintain an open source project and receive a pull request from a contributor you have never worked with. You need to check it thoroughly but do not have time to trace every code path. The eight roles split the review surface so that security, performance, correctness, and accessibility are each examined by a reviewer that cannot be distracted by the others.

You are cutting a release of a library that other projects depend on. The diff is large and touches public API surface. The docs role checks whether the API documentation matches the actual exports. The verifier runs your compatibility test suite. The skeptic looks for breaking changes that the test suite does not cover. The structured results give you a release-readiness report, not just a gut feeling.

### Compliance and audit

You need to demonstrate to an auditor that code changes go through a documented review process. `eight-eyes` produces timestamped, structured evidence per role -- what was reviewed, what was found, what passed, what was flagged. The mission ledger is append-only. Every tool action is recorded by the PostToolUse hook. This is not a checkbox. It is a machine-readable audit trail.

You keep shipping accessibility regressions because nobody catches them until a user reports them. The accessibility role runs axe-core or whatever tools you approve, checks semantic HTML, keyboard navigation, and contrast -- every time, not just when someone remembers to.

You have a compliance requirement that security-sensitive changes get a second set of eyes. You run the same `/8eyes` mission you would run for any change, but the security role's structured findings with severity ratings and CVE references give the compliance team exactly what they need -- without scheduling a meeting or writing a separate report.

### Education and learning

You are learning a new framework and you wrote your first real feature in it. You want feedback, but not from a tutorial. The eight roles review your code the way experienced practitioners would. The security role flags patterns that look fine in a tutorial but break in production. The performance role finds the N+1 query you did not know you wrote. The findings link to specific lines, not generic advice.

You are mentoring a junior developer and you want them to experience what a thorough review feels like before they join a team with real stakes. Run `/8eyes` on their project. The structured findings become a teaching syllabus: here is what the security role found, here is why, here is how to fix it. The junior developer learns from eight perspectives, not just yours.

### Custom role scenarios

Your project has i18n requirements and you need to verify that every user-facing string goes through the translation layer. You define a custom role: `--custom-role "name=i18n-checker,scope=read_only,commands=grep -r 'hardcoded string' src/"`. It participates in the audit phase alongside the other roles, with the same structured result format and the same scope enforcement.

You have license compliance requirements -- every dependency must be Apache-2.0, MIT, or BSD. You define a custom role that runs your license checker. It cannot edit files. It reports findings in the same schema as every other role. The verifier confirms the findings are addressed before the mission can close.

You maintain an API and you need to verify that every endpoint matches the OpenAPI spec. A custom verifier role runs your contract testing tool. The skeptic reviews the spec itself for ambiguity. The docs role updates the spec if the implementation intentionally diverged. Three roles, one consistent API contract.

### High-stakes environments

You are refactoring a payment processor. The implementer is scoped to `src/payments/` and `src/utils/` but `src/fraud/` and `src/compliance/` are denied paths — the AI literally cannot touch fraud detection logic. The security auditor runs `npm audit` and `snyk test` before the mission can close. The skeptic verifies that fraud logic is unchanged without knowing what the implementer intended.

You are updating a patient portal UI in a healthcare application. The implementer is scoped to `frontend/` and `tests/fixtures/mock/` — it cannot reach `backend/`, `db/`, or `config/secrets`. The security auditor scans commits for PHI patterns. The verifier runs a regex sweep for SSN and DOB strings in test fixtures. None of these checks are optional.

A junior developer and an AI agent are adding OAuth2 login together. The implementer can write to `src/auth/oauth/` and `tests/auth/` but `src/middleware/` is a denied path — the AI cannot modify the authentication middleware. The security auditor runs `semgrep --config=auth` and must find zero issues. The blind skeptic checks for middleware changes and session handling without knowing what the AI claimed to do.

You run a nightly CI job that updates dependencies and fixes deprecations. The implementer can modify `package.json`, `package-lock.json`, and `src/`. The verifier must pass unit, integration, and e2e test suites — not just unit tests. The security auditor blocks on moderate-or-higher vulnerabilities from `npm audit`. The mission produces an auditable record of every check that ran.

Two developers are running AI agents simultaneously — one fixing API rate limiting, the other optimizing database queries. Each mission scopes its implementer to different paths. `src/db/connection.js` is a denied path for both. The agents cannot create merge conflicts in critical shared files because the hook layer enforces path separation at tool time.

## Quick Start

Inside the Git repo you want to review:

```bash
git clone https://github.com/AgentBuildersApp/eight-eyes.git
cd eight-eyes
python3 install.py
```

The installer detects which platforms are available and sets up the right adapter. To target a single platform:

```bash
python3 install.py --platform claude_code
python3 install.py --platform copilot_cli
python3 install.py --platform codex_cli
```

Then, from your project directory:

```bash
/8eyes Refactor auth to use JWT
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
