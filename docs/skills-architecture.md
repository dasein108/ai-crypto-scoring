# Skills Architecture — crypto research from outside

How the agent-facing **skills** wrap the `token-research` project for external crypto research, what modes/purposes they serve, and how a request gets routed to the right one.

> **Status: Live design.** Describes the skill set under `.claude/skills/` and the routing contract between them.

## The two engines

There are two independent research engines, and a good skill set routes between them instead of duplicating either:

| Engine | What it is | Strength | Boundary |
|---|---|---|---|
| **`token-research` CLI** | ~30 commands over on-chain + free-API data | Canonical on-chain truth, deterministic, structured output, quant scoring | EVM-only, token-centric; refuses non-EVM / pre-TGE |
| **Web-research CPD** | Evidence-first sourced scoring (WebFetch/WebSearch + scorer scripts) | Works *anywhere* — non-EVM, pre-TGE, qualitative trust | Numbers are sourced claims, not on-chain truth |

They are complementary, not competing: the CLI is strongest exactly where the web engine is weakest (hard on-chain numbers) and refuses exactly where the web engine still works (HBAR, Katana). A complete report uses **both** — the CLI for the numbers, CPD for the sourced trust narrative.

## Three axes (don't collapse them)

A single skill that "analyzes a crypto project" hides three independent decisions:

### Axis 1 — Purpose (what question)

| Purpose | Command(s) | Detail |
|---|---|---|
| Trust / rug risk | `risk` (CPD 2.0) + web CPD skill | [risk_revenue_estimation.md](risk_revenue_estimation.md) |
| Structural quality | `score` (6-pillar) | [scoring-overview.md](scoring-overview.md) |
| Entry / exit timing | `signal`, `momentum-*` | [trend_signals.md](trend_signals.md) |
| Launch fairness | `fairlaunch` | 15-point rubric |
| Portfolio construction | `portfolio-build` | [strategies/](strategies/) |
| Head-to-head | `compare`, `screen` | derived views |

### Axis 2 — Mode (depth vs cost)

| Mode | Shape | When |
|---|---|---|
| **quick** | one or two commands (`risk`, `score`), seconds | triage, a fast read |
| **deep** | `deep-report` (all modules + narrative) | a real dossier |
| **sourced** | web-research CPD (the `cpd-crypto-analysis` skill) | trust DD, or when on-chain isn't enough |
| **orchestrated** | fan out commands concurrently, then verify | comprehensive review (see [orchestration article](../articles/02-agent-orchestration.md)) |

### Axis 3 — Data regime (the routing key)

This is the axis that picks the engine:

```
Is it an EVM token with a known/derivable address?
 ├─ yes → token-research CLI skill (on-chain truth)
 └─ no  → web-research CPD skill
            (pre-TGE program, non-EVM L1, or a purely qualitative trust question)
```

The CLI's documented refusals (non-EVM like HBAR, pre-TGE like Katana) are not failures — they are the **hand-off signal** to the web engine.

## The skill set

| Skill | Engine | Covers | Modes |
|---|---|---|---|
| **`token-research`** (new) | CLI | EVM token on-chain analysis | quick · deep · compare |
| **`cpd-crypto-analysis`** (existing) | Web research | trust DD, non-EVM, pre-TGE | standard · stablecoin (ADP) |

### Routing rule (apply first, every time)

1. **EVM token + address available** → `token-research` skill. Pick mode by purpose: triage → `quick`; full dossier → `deep`; two+ tokens → `compare`.
2. **Pre-TGE / non-EVM / off-chain trust question** → `cpd-crypto-analysis` skill.
3. **Both needed** (e.g. "is MORPHO safe *and* well-governed?") → run the CLI skill for the numbers, then the CPD skill for the sourced trust layer, and merge. The CLI's `risk` output and the CPD score should agree; where they diverge, that divergence is itself a finding.

## De-duplicating CPD (the drift trap)

Both engines implement the **same CPD framework** — the CLI in code (`risk` / `risk_model.py`), the skill manually from sources. That is two implementations of one model, and two implementations drift (exactly the 5-vs-6-pillar bug from the [orchestration article](../articles/02-agent-orchestration.md)).

**Mitigation:** both cite **one source of truth** — [`risk_revenue_estimation.md`](risk_revenue_estimation.md) — for tiers, bands, and thresholds. The skill's scorer (`score_cpd.py`) and the CLI's `risk_model.py` should encode the same numbers, and any change lands in the doc first. Treat the doc as the schema; the two engines are conformant implementations.

## Why this shape

- **Specialize, don't merge.** One mega-skill that tries to be both on-chain and web-sourced ends up mediocre at both and impossible to route. Two focused skills with a clear hand-off beat one ambiguous one.
- **Route by data regime, not by purpose.** Purpose picks the *command*; the data regime picks the *engine*. Getting this order right is what makes "research this token" resolve cleanly whether the token is MORPHO (EVM) or HBAR (non-EVM).
- **Compose for completeness.** The strongest reports use both engines and reconcile them. Agreement raises confidence; divergence is a finding.

## Related

- The new skill: [`.claude/skills/token-research/SKILL.md`](../.claude/skills/token-research/SKILL.md)
- The existing skill: [`.claude/skills/cpd-crypto-analysis/SKILL.md`](../.claude/skills/cpd-crypto-analysis/SKILL.md)
- Command surface: [cli-reference.md](cli-reference.md) · Scoring systems: [scoring-overview.md](scoring-overview.md)
