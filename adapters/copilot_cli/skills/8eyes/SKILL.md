---
name: 8eyes
description: Multi-agent adversarial code review via eight-eyes enforcement kernel
triggers: ["/8eyes"]
version: "4.2.0"
author: "AgentBuildersApp"
category: "review"
tags: ['#code-review', '#multi-agent', '#enforcement']
---

# /8eyes - Multi-Agent Code Review

**Trigger**: `/8eyes [target]`
**Version**: 4.2.0 — Verifiable Enforcement (installed plugin version)

## Overview

Eight constrained reviewer roles with hook-enforced isolation. Prevents AI reviewer consensus failure through architectural enforcement.

## Roles

<!-- VON-1408 P2 roster shifts 2026-05-02 — min-1-Eastern requirement + Kimi role eligibility -->

| Role | Focus | GLM 5.1 Eligible? | Kimi K2.6 Eligible? |
|------|-------|-------------------|---------------------|
| Implementer | Code correctness, logic | YES (alt — opt-in) | NO (default vendor stays) |
| Test Writer | Test coverage, edge cases | YES (alt — opt-in) | NO (default vendor stays) |
| Skeptic | Adversarial review, assumptions | NO (MiniMax canonical Skeptic) | NO (MiniMax canonical Skeptic) |
| Security | Vulnerabilities, auth, secrets | **NO — HARD EXCLUSION** | **NO — HARD EXCLUSION** |
| Performance | Bottlenecks, complexity | NO (default vendor stays) | NO (default vendor stays) |
| Accessibility | WCAG compliance, UX | NO (default vendor stays) | NO (default vendor stays) |
| Docs | Documentation completeness | NO (default vendor stays) | NO (default vendor stays) |
| Verifier | Final sign-off, integration | NO (default vendor stays) | NO (default vendor stays) |
| Long-Context Reviewer (opt-in) | Cross-file architectural review when target spans 5-15 files | YES — GLM 200K context lane | YES — Kimi long-horizon lane |
| Architect-alt (opt-in) | Cross-module architectural reasoning, large-design review | NO (default vendor stays) | YES — Kimi long-context architect lane |

### Min-1-Eastern Requirement (VON-1408 P2)

When invoking /8eyes by default (no operator-supplied `--role` overrides), **at least 1 of the 8
default reviewer roles MUST be filled by an Eastern model (Kimi, MiniMax, or GLM)**, unless the
governance-locked hard exclusions apply (see below). This requirement is satisfied automatically
because MiniMax already canonically fills the Skeptic role; the requirement only forces visible
review when an operator attempts to override the default skeptic with a Western model.

**Hard exclusions UNCHANGED — these roles can NEVER be filled by an Eastern model**:
- **Security role** — no published Eastern-vendor security benchmarks; new-vendor track records short
- **Threat Modeler** (any sub-role of Skeptic that performs threat modeling)
- **Customer-data paths** (any review touching Vettara/Attestra T1 customer data)
- **Billing-critical or auth-critical code paths**

When the target review intersects a hard-exclusion lane, /8eyes routes the affected role(s) to a
Western model and the min-1-Eastern requirement is waived for the affected portion.

<!-- VON-1399 GLM integration 2026-05-01 -->
### GLM 5.1 Role-Fill Policy

GLM 5.1 is OPT-IN as an alternative role-fill where MiniMax/Codex would otherwise be reused. Default reviewer roster is unchanged — GLM is invoked only when explicitly selected.

**GLM eligible roles**: Implementer-alt, Test Writer-alt, Long-Context Reviewer.

**GLM HARD EXCLUSIONS (per /collab consensus 2026-05-01 Q1, REAFFIRMED VON-1408 P2)**:
- **Security role** — no published GLM security benchmark; new-vendor track record short
- **Threat Modeler** (any sub-role of Skeptic that performs threat modeling)
- **Any review touching Vettara/Attestra customer data** — GLM is T2/T4 PERMITTED, NEVER T1 customer data
- **Billing-critical or auth-critical code paths**

GLM is also recused under Law #31 (cross-vendor recusal): GLM cannot review work GLM built. Invocation flows through `~/.claude/session-tools/zai_client.py` (vendor=glm, model=glm-5.1).

### Kimi K2.6 Role-Fill Policy (VON-1408 P2 Q2)

Kimi K2.6 is OPT-IN for two specific roles where its long-context strengths add signal:
- **Long-Context Reviewer** — cross-file architectural review (200K+ context lane, peer to GLM)
- **Architect-alt** — large-design review where Kimi's long-horizon reasoning helps trace
  cross-module dependencies that fit-in-window for fewer Western models

**Kimi HARD EXCLUSIONS (same as GLM, per /collab consensus 2026-05-01 Q1)**: Security role,
Threat Modeler, customer-data paths, billing-/auth-critical code. Kimi is also subject to
Law #31 cross-vendor recusal: Kimi cannot review work Kimi built.

## Usage

```
/8eyes                    # Review current changes
/8eyes src/auth/          # Review specific directory
/8eyes --role security    # Single-role review
```

## Integration

This skill wraps the eight-eyes plugin installed at:
`~/.claude/plugins/cache/8eyes-marketplace/8eyes/4.2.0/`

Installed plugin version: 4.2.0 (per `.claude-plugin/plugin.json`).
Dev source repo: ~/eight-eyes/ (active development; `pytest --collect-only` reports 184 tests).
Note: the installed-plugin version (4.2.0) and the dev-source-repo are distinct — the dev repo may be ahead of the published/installed plugin.

For full documentation, see ~/eight-eyes/README.md

## ALS INTEGRATION (Wave 3)

After completing review, feed top findings to the Adaptive Learning System.

### When to Feed

| Event | Claim Template | Category |
|-------|---------------|----------|
| Review PASS | "8eyes review PASS for [target]: [top strength noted]" | governance |
| Review CONDITIONAL | "8eyes CONDITIONAL for [target]: [conditions from roles]" | governance |
| HIGH finding | "[Role]: [finding] in [target]" | quality |
| Security finding | "Security: [vulnerability] in [target]" | quality |

### How to Feed

After review completes, feed top 3 findings by severity via Bash:

```bash
echo '{"source_skill":"8eyes","claim":"Security: SQL injection vector in user search endpoint via unsanitized query parameter","evidence":"8eyes Security role: user_search.py:45 interpolates req.query directly into SQL. Confirmed by Skeptic role cross-examination.","category":"quality"}' | python3 ~/.claude/session-tools/als_feed.py store
```

### When to Consume

At review start, query known patterns for the target area:

```bash
echo '{"category":"quality","min_confidence":0.50,"limit":5,"exclude_skill":"8eyes"}' | python3 ~/.claude/session-tools/als_feed.py query
```

### Rules
- Feed only HIGH severity findings (not medium/low)
- Maximum 3 learnings per review (top findings only)
- Include which role found it and if cross-examination confirmed it
- Fail-open: if als_feed.py fails, continue /8eyes execution normally
