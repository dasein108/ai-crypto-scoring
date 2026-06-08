# Scoring Overview — four systems, four questions

`token-research` ships **four separate assessment systems**. They look similar (each spits out a number) but they answer different questions over different time horizons. Don't average them together and don't read one as a proxy for another.

| System (command) | Question it answers | Output shape | Horizon | Best used for |
|---|---|---|---|---|
| **`score`** | Is this a *structurally* healthy token? | Composite **0–100** over 6 weighted pillars | Structural / slow-moving | A standing quality read on a live token |
| **`risk`** | How reliable is the chain + protocol + dapp + team + code? How likely to fail/rug? | CPD 2.0 **risk %** + tier | Structural / slow-moving | Due diligence, "should I trust this at all?" |
| **`signal`** | Should I enter or exit *now*? | Trader composite **[−10, +10]** | Momentary / fast | Timing, momentum, flow direction |
| **`fairlaunch`** | Was the token *launched* fairly? | **15-point** distribution-fairness rubric | One-time (launch) | Judging TGE/distribution fairness |

---

## `score` — 6-pillar structural composite

`score` runs `supply`, `holders`, `liquidity`, `locks`, `staking`, `yields`, and `compare` internally, then computes a weighted composite in `[0, 100]`. A **coverage-ratio penalty** (−15 points × (1 − coverage_ratio)) docks the score when inputs are missing, so an unscoreable token can't look healthy by default.

Pillars and weights (from `commands/score.py:PILLAR_WEIGHTS`, sum = 1.0):

| Pillar | Weight | Measures |
|---|---|---|
| `supply_pressure` | 0.22 | Protocol-owned %, revenue model, unlock overhang |
| `ownership_quality` | 0.22 | EOA-only effective top-10 share, nakamoto, gini (protocol custody excluded) |
| `liquidity_quality` | 0.22 | DEX liquidity tier, venue concentration, LP-lock bonus |
| `fundamental_support` | 0.08 | TVL tier, mcap/TVL efficiency, chain diversity, fees/mcap |
| `governance_commitment` | 0.13 | Fingerprinted vesting/staking + generic protocol-owned supply + custody share |
| `token_capture` | 0.13 | VC-lens: does the *token itself* accrue protocol economics (holder accrual), separate from raw TVL/fees |

**Does not measure:** price direction, timing, or launch fairness. A high `score` says the token is well-structured *today*, not that it's a good entry.

→ Reach for `score` when you want one standing number for structural quality. See the pillar logic in [cli-reference.md](cli-reference.md) and the conceptual ancestor in [domains/fair-value-and-risk-scoring.md](domains/fair-value-and-risk-scoring.md).

---

## `risk` — CPD 2.0

`risk` applies the **CPD 2.0** protocol (Chain / Protocol / Dapp, plus stage, age, code quality, and incident history) to produce a reliability/risk percentage and tier. It is evidence-driven: each sub-score is meant to be backed by sourced findings.

**Does not measure:** market quality, liquidity depth, or momentum. It's a "can I trust this stack and team?" read, not a "is the market healthy?" read.

→ Reach for `risk` for due diligence and rug/failure likelihood. Full protocol in [risk_revenue_estimation.md](risk_revenue_estimation.md). The companion **`cpd-crypto-analysis` skill** (`.claude/skills/cpd-crypto-analysis/`) drives the same framework with sourced research and a scorecard.

---

## `signal` — trader composite [−10, +10]

`signal` combines `compare`, `flows`, and `trend` deltas into a single momentary score in `[−10, +10]`. It **requires trend history** (run `trend` over time first) to compute the momentum component — without prior snapshots the momentum leg is muted and a coverage gap is emitted.

**Does not measure:** structural quality or trust. A token can have a strong positive `signal` (good flows/momentum now) while scoring poorly on `score`/`risk`, and vice-versa.

→ Reach for `signal` for entry/exit timing. Framework in [trend_signals.md](trend_signals.md).

---

## `fairlaunch` — 15-point fairness rubric

`fairlaunch` scores how fairly a token was distributed at launch using a 15-point rubric (modeled on [fairlaunch.org](https://fairlaunch.org/)). It runs `supply`, `holders`, `locks`, `staking`, and `compare` to check insider allocation, lock discipline, and concentration at distribution.

**Does not measure:** ongoing health or price. It's a one-time verdict on the *launch event*, not the live token.

→ Reach for `fairlaunch` when judging a TGE/distribution. Rubric documented in [cli-reference.md](cli-reference.md).

---

## Decision guide

| You want to know… | Use |
|---|---|
| Is this a structurally healthy token? | `score` |
| How likely is this to rug? How reliable are the team + code + stack? | `risk` (+ the `cpd-crypto-analysis` skill for sourced DD) |
| Should I enter/exit right now? | `signal` (needs `trend` history) |
| Was the token launched fairly? | `fairlaunch` |
| The full picture in one shot | `deep-report` (bundles `score` + most collectors into one dossier) |
