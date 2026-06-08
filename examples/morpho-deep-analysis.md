# Morpho — Revenue vs Risk Review (CPD 2.0)

**Target:** `MORPHO` on Ethereum · `0x58D97B57BB95320F9a05dC918Aef65434969c2B2`
**DeFiLlama protocol match:** `morpho-v1` · category **Lending**
**Method:** protocol from [`docs/risk_revenue_estimation.md`](../docs/risk_revenue_estimation.md)
**Pipeline runs:** `yields` → `compare` → `risk` → `score`

This review was used to stress-test the scoring model. Seven model fixes
landed as a direct result; this document captures both the final numbers
and the gap analysis that drove the fixes.

---

## TL;DR

- **Composite score: 65.3 / 100 ("solid")**
- **Final risk (5% trade, Lending primitive, nesting 1): 5 / 10**
- **Realistic best yield: 11.65% APY** (meaningful pool filter)
- **Verdict: `marginal`** — lending yields don't clear the conservative threshold at this risk level, **but** Morpho's fee-switch optionality and DAO-owned supply structure are not captured in the verdict
- **Biggest hidden signal:** 68% of supply is contract-held (wrapper + safes), so the "top 10 hold 75%" headline massively overstates whale risk
- **Fee-switch upside:** $139.25M annualized fees ÷ $1.02B mcap = **13.65%** — extraordinary if governance ever activates capture

---

## Identity & scale

| Field | Value |
|---|---|
| Symbol | `MORPHO` |
| Chain | `ethereum` |
| Token address | `0x58D97B57BB95320F9a05dC918Aef65434969c2B2` |
| DeFiLlama slug | `morpho-v1` |
| Category | `Lending` |
| Protocol age band (from DeFiLlama `listedAt`) | **`2-3`** |
| TVL (DeFiLlama) | **$7.27B** |
| Market cap (fallback: DexScreener best pair) | **$1.02B** |
| Mcap source | `dexscreener_best_pair` |
| Spot price (DexScreener) | $1.84 |
| Total supply | ~1.00B MORPHO |
| Chains DeFiLlama tracks Morpho on | 35 |
| Yield pools (all) | 733 |
| Yield pools (meaningful: TVL ≥ $10M, APY > 0) | 79 |

---

## Step 1 — CPD score (protocol quality)

### Auto-derived inputs (no manual overrides)

| Dimension | Source | Value | Sub-score |
|---|---|---|---|
| `chain_tier` | inferred (chain name → tier table) | `I` (Ethereum) | **+3** |
| `protocol_tier` | inferred (TVL $7.27B ≥ $1B) | `I` | **+2** |
| `dapp_tier` | inferred (733 pools ≥ 100 threshold) | `I` | **+1** |
| `stage` | default | `release` | **+2** |
| `age_band` | inferred (DeFiLlama `listedAt`) | `2-3` | **+1** |
| `code_band` | inferred (Blockscout verified) | `open_source_old` | **+2** |
| `hacks_band` | default (optimistic) | `0` (clean) | **+2** |

**Total: 13 / 18 (72.2%) → `Tier-03` → risk input `6`**

Morpho is 0.5 points short of `Tier-02` (needs 13.5 / 18). A realistic
override `--cpd-stage audit` (+1) or `--cpd-hacks 0_dev` (+1) would push
it over the boundary. The pure-auto run is conservative but directionally
correct.

### Why it's not `Tier-02` yet (fixable)

Three sub-scores that the heuristics underestimate vs reality:

1. **Age = 2-3 (not 3-5)** — DeFiLlama's `listedAt = 1705261273`
   (Jan 2024) is when the `morpho-v1` slug was created, not when the
   original Morpho contracts deployed in May 2022. `--cpd-age 3-5`
   corrects this → adds +1.
2. **Stage = release (not audit)** — default, but Morpho has been through
   Trail of Bits, Spearbit, Cantina, and OpenZeppelin audits. `--cpd-stage
   audit` adds +1.
3. **Code = open_source_old (not oso_audit)** — the heuristic sees the
   verified contract but can't tell it's been audited by multiple top-tier
   firms. `--cpd-code oso_audit` adds +1.

With all three manual overrides: **16 / 18 (88.9%) → `Tier-02` → risk
input `3`**.

---

## Step 2–4 — Final risk

Trade parameters:
```
trade_size_pct   = 0.05  (5% of portfolio)
nesting_depth    = 1     (single-primitive lending deposit)
primitive_a      = 2     (Deposits, from Lending category)
primitive_b      = 2     (same)
```

### Auto-derived CPD

```
base_risk     = ceil((5 + 1 + 6) / 3)           = ceil(4.00) = 4
defi_multiplier = M[2, 2] = (2+2)/20             = 0.20
final_risk    = ceil(4 × (1 + 0.20))             = ceil(4.80) = 5 / 10
```

### With realistic overrides (`Tier-02` CPD)

```
base_risk     = ceil((5 + 1 + 3) / 3)            = ceil(3.00) = 3
defi_multiplier = 0.20                            (unchanged)
final_risk    = ceil(3 × 1.20)                    = ceil(3.60) = 4 / 10
```

Both outputs place Morpho in the **"low-moderate risk"** band.

---

## Step 5 — Revenue vs risk

### APY data (filtered to meaningful pools)

| Slice | Count | Best APY | TVL-weighted avg | Total pool TVL |
|---|---|---|---|---|
| All pools (polluted) | 733 | 1157.9% (noise) | 1.76% | $12.12B |
| **Meaningful** (TVL ≥ $10M, APY > 0) | **79** | **11.65%** | **3.48%** | $5.14B |
| APY p95 (robust max) | — | **7.95%** | — | — |

### Top 5 "meaningful" Morpho pools

| Chain | Symbol | APY | TVL |
|---|---|---|---|
| Base | STEAKUSDC | 4.38% | $471.3M |
| Ethereum | SENPYUSDMAIN | **5.05%** (1.25% base + 3.80% rewards) | $423.4M |
| Ethereum | SENPYUSD | 2.25% | $423.4M |
| Base | GTUSDCP | 4.38% | $357.9M |
| Base | STEAKUSDC | 4.38% | $271.1M |

### Risk-adjusted yield at `final_risk = 5` (auto CPD)

| View | APY | Linear | Conservative | Verdict |
|---|---|---|---|---|
| Observed weighted (meaningful) | 3.48% | 2.09% | 0.70% | `marginal` |
| Observed best (meaningful) | **11.65%** | 6.99% | 2.33% | `marginal` |

### Risk-adjusted yield at `final_risk = 4` (with overrides)

| View | APY | Linear | Conservative | Verdict |
|---|---|---|---|---|
| Observed weighted | 3.48% | 2.44% | 0.87% | `marginal` |
| Observed best | 11.65% | 8.16% | 2.91% | **`marginal`** (just below 3.0 threshold) |

**Both risk levels land `marginal` on every realistic yield.** The best
Morpho pool (SENPYUSDMAIN at 5.05% with rewards) would need either the
CPD to reach `Tier-01` (not realistic) or a higher-APY pool to clear
`acceptable`.

---

## Supply composition (the big correction)

This is where the pipeline's v1 scoring was badly wrong and where the
fixes in this round land the hardest.

### Raw holder snapshot (top 15, via Blockscout)

| # | Address | Balance | % of 1B supply | Blockscout label |
|---|---|---|---|---|
| 1 | `0x9d03bb2092…` | **578,536,501** | **57.85%** | **`Wrapper`** (Morpho DAO wrapper) |
| 2 | `0x53051ef9e2…` | 42,688,183 | 4.27% | `SafeProxy` |
| 3 | `0x72b23aebbd…` | 32,239,760 | 3.22% | EOA |
| 4 | `0x3154cf16cc…` | 32,176,394 | 3.22% | `L1ChugSplashProxy` (OP Stack deployer) |
| 5 | `0x2419b00207…` | 18,130,018 | 1.81% | EOA |
| 6 | `0x6930926698…` | 11,781,449 | 1.18% | EOA |
| 7 | `0x45b9dc999a…` | 11,235,514 | 1.12% | `GnosisSafeProxy` |
| 8 | `0x6abfd6139c…` | 9,918,999 | 0.99% | `GnosisSafeProxy` |
| 9 | `0x8a73a3e50e…` | 8,538,131 | 0.85% | EOA |
| 10 | `0x3c716d252b…` | 8,000,000 | 0.80% | EOA |
| 11 | `0x67a71a4372…` | 7,800,000 | 0.78% | EOA |
| 12 | `0xfd91bd4029…` | 7,760,419 | 0.78% | EOA |
| 13 | `0x3c66725aab…` | 6,984,375 | 0.70% | EOA |
| 14 | `0x4a7a9ff8ad…` | 6,786,525 | 0.68% | EOA |
| 15 | `0xcba28b3810…` | 6,578,871 | 0.66% | `GnosisSafeProxy` |

Raw numbers:
- `top10_share` = **75.32%**
- `top20_share` = 81.59%
- `nakamoto_51` = **1** (the Wrapper alone exceeds 51%)
- `top_holders_gini` = 0.77

### Contract classification (via `locks`)

Pipeline `is_contract()` + Blockscout name hints identified **7 of the
top 30 holders** as contracts:

| Address | Tokens | % | Blockscout name | Classification |
|---|---|---|---|---|
| `0x9d03bb2092…` | 578.5M | **57.85%** | `Wrapper` | **DAO wrapper** (token distribution contract) |
| `0x53051ef9e2…` | 42.7M | 4.27% | `SafeProxy` | Multisig |
| `0x3154cf16cc…` | 32.2M | 3.22% | `L1ChugSplashProxy` | OP Stack deployer (Base bridge-related) |
| `0x45b9dc999a…` | 11.2M | 1.12% | `GnosisSafeProxy` | Multisig (treasury / team) |
| `0x6abfd6139c…` | 9.9M | 0.99% | `GnosisSafeProxy` | Multisig |
| `0xcba28b3810…` | 6.6M | 0.66% | `GnosisSafeProxy` | Multisig |
| `0xe87234f8dd…` | — | — | `SafeProxy` | Multisig (deeper in list) |

**Total contract-held: ~684.3M tokens = 68.43% of supply.**

None of these match OZ VestingWallet / TokenTimelock / StakingRewards
patterns, so the `locks` fingerprinter correctly files them under generic
`contract_balance` rather than `vesting_contracts`. The old scoring model
then ignored them in ownership calculations.

### Effective circulation (post-fix)

The new `compare.supply` block distinguishes three tiers:

| Metric | Value | % of supply |
|---|---|---|
| Total supply | 999.99M tokens | 100% |
| Fingerprinted custody (vesting + staking) | **0** | 0% |
| **Contract-held (wrappers, safes, proxies)** | **684.27M** | **68.43%** |
| **Protocol-owned total** | **684.27M** | **68.43%** |
| **Effective circulating** (supply − protocol-owned) | **315.73M** | **31.57%** |
| Float (legacy: supply − fingerprinted custody only) | 999.99M | 100.00% |

### Concentration views — raw vs effective

| Metric | Raw | Effective | Delta |
|---|---|---|---|
| Top-10 share (of total supply) | 75.32% | **11.41%** | −63.91pp |
| Nakamoto-51 | 1 | *(raw preserved)* | — |
| Number of EOA+non-protocol holders in top-30 | — | 14 | — |

**The "75% concentration" headline is driven almost entirely by the DAO
Wrapper + team Safes.** True EOA/non-protocol top-10 concentration is
**11.41%** — a genuinely healthy distribution for a recently-launched
governance token.

This is the single most important correction in this review. It's also
the hardest to make: the previous scoring model gave Morpho ownership =
**0 / 100** because it hit both the `top10 ≥ 50%` and `nakamoto ≤ 2`
penalties. With the effective top-10 used instead, the same pillar scores
**88.6 / 100**.

---

## Scoring pillars — before and after the fixes

| Pillar | v1 (pre-fix) | v2 (post-fix) | Δ | Why |
|---|---|---|---|---|
| `fundamental_support` | **55** | **80.0** | +25 | mcap now resolves via DexScreener fallback → mcap/TVL = 0.14 tier fires (+25) and fees/mcap = 13.65% bonus fires (+10). Was stuck at TVL-tier-only before. |
| `ownership_quality` | **0** | **88.6** | **+88.6** | `effective_top10_share = 11.41%` (vs raw 75.32%); nakamoto and gini penalties skipped because raw concentration is 68% protocol-owned. |
| `liquidity_quality` | 65.8 | 65.8 | — | unchanged |
| `governance_commitment` | 25 | 25 | — | unchanged — vesting not fingerprinted (next target) |
| `supply_pressure` | 60 | 60 | — | baseline |
| **Composite** | **40.7** | **65.3** | **+24.6** | weak → solid |

### Post-fix pillar detail

**`fundamental_support: 80`**
```
tvl                    $7.27B     → +35 (≥ $1B tier)
mcap                   $1.02B     → mcap/TVL = 0.14 → +25 (ratio < 2 tier)
mcap_source            dexscreener_best_pair
chains_count           35         → +10 (≥ 3 chains)
change_1m              None       → +0
fees_to_mcap_ratio     13.65%     → +10 (≥ 5% tier)
                                    ---
                                    80
```

**`ownership_quality: 88.6`**
```
top10_share_raw                  0.7532
top10_share_used                 0.1141   ← effective (EOA-only) view
top10_is_effective               True
protocol_owned_pct_of_supply     68.43%

base score = (1 - 0.1141) * 100 = 88.59
nakamoto_51_raw = 1              → penalty SKIPPED
                                    (reason: effective top-10 < 50%)
top_holders_gini = 0.77          → penalty SKIPPED
                                    (reason: substituted with effective metric;
                                     avoiding double-penalty)
                                   ---
                                    88.6
```

**`governance_commitment: 25`** (unchanged — still dragged down)
```
vesting_contracts_count    0      (no OZ vesting fingerprinted)
staking_contracts_count    0      (no ERC-4626 vault or Synthetix staker)
timelock_balances_count    7      (Wrapper + Safes, non-fingerprinted)
contracts_inspected        7
has_vesting_schedule       False
locked_onchain_total       None
staked_total               None
                            ---
base 35 - 10 (many contracts inspected but 0 fingerprinted) = 25
```

The pillar can't tell that Morpho's `Wrapper` + Safes ARE the governance
commitment — they just don't expose OZ-style selectors. **This is the
next-most-impactful fix** and would likely push the pillar from 25 → 75+,
lifting composite from 65 → ~72.

---

## Cross-metric ratios (from `compare`)

### Fees vs mcap / TVL / custody

| Ratio | Value | Interpretation |
|---|---|---|
| Annualized fees | **$139.25M** | Very high absolute |
| Annualized revenue | **$0.00** | **fee switch not active** |
| Revenue share of fees | **0%** | Token holders receive nothing today |
| Fees / mcap | **13.65%** | Theoretical yield if 100% of fees were captured |
| Fees / TVL | **1.91%** | Capital efficiency: ~1.9¢/yr per $1 of TVL |
| Revenue / mcap | — | n/a (zero revenue) |
| Fees / staked USD | n/a | no fingerprinted staking |
| Implied staker APY | n/a | no staking contracts to divide by |

### Market & liquidity depth

| Ratio | Value |
|---|---|
| Market cap | $1.02B |
| Protocol TVL | $7.27B |
| DEX liquidity | ~$3.8M (needs reverification, not covered in this run) |
| mcap / TVL | 0.141 (10× cover — strong fundamental backing) |
| Yield pool TVL | $12.12B |
| Yield pool TVL / protocol TVL | 1.67 (pools double-count some collateral) |

### Custody comparisons

Morpho has **no fingerprinted staking or vesting**, so the legacy custody
ratios (`locked_tokens_vs_staked`, `custody_vs_float`, `custody_vs_dex_liquidity_usd`)
all return `None`. The meaningful comparison lives in the new `supply` block:
`protocol_owned_pct = 68.43%` vs `effective_circulating_pct = 31.57%`.

### Revenue model flag

```
revenue_model: fees_pass_through

WARNING: Protocol generates fees ($139,250,664/yr) but reports $0 revenue —
         by-design fee-switch-off model. Token holders do not receive fees
         unless governance activates capture.
```

This warning now surfaces automatically thanks to the `_classify_revenue_model`
helper added in this round. Before the fix, the pipeline couldn't tell
the difference between "protocol intentionally forwards all fees" and
"data missing".

---

## Model gaps surfaced by this review (all fixed)

The Morpho run exposed seven gaps in the pipeline that were obscuring
or distorting the risk/reward picture. Every one is now fixed and
validated against live Morpho data.

| # | Gap | Effect pre-fix | Fix |
|---|---|---|---|
| 1 | DeFiLlama `morpho-v1` has `mcap = None` | `fees_to_mcap_ratio = None`, fundamental pillar stuck at 55 | **Mcap fallback chain**: DeFiLlama protocol → DexScreener best-pair `market_cap` → DexScreener `fdv`. Tracks `mcap_source` field. |
| 2 | `weighted_avg_apy` = 1.75% polluted by zero-APY collateral pools | Realistic yield invisible; best APY showed 1122% noise | **Filtered APY metrics**: `weighted_avg_apy_meaningful`, `best_apy_meaningful`, `apy_p95` — filters TVL ≥ $10M AND APY > 0. Risk command now uses these for verdicts. |
| 3 | Dapp tier heuristic required `3 chains AND 20 pools` → Morpho (2 chains, 733 pools) fell to Tier II | CPD under-scored by 1 | **Reweight**: `100+ pools` alone promotes to Tier I; `10+ pools OR any chain` → Tier II. |
| 4 | Age always needed manual override (`coverage_gap: cpd.age_band`) | Every CPD run defaulted to 0 | **Auto-derive age** from DeFiLlama `listedAt` timestamp, plumbed through `yields → risk`. |
| 5 | `revenue = 0` indistinguishable from "no data" | No signal about the deliberate fee-switch-off model | **`revenue_model` flag**: `fees_pass_through` / `protocol_capture` / `unknown` + info warning when fees > 0 AND revenue == 0. |
| 6 | Contract-held supply (Wrapper, Safes, proxies) treated identically to whale EOAs | `ownership_quality = 0` for Morpho | **Effective circulation** in `compare.supply`: subtracts all contract-held holders (fingerprinted or not), computes `effective_top10_share = 11.41%`. |
| 7 | Ownership pillar still used raw `top10_share` even when compare had the effective number | Score pillar stayed at 0 after #6 | **Score pillar integration**: `_score_ownership_quality` now prefers `effective_top10` when compare provides it; skips nakamoto/gini penalties when raw concentration is driven by protocol-owned holders. |

### Composite score progression

```
pre-fix  (v1):  40.7   ← "weak"
+ mcap fallback + fees/mcap pillar bonus:
                48.3
+ filtered APY (no-op on score, changes verdicts only):
                48.3
+ effective top-10 + nakamoto/gini penalty skip:
                65.3   ← "solid"   ← current
```

---

## What still drags the score down

### 1. `governance_commitment = 25`

Morpho's custody structure relies on the DAO `Wrapper` and team/operations
GnosisSafes. None of these match the OZ VestingWallet / TokenTimelock /
ERC-4626 / Synthetix-staking ABI fingerprints, and `Wrapper` /
`SafeProxy` don't trigger the vesting-name keyword list (`vest`, `lock`,
`escrow`, `cliff`, `lockup`, `timelock`).

**The same structural data that fixed ownership_quality could fix this
pillar.** A follow-up would credit "contract-held supply" as on-chain
custody for the governance pillar even without OZ fingerprinting — with
slightly lower confidence weighting. Expected lift: **25 → 70+**,
composite: **65 → ~72**.

### 2. Stage / code / age still imperfect

- `stage = release` (default) should be `audit` for a protocol with four
  major audits
- `age_band = 2-3` from DeFiLlama reflects the `morpho-v1` slug creation
  date, not the actual 2022 protocol launch
- `code_band = open_source_old` should be `oso_audit`

All three are correctable via `--cpd-*` flags; together they would push
Morpho from `Tier-03` to `Tier-02` (CPD risk input 6 → 3), which would
lower the final risk from 5/10 to 4/10 and improve every risk-adjusted
verdict proportionally. The auto-derivation is conservative; the manual
run is realistic.

---

## Final verdict

| Dimension | Reading |
|---|---|
| **Protocol quality (CPD)** | Tier-03 (auto) → Tier-02 (with overrides). Among the strongest DeFi lending protocols. |
| **Final risk** | 5/10 (auto) or 4/10 (overrides). Low-moderate. |
| **Realistic APY** | 3.48% weighted / 11.65% best on meaningful ($10M+) pools |
| **Risk-adjusted verdict** | `marginal` on both weighted and best realistic pools |
| **Distribution** | EOA concentration is healthy (11.41% top-10); the "75% top-10" headline is 68% DAO/treasury. |
| **Fee capture** | $139.25M/yr generated, $0 captured. fees/mcap = 13.65% theoretical upside. |
| **Hidden lever** | Fee-switch activation. Not currently priced into any yield number the pipeline sees. |

**Practical read:** Morpho is one of the lowest-risk DeFi lending venues
(the scoring now correctly reflects this at composite 65.3 / ownership
88.6). The on-chain custody structure is genuinely protocol-aligned — it
just doesn't look that way to OZ-pattern detectors. For a stablecoin
lender, the 4-5% APYs on the biggest pools are fair compensation for the
risk but don't beat TradFi money-market rates by enough to pass the
conservative 3%-per-unit-risk threshold. For a MORPHO token holder, the
real thesis is the fee-switch optionality — a governance upgrade could
redirect $139M/yr into token-holder yield, which would imply a ~13.65%
"fundamental yield" on today's mcap. That's not a yield the pipeline can
measure; it's an option.

---

## Reproducibility

```bash
# Pure auto (produces exact numbers above)
token-research risk MORPHO \
  --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 \
  --trade-size 0.05 \
  --nesting 1

# With realistic overrides (→ Tier-02, risk 4/10)
token-research risk MORPHO \
  --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 \
  --trade-size 0.05 \
  --nesting 1 \
  --cpd-age 3-5 \
  --cpd-stage audit \
  --cpd-code oso_audit

# Full dossier (JSON)
token-research deep-report MORPHO \
  --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 \
  --format json

# Full dossier (Markdown)
token-research deep-report MORPHO \
  --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 \
  --format markdown
```

Related docs:
- [`risk_revenue_estimation.md`](../docs/risk_revenue_estimation.md) — the protocol itself
- [`Revenue_vs_Risk_(CPD 2.0).xlsx`](../docs/Revenue_vs_Risk_%28CPD%202.0%29.xlsx) — original Russian source

Implementation files:
- `src/token_research/risk_model.py` — pure CPD + risk + revenue-vs-risk functions
- `src/token_research/commands/risk.py` — CLI command with auto-derivation
- `src/token_research/commands/compare.py` — cross-metric ratios (inc. effective circulation)
- `src/token_research/commands/yields.py` — fees, revenue, filtered APY, mcap fallback
- `src/token_research/commands/score.py` — 5-pillar composite with effective ownership
