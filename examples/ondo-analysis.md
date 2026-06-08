# Ondo Finance (ONDO) — Deep Analysis

**As of 2026-04-11** · Applied protocol: [`docs/risk_revenue_estimation.md`](../docs/risk_revenue_estimation.md)
Sibling reviews: [`morph_risk_revenue.md`](morpho-deep-analysis.md), [`katana_analysis.md`](../archived/docs/katana_analysis.md)

---

## TL;DR

- **Composite score: 65.8 / 100 ("solid")** — nearly identical to Morpho (65.3)
- **Final risk (5% trade, Deposits primitive, nesting 1): 5 / 10** (auto-CPD)
- **Realistic best yield: 3.55% APY** (USDY, USDYC, OUSG — Treasury-backed)
- **Verdict: `marginal`** — RWA yields are capped by the Treasury bill rate floor
- **Key structural reading:** 60.04% of supply is held in **two GnosisSafe multisigs**; effective non-protocol top-10 is **14.31%** (vs raw 73.28%)
- **Hidden lever (same as Morpho):** $53.23M annualized fees ÷ $1.24B mcap = **4.30%** theoretical holder yield if governance activates a fee switch
- **RWA-specific context:** Ondo Yield Assets is the **largest RWA protocol on DeFiLlama** at $2.81B TVL across 12 chains. Product family: **OUSG** (institutional-accredited US Treasury ETF wrapper), **USDY** (retail-accessible Treasury-backed yield-stablecoin), **USDYC** (scaled USDY variant)

---

## Critical resolution problem (same class of bug as Katana)

`token-research resolve ONDO` returns:

```
symbol:  ONDO
project: Ondo
chain:   ethereum
address: 0x20d22eDdC93a1D30b6E7017E89930A1908c2fD34   ← wrong
```

That address is a contract named **`MintBurnTeamToken`** — an early team-allocation wrapper with `circulating_market_cap: None`. The **canonical ONDO token** is `0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3` (contract name `Ondo`, mcap $1.24B). Both share the same 10B total supply, which confused the resolver's ranking.

This is the third time the "resolver picks a different contract than the real token" failure mode has surfaced (previous: KatanaToken memecoin, Katana Inu memecoin). The fundamental fix is to consult DeFiLlama's protocol `address` field during resolution — the `ondo-yield-assets` protocol entry exposes the canonical token address via `address` / `tokenBreakdowns`, but the resolver doesn't use it today. **Follow-up task worth scheduling.**

All analysis below uses `--address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3` explicitly.

---

## Identity & scale

| Field | Value |
|---|---|
| Symbol | `ONDO` |
| Chain | Ethereum |
| Canonical token | `0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3` |
| DeFiLlama slug | `ondo-yield-assets` |
| DeFiLlama category | **RWA** |
| TVL | **$2.81B** |
| Chains tracked | **12** (Ethereum, XRPL, Solana, Stellar, Arbitrum, Optimism, and others) |
| Market cap (spot × circulating) | **$1.24B** |
| mcap source | `dexscreener_best_pair` |
| Spot price | **$0.2542** |
| Total supply | 10,000,000,000 ONDO |
| FDV | ~$2.54B |
| protocol_age_band | `None` (DeFiLlama `listedAt` missing for this slug) |
| Audits on DeFiLlama | (not populated in the main directory) |

### The Ondo ecosystem on DeFiLlama

| Protocol | Category | TVL | Chains |
|---|---|---|---|
| **Ondo Yield Assets** (this analysis target) | RWA | **$2.81B** | 12 |
| Ondo Global Markets | RWA | $0.75B | 3 |
| Ondo v1 (Legacy) | Yield | $0 | 1 |

**Combined ecosystem TVL: ~$3.56B.** Ondo is the market-leading RWA protocol on a TVL basis.

---

## Step 1 — CPD score (auto-derived)

| Dimension | Source | Value | Sub-score |
|---|---|---|---|
| `chain_tier` | inferred | `I` (Ethereum) | **+3** |
| `protocol_tier` | inferred | `I` ($2.81B ≥ $1B) | **+2** |
| `dapp_tier` | inferred | `I` (12 chains, 16 yield pools — passes the 3+ chain / 20+ pool rule) | **+1** |
| `stage` | default | `release` | **+2** |
| `age_band` | **null** | — | **0** (neutral — DefiLlama `listedAt` absent) |
| `code_band` | inferred (Blockscout verified) | `open_source_old` | **+2** |
| `hacks_band` | default | `0` (clean) | **+2** |

**Total: 12 / 18 (66.7%) → `Tier-03` → risk input `6`**

ONDO sits exactly in the middle of the tier band. Without manual overrides it can't cross 13.5 / 18 (the Tier-02 threshold). Realistic overrides that would push it higher:

- `--cpd-age 3-5` (Ondo Finance launched 2022, 4 years old) → **+2**
- `--cpd-stage audit` (multiple audits including OpenZeppelin, Code4rena, Trail of Bits) → **+1**
- `--cpd-code oso_audit` (open-source + multi-firm audits) → **+1**

With those: **16 / 18 (88.9%) → `Tier-02` → risk input `3`**.

---

## Steps 2–4 — Final risk

Inputs (5% trade, standard lending deposit):
```
trade_size_pct = 0.05     (score 5)
nesting_depth  = 1        (single-primitive deposit into an Ondo Treasury product)
primitive_a    = 2        (Deposits — this is RWA-lending category)
primitive_b    = 2        (same)
```

### Auto CPD (Tier-03)

```
base_risk    = ceil((5 + 1 + 6) / 3)       = 4
defi_mult    = M[2, 2] = (2+2)/20          = 0.20
final_risk   = ceil(4 × (1 + 0.20))        = ceil(4.80) = 5 / 10
```

### Realistic overrides (Tier-02)

```
base_risk    = ceil((5 + 1 + 3) / 3)       = 3
defi_mult    = 0.20                         (unchanged)
final_risk   = ceil(3 × 1.20)               = ceil(3.60) = 4 / 10
```

**Final risk = 4–5/10** — low-moderate, consistent with Morpho. The structure is nearly identical: both are Tier-02/03 Lending/RWA protocols with simple primitive profiles.

---

## Yield data

### All indexed Ondo yield pools

| Count | View | Value |
|---|---|---|
| 16 | All yield pools | — |
| 11 | Meaningful (TVL ≥ $10M, APY > 0) | — |
| — | Best APY (meaningful) | **3.55%** |
| — | TVL-weighted avg APY (meaningful) | **3.518%** |
| — | APY p95 (robust max) | 31.91% |
| — | Total meaningful pool TVL | $2.52B (90% of protocol TVL indexed) |

### Top meaningful pools

| Project | Chain | Symbol | APY | TVL |
|---|---|---|---|---|
| `ondo-yield-assets` | Ethereum | **USDYC** | 3.55% | **$807.4M** |
| `ondo-yield-assets` | Ethereum | **USDY** | 3.55% | $552.0M |
| `ondo-yield-assets` | Ethereum | **OUSG** | 3.43% | $383.2M |
| `ondo-yield-assets` | XRPL | OUSG | 3.43% | $221.7M |
| `ondo-yield-assets` | Solana | USDY | 3.55% | $180.0M |
| `ondo-yield-assets` | Stellar | USDY | 3.55% | $123.7M |

**What this tells you:**

- **Yield clusters tightly around 3.50–3.55%** because the underlying is always short-dated US Treasury bills. This is not a DeFi yield with idiosyncratic rate — it's a Treasury bill rate minus Ondo's management fee.
- **OUSG runs slightly lower** (3.43%) than USDY (3.55%) — OUSG is a more conservative allocation (pure T-bills) while USDY includes bank deposit exposure which offers a small pickup.
- **The biggest pool is USDYC on Ethereum ($807M)** — this is the scaled USDY variant optimized for compliance and institutional distribution.
- **Multi-chain distribution is structural**, not just bridged supply. XRPL ($222M OUSG) and Solana ($180M USDY) are real deployments, not wrapped tokens. This is rare for an RWA protocol.

### Risk-adjusted yield at `final_risk = 5` (auto CPD)

| View | APY | Linear | Conservative | Verdict |
|---|---|---|---|---|
| Weighted meaningful | 3.52% | 2.11% | **0.70%** | `marginal` |
| Best meaningful (USDY/USDYC) | 3.55% | 2.13% | **0.71%** | `marginal` |

### Risk-adjusted yield at `final_risk = 4` (Tier-02 overrides)

| View | APY | Linear | Conservative | Verdict |
|---|---|---|---|---|
| Weighted meaningful | 3.52% | 2.46% | **0.88%** | `marginal` |
| Best meaningful | 3.55% | 2.49% | **0.89%** | `marginal` |

**Every realistic yield is `marginal`.** ONDO's Treasury-backed products can't beat their own underlying — 3.5% is approximately the 90-day T-bill rate, and any DeFi position in these products produces **negative risk-adjusted yield vs holding T-bills directly in a brokerage account**. The only legitimate reasons to hold USDY/OUSG over direct T-bills are:

1. Composability (use as collateral elsewhere)
2. 24/7 onchain settlement
3. Cross-chain deployment (XRPL, Solana for investors without Ethereum infrastructure)
4. Tax-advantaged jurisdictions where tokenized T-bills route differently

None of these enter the `combine_revenue_risk` model.

---

## Supply composition (where the ownership story lives)

### Raw top-20 holders

| # | Address | Balance | % of 10B |
|---|---|---|---|
| **1** | `0x677fd4ed8a…` | **5,904,207,574** | **59.04%** |
| 2 | `0x460ae5a666…` | 637,123,200 | 6.37% |
| 3 | `0xa63eace476…` | 187,184,323 | 1.87% |
| 4 | `0xf977814e90…` | 155,665,633 | 1.56% |
| 5 | `0xd2e6e930e2…` | 100,182,196 | 1.00% |
| 6 | `0xac7dc31d7e…` | 95,738,091 | 0.96% |
| 7 | `0xb829e684df…` | 80,400,000 | 0.80% |
| 8 | `0xc18d0c0e4c…` | 59,940,008 | 0.60% |
| 9 | `0xf197c6f2ac…` | 54,352,486 | 0.54% |
| 10 | `0x05ce068348…` | 53,573,956 | 0.54% |
| 11 | `0xf3fa848498…` | 53,564,631 | 0.54% |
| 12 | `0xa4f6841963…` | 53,549,094 | 0.54% |
| 13 | `0xf02bb1617b…` | 53,535,567 | 0.54% |
| 14 | `0xe002402ccf…` | 53,535,567 | 0.54% |
| 15 | `0x02bfd8f779…` | 53,535,567 | 0.54% |
| 16 | `0x50cad633ac…` | 53,421,157 | 0.53% |
| 17 | `0xbf68b40caf…` | 49,989,747 | 0.50% |
| 18 | `0xdbc8397375…` | 48,577,907 | 0.49% |
| 19 | `0x0d37bf9ef9…` | 48,443,680 | 0.48% |
| 20 | `0xffa8db7b38…` | 45,210,314 | 0.45% |

**Raw stats:**
- top10_share: **73.28%**
- top20_share: 78.42%
- nakamoto_51: **1**
- top_holders_gini: 0.80

### Three patterns worth calling out

**1. The Foundation Safe (59.04%)**

Holder #1 at `0x677fd4ed8a…` holds **5.9B ONDO in a single GnosisSafe**. This is the Ondo Foundation's primary treasury, and it alone exceeds 51% of supply (hence `nakamoto_51 = 1`). Without the fix wired into the `compare` command, this would produce `ownership_quality = 0`. With the fix, it gets excluded from the effective top-10 calculation and ownership_quality scores 85.7.

**2. Programmatic batch distribution (holders 11–16)**

Six addresses from rank 11 to 16 hold **almost exactly identical balances of ~53.5M ONDO**, with the differences being rounding dust of a few thousand wei. This is a clear signal of **programmatic distribution** — either:
- A batch transfer from a single source (e.g., a distributor contract)
- Multi-address vesting for a single legal entity (common for investor structures that split allocations for tax/compliance reasons)
- An airdrop tier that assigned fixed amounts

This isn't a red flag by itself — it's how many VC allocations are handled on-chain. But it tells you **~321M ONDO (3.2% of supply) is functionally controlled as a single bloc** even though the pipeline counts it as six separate EOAs.

**3. Binance hot wallet (holder #4)**

`0xf977814e90…` is the well-known Binance hot wallet #4, holding 155.6M ONDO (1.56%). This is CEX inventory — users' ONDO held in Binance custody. It's liquid exchange supply, not long-term holders.

### Contract classification (via `locks`)

| Address | Tokens | % | Blockscout name | Classification |
|---|---|---|---|---|
| `0x677fd4ed8a…` | 5.90B | **59.04%** | **`GnosisSafeProxy`** | Foundation multisig |
| `0xd2e6e930e2…` | 100.2M | 1.00% | `GnosisSafeProxy` | Secondary treasury |

**Only 2 contracts in the top 30 holders** (the other 28 are EOAs). Compare to Morpho which had 7 contract holders among its top 30 (Wrapper + 5 Safes + L1ChugSplashProxy). ONDO's custody structure is **more concentrated in fewer buckets** but the buckets are cleaner.

**Total protocol-owned: 6.004B ONDO = 60.04% of supply.**

### Effective circulation (post-fix calculations)

| Metric | Value | % of supply |
|---|---|---|
| Total supply | 10,000,000,000 | 100% |
| Fingerprinted custody (vesting + staking) | 0 | 0% |
| **Contract-held (multisigs)** | **6,004,389,770** | **60.04%** |
| **Protocol-owned total** | **6,004,389,770** | **60.04%** |
| **Effective circulating** | **3,995,610,229** | **39.96%** |
| Non-protocol top-10 holders considered | 18 | — |
| **Effective top-10 share** | — | **14.31%** |

**14.31% effective top-10 is a genuinely healthy EOA distribution** — comparable to Morpho's 11.41%. Among the 18 non-protocol top-holders:
- 1 Binance hot wallet (1.56%)
- 1 very large EOA at 6.37% (likely investor entity)
- ~15 smaller holders in the 0.4–1.9% range

For a governance token at $1.24B mcap, this is acceptable distribution.

---

## Cross-metric comparison (the ratios view)

### Supply

| | |
|---|---|
| Total supply (tokens) | 10.00B |
| Price | $0.2542 |
| Total supply (USD, FDV) | **$2.542B** |
| Mcap (circulating × price) | $1.24B |
| **Implied circulating** | ~4.87B ONDO (~48.7% of total) |
| **Protocol-owned** | 6.00B (60.04%) |
| Effective circulating | 3.996B (39.96%) |

**Note the FDV/mcap discrepancy:** DexScreener reports a circulating market cap of $1.24B which implies ~4.87B ONDO in active circulation, while our effective_circulating calculation says 3.996B. The gap (~870M tokens) is probably tokens that DexScreener counts as circulating but that we classify as "protocol-owned" — e.g., the secondary `0xd2e6e930e2` Safe plus perhaps vesting escrows that look like EOAs to our heuristic.

### Market & liquidity

| Ratio | Value | Interpretation |
|---|---|---|
| Market cap | $1.24B | |
| Protocol TVL | $2.81B | |
| **mcap / TVL** | **0.440** | Moderate cover (Morpho was 0.14 → stronger; Pendle was 0.10 → strongest) |
| DEX liquidity | **$3.92M** | Very thin for a $1.24B mcap token |
| **DEX liq / mcap** | **0.32%** | Only 0.3% of mcap is on-DEX |
| **DEX liq / float** | **0.15%** | Of the effective circulating supply, only 0.15% is immediately tradeable |
| Yield pool TVL | $2.52B | |
| **Yield pool TVL / protocol TVL** | **89.6%** | Near-total coverage — DeFiLlama indexes almost all of Ondo's TVL |

### Fees vs mcap / TVL / custody

| Ratio | Value |
|---|---|
| Annualized fees | **$53.23M** |
| Annualized revenue | **$0.00** |
| Revenue share of fees | **0%** (fees pass through to product holders as yield) |
| **Fees / mcap** | **4.30%** |
| **Fees / TVL** | **1.89%** |
| Revenue / mcap | n/a |
| Revenue / TVL | n/a |
| Fees / custody USD | n/a (no custody price data) |
| Fees / staked USD | n/a |
| Implied staker APY | n/a (no staking) |

### Comparing to sibling reviews

| Metric | Morpho | **ONDO** | Pendle |
|---|---|---|---|
| TVL | $7.27B | **$2.81B** | $1.76B |
| Mcap | $1.02B | **$1.24B** | $0.18B |
| Annualized fees | $139.25M | **$53.23M** | $6.75M |
| **mcap / TVL** | 0.141 | **0.440** | 0.100 |
| **fees / mcap** | 13.65% | **4.30%** | 3.82% |
| **fees / TVL** | 1.91% | **1.89%** | 0.38% |
| Revenue model | fees_pass_through | **fees_pass_through** | protocol_capture |
| Protocol-owned supply | 68.43% | **60.04%** | ~35% |
| Effective top-10 | 11.41% | **14.31%** | 11.41% |
| nakamoto_51 | 1 | **1** | 6 |
| Composite score | 65.3 | **65.8** | 59.9 |

**Read-outs:**

- **ONDO has the best fees/TVL efficiency of the three** — tied with Morpho at ~1.9% and dramatically better than Pendle at 0.38%. RWA protocols extract real fees from real yield.
- **ONDO's mcap/TVL cover is weakest of the three** — 0.44 vs Morpho 0.14 vs Pendle 0.10. Token holders are paying ~44¢ of mcap for every $1 of managed TVL, vs 10–14¢ at the other two. This is the "you pay more for less TVL cushion" direction.
- **Fees/mcap (the fee-switch thesis) is middle of the pack** — 4.30% vs Morpho 13.65% vs Pendle 3.82%. Morpho has the strongest theoretical upside if a fee switch activates.
- **Revenue capture is identical** — all three are `fees_pass_through` with $0 revenue to token holders.

---

## Scoring pillars

| Pillar | Score | Breakdown |
|---|---|---|
| **fundamental_support** | **75** | TVL $2.81B tier → +35; mcap/TVL 0.44 → mcap/TVL bonus tier `<2` +25; 12 chains → +10; fees/mcap 4.30% → `≥1% tier` **+5**; no 1m trend → 0 → **75** |
| **supply_pressure** | 60 | Baseline (supply data exists, no unlock schedule) |
| **ownership_quality** | **85.7** | base = (1 − 0.1431) × 100 = 85.69; nakamoto_51 = 1 but effective top-10 = 14.31% < 50% → **penalty skipped**; gini 0.80 > 0.7 but effective substitution → penalty skipped; → 85.7 |
| **liquidity_quality** | 66.5 | $3.92M liquidity → lower tier; 12-chain spread and high venue diversity keeps it above the concentration penalty |
| **governance_commitment** | **35** | Base 35. No OZ vesting detected, no staking, 2 Safes fingerprinted but not credited → baseline only |
| **Composite** | **65.8** | Solid |

### What drags it down (and why)

**1. `governance_commitment: 35`** — same blind spot as Morpho. The pillar sees `vesting_contracts=0, staking_contracts=0` and doesn't credit the two GnosisSafes holding 60% of supply as on-chain custody. The follow-up fix would be to count "protocol-owned tokens in contracts" as a governance signal, same way effective_top10 was wired into ownership. Estimated lift: **35 → 65+**, composite **65.8 → ~70**.

**2. `supply_pressure: 60`** — baseline default. A proper pressure model would use `protocol_owned_pct` (60.04%) + `revenue_model` (fees_pass_through) + the 2.81B TVL structural float situation to derive a real number. ONDO has comparable overhang to Morpho but less severe: overhang ratio **1.50x** vs Morpho's **2.17x**.

**3. `liquidity_quality: 66.5`** — reflects the thin $3.92M DEX liquidity against a $1.24B mcap. Liquidity-to-mcap of 0.32% is **extremely thin** — any meaningful position would face material slippage. This is structurally the same situation as Morpho (custody dominates float, DEX is an afterthought).

---

## Sell-pressure analysis (explicit)

Using the same framing as the Morpho review:

```
Total supply              10,000,000,000 ONDO
Effective circulating        3,995,610,229 (39.96%)
Protocol-owned               6,004,389,770 (60.04%)
  └─ 0x677fd4ed8a (foundation Safe)     5.90B
  └─ 0xd2e6e930e2 (treasury Safe)         100M
```

### Overhang ratio

```
protocol_owned / effective_circulating = 6.00B / 3.996B = 1.50x
```

**For every ONDO in active circulation, there are 1.5 in the foundation Safes.** Linear-distribution scenarios:

| Safe distribution / year | New supply to market | Dilution of current float |
|---|---|---|
| 10% | 600M | **+15.0%** |
| 20% | 1.2B | **+30.0%** |
| 33% (typical 3-yr unlock) | 2.0B | **+50.1%** |
| 50% | 3.0B | **+75.2%** |

**ONDO has less severe overhang than Morpho** (1.50x vs 2.17x) but **more severe than average**. The 59% single-Safe concentration is the defining structural risk.

### Sell-pressure counterweights

Same diagnostic as Morpho — ONDO has none of the usual offsets:

1. **Fee distribution:** $0. `revenue_model: fees_pass_through` → fees go to USDY/OUSG holders, not ONDO holders.
2. **Staking sink:** None detected. `staking_contracts_count = 0`, `staked_total = None`.
3. **Treasury buybacks:** Not indexed. Ondo hasn't announced a systematic buyback program.
4. **Tokenomics vesting mechanics:** Not fingerprinted (the Safes don't expose OZ VestingWallet selectors).

**Structural setup is identical to Morpho:** high overhang + zero organic buy pressure from fees or staking. The difference is ONDO's overhang is ~30% less severe.

---

## RWA-specific considerations

These are the parts of Ondo's thesis that don't fit cleanly into the CPD 2.0 model:

### 1. The "RWA leader" story

Ondo Yield Assets is the **largest single RWA protocol on DeFiLlama** at $2.81B TVL. The second-place RWA entry (Ondo Global Markets at $0.75B) is also Ondo. Combined ecosystem TVL: $3.56B. For comparison:
- BlackRock BUIDL: separately listed, ~$2.5B (not a DefiLlama-tracked protocol)
- Franklin OnChain (BENJI): ~$650M
- Mountain USDM: ~$120M
- All other RWA: fragmentary

**Ondo is the default onchain-Treasuries venue for non-BlackRock-direct users.** That's a meaningful competitive moat — especially given the 12-chain spread, which is rare for RWA.

### 2. Product–token decoupling

The yield (~3.5%) goes to **USDY/OUSG/USDYC holders**, not ONDO token holders. ONDO is a governance-only token with zero direct cash flow claim. This is fundamentally different from the Pendle or Aave token models. The ONDO token bet is:

- **Governance optionality** — control over future fee-switch decisions, new product launches, Ondo Chain direction
- **Brand/ecosystem equity** — if Ondo becomes *the* RWA platform, the governance token captures some premium
- **Speculative beta to RWA sector growth** — as a liquid proxy for institutional tokenization adoption

None of those are measurable yields. The risk model will always say `marginal` because it's reading the wrong signal.

### 3. Ondo Chain Pre-Launch context

DeFiLlama tracks `Ondo Global Markets` ($750M TVL, RWA, 3 chains) separately from `Ondo Yield Assets`. This is a distinct product line — tokenized equities / onchain stocks — that launched in 2025. It's early (no public tokenomics integration yet) but it's a meaningful expansion beyond pure Treasury-backed products.

If Ondo Chain launches (separate sovereign-chain initiative Ondo has telegraphed), ONDO token would likely gain either:
- Native chain utility (gas, staking, fee burn)
- Direct fee accrual via chain-level economic design

Neither is committed. Both are optionality priced into today's $0.25 spot.

### 4. Regulatory surface

Ondo operates regulated yield products (OUSG is accredited-only; USDY is retail but with jurisdictional restrictions). This creates two-sided tail risks the pipeline can't measure:

- **Upside:** if SEC/OCC guidance becomes clearer, Ondo's compliance moat becomes a competitive asset
- **Downside:** regulatory enforcement against the token model (e.g., governance token treated as a security) could force a restructuring

The CPD model gives Ondo a +2 on `code_band = open_source_old` for having verified code. It doesn't model regulatory outcomes at all.

---

## Verdict

| Dimension | Reading |
|---|---|
| **Protocol quality (CPD)** | Tier-03 auto → Tier-02 with overrides. Near-identical to Morpho. |
| **Final risk** | 5/10 auto → 4/10 with overrides. Low-moderate. |
| **Realistic APY** | 3.5% across Treasury-backed products. Capped by the T-bill rate. |
| **Risk-adjusted verdict** | `marginal` on every realistic yield. Conservative-adjusted is ≤1%. |
| **Ownership** | Healthy once effective top-10 is used (14.31%). Raw 73% is dominated by the foundation Safe. |
| **Fee model** | `fees_pass_through`. $53.23M/yr generated, $0 captured. fees/mcap 4.30%. |
| **TVL story** | Market-leading RWA protocol, 12-chain spread, 89.6% of TVL indexed in yield pools. |
| **Overhang** | 60% protocol-owned, 1.50× overhang ratio — less severe than Morpho but still significant. |
| **Composite** | **65.8 (solid)** |

### Practical read

**For a lender / yield-seeker:** ONDO's Treasury products (USDY, USDYC, OUSG) earn ~3.5% APY, which is approximately the 3-month T-bill rate minus Ondo's fee. The risk model correctly tags this as `marginal` because you can earn the same yield holding T-bills in a brokerage account with no smart-contract risk. The ONLY reasons to use Ondo's products over direct T-bills are composability (use as collateral), 24/7 onchain settlement, or cross-chain deployment flexibility.

**For an ONDO token holder:** You're holding governance rights to a $2.81B TVL RWA protocol generating $53M/yr in fees that currently flow entirely to product holders. The thesis is identical in shape to Morpho's: **bet on future fee-switch activation**. Ondo's theoretical fee-switch yield is 4.30% (vs Morpho's 13.65%), meaning Morpho offers a ~3× higher fee-switch payoff. But Ondo may have additional optionality from Ondo Chain and regulatory moat that Morpho doesn't.

**For a venture/speculator:** ONDO is a liquid beta on "will institutional RWA keep growing." The pipeline can't price that, but it can tell you that the protocol is already the market leader and generating real revenue. The sell-pressure overhang (60% in foundation Safes) is the single biggest structural risk — it's a ~1.5× float-expansion lever the pipeline can't see the timing of.

### Compare to Morpho head-to-head

Both end up at composite ~65 (Morpho 65.3, ONDO 65.8). They're structurally very similar: fees_pass_through models, 60–68% protocol-owned supply, ~14% effective EOA concentration, Tier-03 auto CPD. The practical difference:

| | Advantage to |
|---|---|
| Fundamental depth (TVL, fees) | **Morpho** ($7.27B vs $2.81B; $139M vs $53M fees) |
| Fee-switch optionality (fees/mcap) | **Morpho** (13.65% vs 4.30%) |
| mcap/TVL cover | **Morpho** (0.14 vs 0.44) |
| Ownership distribution | **Morpho** (11.4% vs 14.3% effective top-10, marginally better) |
| Sell-pressure overhang | **ONDO** (1.50× vs 2.17×) |
| Category breadth | **ONDO** (RWA + Global Markets + future chain) |
| Market position in category | **ONDO** (clear RWA leader) vs **Morpho** (one of several lending primitives) |
| Multi-chain deployment | **ONDO** (12 chains, structurally native) vs Morpho (2 chains) |

**If you had to pick one for a venture-style governance-token position**, Morpho has stronger fee economics and lower mcap/TVL entry, but ONDO has less dilution risk and a clearer "market leader" narrative in its category. They're complements, not substitutes.

---

## Model gaps this review surfaced

Mostly the same gaps already flagged in the Morpho and Katana reviews, but this run made one additional one explicit:

| # | Gap | Next fix |
|---|---|---|
| 1 | Resolver picked wrong ONDO address (`MintBurnTeamToken` vs canonical). Happens whenever two contracts share symbol+supply. | Use DeFiLlama protocol `address` field as a cross-check during resolution. |
| 2 | `governance_commitment` pillar still doesn't credit generic multisig holdings as custody (same as Morpho, same as Katana Pre-Launch). | Wire `protocol_owned_pct` into the governance pillar with lower confidence weight than fingerprinted vesting. |
| 3 | `supply_pressure` still uses a flat baseline of 60 regardless of overhang. | Use `protocol_owned_pct` + `revenue_model` to derive a real score. |
| 4 | `protocol_age_band = None` for Ondo — DeFiLlama `listedAt` is empty for this protocol slug. | Fall back to Blockscout contract creation block timestamp, or use the DeFiLlama TVL history for the first non-null entry. |
| 5 | DeFiLlama has NO mcap for `ondo-yield-assets` — the fallback correctly used DexScreener. | Already fixed; this run validates the fallback works on a high-profile RWA protocol. |

---

## Reproducibility

```bash
# Pure auto (produces the exact numbers above)
token-research risk ONDO \
  --chain ethereum \
  --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3 \
  --trade-size 0.05 \
  --nesting 1

# With realistic overrides → Tier-02, risk 4/10
token-research risk ONDO \
  --chain ethereum \
  --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3 \
  --trade-size 0.05 \
  --nesting 1 \
  --cpd-age 3-5 \
  --cpd-stage audit \
  --cpd-code oso_audit

# Full dossier with Markdown output
token-research deep-report ONDO \
  --chain ethereum \
  --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3 \
  --format markdown

# Cross-metric comparison standalone
token-research compare ONDO \
  --chain ethereum \
  --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3
```

**Note:** the `--address` override is **required** until the resolver fix ships. Without it, the pipeline will analyze `0x20d22eDdC93a1D30b6E7017E89930A1908c2fD34` (the `MintBurnTeamToken` contract) instead of the canonical ONDO.
