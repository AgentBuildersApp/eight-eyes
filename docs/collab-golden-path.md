# /collab Golden Path

This is the canonical operator runbook for `/collab`. It is the fastest
complete path from mission start to mission close, including research gating,
buyoff, audit, loopback, and final verification.

Policy source of truth:

- [skills/collab/SKILL.md](../skills/collab/SKILL.md)

Runtime contract summaries:

- [docs/research-gate.md](./research-gate.md)
- [commands/8eyes.md](../commands/8eyes.md)

## 1. Start The Mission

```bash
python3 scripts/collabctl.py init \
  --objective "Ship the requested change" \
  --allowed-path src \
  --allowed-path tests \
  --criterion "Behavior matches the requested objective." \
  --criterion "Relevant tests pass." \
  --verify-command "python3 -m pytest -q" \
  --domain backend \
  --action-type fix \
  --risk low \
  --root-cause-clarity 2 \
  --fix-path-clarity 2 \
  --verification-clarity 2 \
  --prior-pattern-match 1 \
  --environmental-stability 2 \
  --research-rationale "Known low-risk change with clear verification."
```

Immediately inspect state:

```bash
python3 scripts/collabctl.py status
python3 scripts/collabctl.py show
```

## 2. Record Research And Plan Buyoff

If the research gate says `skip`, record plan buyoff:

```bash
python3 scripts/collabctl.py buyoff plan \
  --objective "Ship the requested change" \
  --recommendation approve
```

If the gate says `targeted` or `broad`, attach sources and artifacts first:

```bash
python3 scripts/collabctl.py research add-source \
  --title "Primary source" \
  --kind local_doc \
  --location "skills/collab/SKILL.md"

python3 scripts/collabctl.py research add-artifact \
  --path "docs/research-notes.md" \
  --kind notes

python3 scripts/collabctl.py research complete

python3 scripts/collabctl.py buyoff plan \
  --objective "Ship the requested change" \
  --recommendation approve_with_research
```

## 3. Run The Mission

Manual phase sequence:

```bash
python3 scripts/collabctl.py phase implement --awaiting-user false
python3 scripts/collabctl.py phase test --awaiting-user false
python3 scripts/collabctl.py phase audit --awaiting-user false
python3 scripts/collabctl.py phase verify --awaiting-user false
python3 scripts/collabctl.py phase docs --awaiting-user false
python3 scripts/collabctl.py close pass --reason "Mission completed successfully."
```

During the role workflow:

- implementer writes code or confirms no change is needed
- test-writer records test coverage
- skeptic, security, performance, and accessibility run in parallel during `audit`
- verifier checks acceptance criteria and research trace
- docs updates operator-facing guidance only when needed

After each phase:

```bash
python3 scripts/collabctl.py status
python3 scripts/collabctl.py timeline
```

## 4. Loopback Rule

If any audit or verify role returns `needs_changes`, `fail`, `abort`, or
`blocked`, loop back to `implement` and rerun downstream phases:

```bash
python3 scripts/collabctl.py phase implement --awaiting-user false
python3 scripts/collabctl.py phase test --awaiting-user false
python3 scripts/collabctl.py phase audit --awaiting-user false
python3 scripts/collabctl.py phase verify --awaiting-user false
```

Do not close the mission while any downstream role is stale relative to the
current loop epoch.

## 5. One-Command Smoke Validation

Use the built-in deterministic mission runner when you need to validate
mission-state orchestration and report generation rather than a live code change.

Precondition:

- no other `/collab` mission may be active when you start a smoke run

```bash
python3 scripts/collabctl.py smoke --scenario clean-pass
python3 scripts/collabctl.py smoke --scenario audit-loop
```

Scenarios:

- `clean-pass`: one-pass mission lifecycle with structured results and report
- `audit-loop`: forces one audit loopback before converging to pass

Each smoke run:

- initializes a mission
- records buyoff
- advances phases in order
- persists deterministic, schema-validated fixture results
- writes `summary.md` into the mission directory
- closes the mission as `pass`

What smoke does validate:

- phase sequencing
- loopback handling
- structured result persistence
- report generation
- closeout behavior

What smoke does **not** validate:

- live subagent dispatch
- hook-mediated runtime enforcement paths
- real role prompt quality

## 6. Live Controller

Use the live mission controller when real named `/8eyes` roles are running and
you want `/collab` to converge the mission automatically instead of advancing
phases by hand.

```bash
python3 scripts/collabctl.py run --json
python3 scripts/collabctl.py run --watch --timeout-seconds 300 --json
```

The live controller does four things:

- reports the exact pending roles and named agents to dispatch next
- watches for schema-valid role results on the active mission
- auto-advances `implement -> test -> audit -> verify -> docs`
- loops back to `implement` automatically when audit or verifier results request changes

What the live controller does **not** do:

- spawn host subagents by itself
- bypass plan approval or research-gate requirements
- fabricate role results

## 7. Final Cleanliness Check

Before calling the repo clean:

```bash
python3 -m pytest tests/test_collab_hooks.py -q
python3 -m pytest -q
git status --short
```

Success criteria:

- test suite is green
- no active `/collab` mission remains unless intentionally left open
- `git status --short` is empty
