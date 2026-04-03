# Research Gate Runtime Contract

This document summarizes the runtime contract for `/collab` Stage 0 research
classification. The source of truth remains [skills/collab/SKILL.md](../skills/collab/SKILL.md).

## Purpose

The research gate forces the coordinator to classify work before implementation
and blocks meaningful implementation when required research has not been
completed and recorded.

## Stage 0 Inputs

The coordinator may classify work during `collabctl init` with:

- `--domain`
- `--action-type`
- `--risk`
- `--root-cause-clarity`
- `--fix-path-clarity`
- `--verification-clarity`
- `--prior-pattern-match`
- `--environmental-stability`
- penalty flags for architecture, security/data, cross-module changes,
  ecosystem churn, and weak observability

If those inputs are omitted, the mission initializes with
`research_gate.status = "incomplete"` and cannot advance to implementation until
the gate is fully defined and approved.

## Score Model

Confidence is computed from five 0..2 factors and explicit numeric penalties:

- 8..10 => `skip`
- 5..7 => `targeted`
- 0..4 => `broad`

Overrides raise the minimum research mode when the work is architectural,
security-sensitive, medium-or-higher risk, or follows a previous failed attempt.

## Manifest Contract

Each mission stores a `research_gate` section with:

- classification metadata
- factor and penalty breakdowns
- final confidence score
- selected research mode
- override reasons
- rationale
- reviewed sources
- research artifacts
- completion status

Plan-phase approval is recorded structurally in `buyoffs[]`, not as free-form
progress text.

## Enforcement

`collabctl phase implement` fails closed when:

- the research gate is missing or incomplete
- the plan buyoff is missing
- required research has not been completed

The runtime hook in `hooks/scripts/collab_pre_tool.py` also denies implementer
write/edit operations when research is still required but unsatisfied. This
prevents direct mission-state drift from bypassing the gate.

## Audit Expectations

Audit roles receive research context in their injected mission summary:

- skeptic checks whether skip was granted incorrectly or confidence was inflated
- security checks whether risk classification and required research were correct
- verifier checks whether cited sources materially influenced implementation or
  verification decisions

## Completion Rules

For `skip`, the mission still requires a complete rationale, confidence
breakdown, and recorded plan buyoff.

For `targeted` or `broad`, the mission requires recorded sources and an explicit
research completion step before implementation can proceed.
