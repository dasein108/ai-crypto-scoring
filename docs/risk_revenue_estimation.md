# Revenue vs Risk Estimation Protocol (CPD 2.0)

A four-step scoring protocol for weighing the expected return of a crypto
position against its structural risk. Translated and formalised from
`docs/Revenue_vs_Risk_(CPD 2.0).xlsx` and implemented end-to-end in
`src/token_research/risk_model.py` + `src/token_research/commands/risk.py`.

The model produces two numbers that you can actually compare across
positions:

- `final_risk` — an integer 1..10
- `risk_adjusted_apy` — the expected return, discounted by that risk score

Everything else in this document exists to explain how those two numbers are
computed from what the pipeline already knows about a token.

---

## Why this exists

"Token X has 20% APY" is not a comparable claim without context. The same
yield can mean *excellent, boring, or terrifying* depending on the stack
underneath it. This protocol forces the decomposition into four orthogonal
dimensions — protocol quality, trade size, composition depth, primitive
type — and combines them into a single risk number.

It also records every input, so the score is reproducible and auditable.

---

## Glossary (Russian source → English)

| Source term | Translation |
|---|---|
| Сделка | Trade (size as % of portfolio) |
| Вложенность | Nesting depth (number of composed DeFi primitives) |
| CPD | Cardinal Protocol Description (quality score) |
| Стадия | Stage |
| Возраст | Age |
| Код | Code |
| Взломы | Hacks |
| Иное | Other / custom |

---

## Step 1 — CPD score (protocol quality)

Sum seven sub-scores. Total range: **−7 to +18**.

### Stack tiers (chain / protocol / dapp)

The three scores use the same I/II/III classification but with different
weights, so a Tier-I chain matters more than a Tier-I dapp.

| Tier | Chain | Protocol | Dapp |
|---|---|---|---|
| I   | +3 | +2 | +1 |
| II  | +2 | +1 |  0 |
| III | +1 |  0 | −1 |

**What the tiers mean in practice:**

- **Chain Tier I** — Ethereum mainnet. Maximum security budget, deepest
  tooling, strongest liveness guarantees.
- **Chain Tier II** — major L2s with real fraud/validity proofs, their own
  sequencer, and substantial TVL (Base, Arbitrum, Optimism).
- **Chain Tier III** — experimental chains, appchains, or anything without
  a well-tested fork-choice rule (Monad, new EVMs, sidechains).
- **Protocol Tier I** — top-10 DeFiLlama protocols by TVL in their category,
  multi-year track record, battle-tested code.
- **Protocol Tier II** — mid-market protocols with >$100M TVL or strong
  niche standing.
- **Protocol Tier III** — early-stage or thin protocols.
- **Dapp Tier I** — large active user base, documented integrations, visible
  on-chain activity.
- **Dapp Tier II** — live but limited adoption.
- **Dapp Tier III** — experimental or single-user frontends.

### Lifecycle sub-scores

| Dimension | +3 | +2 | +1 | 0 | −1 |
|---|---|---|---|---|---|
| **Stage** | Audit | Release | Beta | Alpha | MVP |
| **Age** | >5 years | 3–5 years | 2–3 years | 1–2 years | 0–1 year |
| **Code** | OSO + Audit | OSO old | OSO new | Proprietary old | Proprietary new |
| **Hacks** | 0 (dev-only) | 0 | 0–1 | 2 | >2 |

(`OSO` = Open Source Old/established codebase.)

### Tier mapping

The CPD total (out of max = 18) maps to a quality tier and a **risk input**
used by Step 2.

| Tier | % of max | Threshold | Risk input |
|---|---|---|---|
| Tier-01 | ≥ 100% | 18 | **1** |
| Tier-02 | ≥ 75%  | 13.5 | **3** |
| Tier-03 | ≥ 50%  | 9    | **6** |
| Tier-04 | ≥ 25%  | 4.5  | **8** |
| Tier-05 | < 25%  | —    | **10** |

Lower tier number = higher quality = lower risk input.

---

## Step 2 — Base risk from the trade setup

Three integer inputs (each 1..10), averaged and rounded up.

```
base_risk = ceil((trade_size_score + nesting_score + cpd_risk_input) / 3)
```

- **trade_size_score** — trade size as % of portfolio, mapped linearly:
  1% → 1, 2% → 2, …, 10% → 10. Values outside the 0.01..0.10 range clamp.
- **nesting_score** — number of composed DeFi primitives in the strategy
  (e.g. "LP token deposited into a vault used as lending collateral" ≈ 3).
- **cpd_risk_input** — from Step 1.

---

## Step 3 — Primitive complexity multiplier (DeFi matrix)

A symmetric 10×10 matrix keyed on the primitive level of both ends of the
composition. Formula:

```
M[A, B] = (A + B) / 20    # range [0.1, 1.0]
```

### Primitive levels (lowest risk → highest)

| # | Primitive |
|---|---|
| 1 | Native coin staking |
| 2 | Deposits (lending) |
| 3 | Pools (AMMs, DEXs) |
| 4 | Vaults |
| 5 | Yield farming |
| 6 | Derivatives 1 (perps) |
| 7 | Derivatives 2 (options) |
| 8 | Indices (complex derivatives) |
| 9 | Compound indices (multi-dim derivatives) |
| 10 | Other / custom |

### How to pick A and B

- **Single primitive** (e.g. just staking ETH) — set A = B = level. The
  matrix reduces to a "self-loop" multiplier: `(2×level)/20 = level/10`.
- **Two primitives** (e.g. providing liquidity to a Uniswap pool whose LP
  token is then deposited into a Yearn vault) — A and B are the two levels
  in the chain.
- **Three or more primitives** — use the two *highest-level* primitives.
  Nesting depth (Step 2) already accounts for the count.

### Example values

| Scenario | A | B | Multiplier |
|---|---|---|---|
| Native ETH staking | 1 | 1 | 0.10 |
| USDC in Aave | 2 | 2 | 0.20 |
| Uniswap v2 LP | 3 | 3 | 0.30 |
| LP deposited into a vault | 3 | 4 | 0.35 |
| Perp position margined with LP | 3 | 6 | 0.45 |
| Options vault backed by yield farm | 4 | 7 | 0.55 |
| Custom structured product | 10 | 10 | 1.00 |

---

## Step 4 — Final risk score

Apply the primitive multiplier to the base risk:

```
final_risk = ceil(base_risk × (1 + M[A, B]))
```

Clamp to 1..10. This is the comparable risk number.

### Worked example from the spreadsheet

Inputs:
- Trade size: 10%
- Nesting depth: 2
- CPD risk input: 8 (Tier-04)
- Primitives: (3, 4) — Pools × Vaults

```
trade_score        = 10
nesting_score      = 2
cpd_risk_input     = 8
base_risk          = ceil((10 + 2 + 8) / 3) = ceil(6.67) = 7
M[3, 4]            = (3 + 4) / 20 = 0.35
final_risk         = ceil(7 × 1.35) = ceil(9.45) = 10
```

The Python implementation (`risk_model.compute_risk`) reproduces this
exactly.

---

## Step 5 — Revenue vs risk comparison

Feed the expected APY (from the `yields` command — either observed
TVL-weighted, best-pool, or the implied-from-fees calculation) into two
risk adjusters:

```
linear_adjusted       = apy × (11 - risk) / 10     # risk 1 keeps 100%, risk 10 keeps 10%
conservative_adjusted = apy / risk                 # punishes risk harder
```

The conservative variant is the better default for portfolio decisions —
it penalises risk geometrically rather than linearly.

### Verdict thresholds

| Condition | Verdict |
|---|---|
| APY ≤ 0 | `negative_yield` |
| risk ≤ 3 AND APY ≥ 8% | `strong_reward_for_risk` |
| risk ≥ 8 AND APY < 15% | `poor_reward_for_risk` |
| conservative_adjusted ≥ 3 | `acceptable` |
| otherwise | `marginal` |

### Rule of thumb

If `conservative_adjusted_apy < 3%` the trade is marginal regardless of how
great any single input looks. This single check will filter out ~80% of
"interesting" yield opportunities.

---

## How the `risk` command auto-derives inputs

Most CPD sub-scores can be inferred from data the pipeline already
collects. The command falls through to CLI overrides for anything it can't
see.

| CPD input | Source | Override flag |
|---|---|---|
| `chain_tier` | chain name → `ethereum=I`, `base/arbitrum/optimism=II`, `monad=III` | `--cpd-chain` |
| `protocol_tier` | DeFiLlama TVL: ≥$1B → I, ≥$100M → II, <$100M → III | `--cpd-protocol` |
| `dapp_tier` | ≥3 distinct yield chains AND ≥20 yield pools → I, any presence → II, else III | `--cpd-dapp` |
| `stage` | defaults to `release` | `--cpd-stage` |
| `age_band` | **not inferred** — emits a coverage gap | `--cpd-age` |
| `code_band` | Blockscout verified contract → `open_source_old`, else `proprietary_old` | `--cpd-code` |
| `hacks_band` | defaults to `0` (clean) — emits an info warning | `--cpd-hacks` |
| `primitive_a/b` | DeFiLlama category → lookup table (Lending→2, Dexs→3, Yield→5, Derivatives→6, …) | `--primitive-a` / `--primitive-b` |

Trade size and nesting depth have no auto-source because they are choices
about the trade, not facts about the protocol. Both flags have defaults
(`--trade-size 0.10`, `--nesting 1`).

---

## Usage

```bash
# Fully auto (uses all heuristics)
token-research risk PENDLE --chain ethereum \
  --address 0x808507121B80c02388fAd14726482e061B8da827

# With realistic overrides
token-research risk PENDLE --chain ethereum --address 0x808... \
  --trade-size 0.05 --nesting 2 \
  --cpd-age 3-5 --cpd-stage audit --cpd-hacks 0-1 \
  --primitive-a 1 --primitive-b 3

# Inside the full dossier
token-research deep-report PENDLE --chain ethereum --address 0x808... --format markdown
```

---

## Live Pendle reference run

### Auto-derived only

```
CPD sub-scores:
  chain      +3   (ethereum)
  protocol   +2   (TVL $1.76B → Tier I)
  dapp        0   (2 chains, 256 pools → Tier II)
  stage      +2   (default: release)
  age         0   (unknown — needs override)
  code       +2   (Blockscout verified)
  hacks      +2   (default: clean)
Total: 11 / 18 (61.1%) → Tier-03 → risk input 6

Trade: 5%, nesting 2, primitives Yield farming × Yield farming (5, 5)
base_risk  = ceil((5 + 2 + 6) / 3) = 5
mult       = (5 + 5) / 20 = 0.5
FINAL RISK = ceil(5 × 1.5) = 8 / 10

Revenue vs risk:
  weighted APY 6.23%  → conservative 0.78%   verdict: poor_reward_for_risk
  best APY    66.71%  → conservative 8.34%   verdict: acceptable
```

### With realistic overrides

`--cpd-age 3-5 --cpd-dapp I --cpd-stage audit`

```
CPD: 15 / 18 (83.3%) → Tier-02 → risk input 3
base_risk  = ceil((5 + 2 + 3) / 3) = 4
FINAL RISK = ceil(4 × 1.5) = 6 / 10

Revenue vs risk:
  weighted APY 6.23%  → conservative 1.04%   verdict: marginal
  best APY    66.71%  → conservative 11.12%  verdict: acceptable
```

### Takeaway

At Pendle's realistic quality (Tier-02) and a 5% trade size in Yield
farming primitive, the *average* pool yield (6.23%) is marginal. Only the
*best* pool yield (66.71%) clears the bar, and only at `acceptable` — not
`strong_reward_for_risk`.

This is the kind of nuance the standalone APY number hides: Pendle is a
legitimately solid protocol, but chasing its average yield doesn't pay for
the structural risk. You either pick specific high-APY pools deliberately
or take a smaller position.

---

## Model limitations

1. **CPD `age` can't be auto-derived** without a contract-creation block
   lookup. Default is 0 (neutral). Override with `--cpd-age` for real
   assessments — the bonus is ±3 points which can shift the tier by one.
2. **CPD `hacks` defaults optimistic.** We don't wire rekt.news. Override if
   the project has incidents. An info warning is emitted by default so you
   can't forget.
3. **Primitive inference is coarse.** A DeFiLlama "Yield" category collapses
   PT/YT/vault strategies into one level. For complex strategies, set both
   primitive levels explicitly.
4. **CPD tiers are quantized.** A project scoring 13/18 (72.2%) is Tier-03
   while 14/18 (77.8%) is Tier-02 — a 50% reduction in risk input (6→3)
   from a single-point swing. When an override pushes you near a boundary,
   treat the tier as provisional.
5. **Verdict thresholds are simple.** The named verdicts
   (`strong_reward_for_risk`, `acceptable`, …) are a shorthand, not a
   decision rule. For portfolio-level decisions, compare the numeric
   `conservative_adjusted` value directly across candidates.
6. **Revenue-side inputs come from the `yields` command.** If DeFiLlama
   has no protocol match, the APY is missing and the whole revenue-vs-risk
   view collapses to `no_yield_data`. The risk number is still valid
   standalone.
7. **The DeFi matrix is symmetric and linear.** Combining a very safe
   primitive with a very risky one averages them. In reality the risky leg
   often dominates — this is a known over-simplification the spreadsheet
   inherited, and a candidate for a future non-linear variant.

---

## Scoring-model updates beyond the spreadsheet

The spreadsheet defines the CPD score, the DeFi primitive matrix, and the
final risk formula. The Python implementation adds **three pillars the
spreadsheet doesn't cover**, all wired into `commands/score.py`. These
were landed in response to concrete misreads the early runs produced on
real tokens (Morpho, ONDO) where the spreadsheet-only CPD number didn't
reflect observed structure.

### supply_pressure

Previously a flat baseline of 60 regardless of inputs. Now derived from:

- **`compare.supply.effective_overhang_pct_of_supply`** (preferred) or
  `compare.supply.protocol_owned_pct_of_supply` (legacy fallback) — the
  share of supply that behaves as structural pressure. Effective overhang
  combines contract-held tokens with concentrated EOA top-10 above the
  20% threshold (KAITO-class fix — see below).
- **`compare.supply.effective_overhang_ratio`** — the overhang/float stress
  metric. 3.04× for KAITO, 2.17× for Morpho, 1.50× for ONDO.
- `yields.revenue_model` — `protocol_capture` adds +10; `fees_pass_through`
  subtracts 5 (no organic buy pressure to absorb future issuance).

Score = `75 − overhang_penalty − model_penalty − ratio_penalty`, where
overhang penalties scale from −25 (≥65% effective overhang, Morpho/KAITO
class) to 0 (<15%). Falls back to 65 baseline when `compare.supply` is
unavailable.

#### KAITO-class fix (2026-04-12)

The original `supply_pressure` used the legacy formula
`protocol_owned_pct / effective_circulating_pct`. This correctly
captured DAO-treasury overhang for Morpho (2.17×) and ONDO (1.50×) but
**misfired on KAITO**: KAITO has only 13% of supply in contracts but 82%
of its effective top-10 is held in EOA wallets (team/investor
allocations at personal addresses rather than in GnosisSafes). The
legacy formula gave KAITO a ratio of 0.15× ("distributed") — the
opposite of the actual structural read.

The fix adds a concentration term:

```
effective_overhang_pct = protocol_owned_pct + max(0, effective_top10_pct - 20)
```

For tokens below the 20% effective-top-10 threshold, this is a no-op
(Morpho, ONDO, Pendle readings are preserved). For tokens above it,
concentrated EOA supply counts as overhang alongside contract-held
supply. KAITO under the new formula: 12.96 + (82.25 − 20) = **75.21%**,
ratio **3.04×** — even more severe than Morpho, which matches intuition.

Signal-command weight (`_score_overhang`) and score-pillar penalty
tables both prefer the new field when available, with the legacy
formula as a fallback for offline or partial data.

### ownership_quality

Now prefers the **EOA-adjusted top-10** from `compare.supply.effective_top10_pct_of_supply`
over the raw top-10 share when available. When raw `nakamoto_51 ≤ 2` is
driven by a DAO treasury contract, the corresponding −15 penalty is
skipped — otherwise a protocol like Morpho (single Wrapper at 57% of
supply) scores 0 on ownership even though the distribution among actual
EOAs is healthy. Raw nakamoto / gini are preserved in `raw_inputs` for
auditability.

### governance_commitment

The original pillar only credited fingerprinted OZ VestingWallets and
Synthetix-style staking. Both Morpho (68% in `Wrapper` + safes) and ONDO
(60% in GnosisSafes) scored ~25 because none of their custody matches
those templates. The pillar now also credits **generic protocol-owned
supply from compare** with lower confidence weighting:

- Protocol-owned ≥50% → +15
- Protocol-owned ≥25% → +10
- Protocol-owned ≥10% → +5

This is explicitly lower-confidence than fingerprinted custody (which
has an enforceable release schedule) — multisigs can distribute at any
time. But a protocol holding 60% of supply in foundation contracts IS
demonstrating on-chain governance commitment, just without an OZ vesting
schedule to prove the release shape.

### Resolver cross-check (2026-04-11)

The resolver now consults DeFiLlama's `/protocols` directory and uses
the canonical `address` field from the matching protocol entry before
falling back to DexScreener. This fixes the `MintBurnTeamToken` class of
bugs reproduced four times across Katana, ONDO, Morpho, and HBAR reviews.
Pre-TGE programs (DeFiLlama entries with no `address` field) still fall
through to DexScreener — use the `prelaunch` command for those cases.

### In-process memoization (2026-04-11)

`providers/_memoize.py` applies a `@cached` decorator to every hot
read path (`defillama.get_protocols`, `get_yield_pools`, `_fees_summary`;
`blockscout.get_token_info`, `get_token_holders`, `get_address_info`;
`rpc.get_code`). This makes `deep-report` ~4× faster by collapsing the
repeated fetches that `score → compare → {holders, locks, staking,
yields}` used to trigger. Cache is per-process.

---

## File map

| File | Role |
|---|---|
| `docs/Revenue_vs_Risk_(CPD 2.0).xlsx` | Source spreadsheet (Russian) |
| `docs/risk_revenue_estimation.md` | This document |
| `src/token_research/risk_model.py` | Pure-function implementation (CPD, matrix, risk, revenue-vs-risk) |
| `src/token_research/commands/risk.py` | CLI command with auto-derivation + override flags |
| `src/token_research/commands/prelaunch.py` | Pre-TGE variant for programs without a token |
| `src/token_research/commands/compare.py` | Cross-metric ratios (source of effective-circulation + protocol-owned) |
| `src/token_research/commands/score.py` | 5-pillar composite (uses updated supply_pressure + ownership + governance) |
| `src/token_research/commands/deep_report.py` | Integrates `risk` / `compare` into the full dossier |
| `src/token_research/serialization.py` | Markdown renderer — "Revenue vs risk (CPD 2.0)" + "Cross-metric comparison" sections |
| `src/token_research/resolver.py` | DeFiLlama `address` cross-check |
| `src/token_research/providers/_memoize.py` | Per-run request memoization |

The Python implementation is a direct translation of the spreadsheet for
the core CPD/matrix/risk formulas. The worked example above reproduces
the spreadsheet's `F19 = 10` result exactly. The three extensions above
(`supply_pressure`, `ownership_quality`, `governance_commitment`) are
Python-side additions that live on top of the translated base and exist
to make the score usable on real on-chain-data runs.
