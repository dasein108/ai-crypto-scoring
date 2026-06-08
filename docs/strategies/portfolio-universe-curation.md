# Portfolio Universe Curation — Rationale and Honest Audit

> **Source of truth for the curated universe** in
> `src/token_research/prices/portfolio_universe.py`. Every name there
> got picked by hand; this document explains why each one is in, what
> was rejected and why, and the gaps the hand-picking leaves open.
>
> **A systematic, re-runnable replacement is planned in
> `docs/tasks/07-systematic-universe-filter.md`.** Until that lands, the
> universe in code is hand-curated and the rules below are normative.

## Selection criteria — what's actually applied

These are the gates a candidate must clear, in order. The first three
are mechanical; the last two are taste. Honest about which is which.

### 1. Bybit USDT-perp listing — hard requirement (mechanical)

The book is held on Bybit. Every constituent must have a tradable
`BASE/USDT:USDT` perpetual on Bybit. Anything CEX-only on Coinbase or
Kraken, anything DEX-only, anything pre-TGE, anything Bybit has
delisted in the last 90 days — out.

Verified by reading Bybit's `/v5/market/instruments-info?category=linear`
endpoint at curation time (2026-04). Re-verify before any rebuild.

### 2. Price-history resolvable — hard requirement (mechanical)

The pipeline reads daily closes from one of two paths:

- `prices/<chain>-<address>.json` for EVM-native tokens with a
  meaningful canonical contract.
- `prices/_reference/<key>.json` for non-EVM tokens (SOL, AVAX, TAO,
  RENDER, LINK) where the CEX pair *is* the canonical price.

Anything outside both paths — Cosmos-ecosystem tokens (ATOM, OSMO),
Sui, Aptos, KAS, etc. — got rejected because the existing pipeline
can't price them today. This is a cache-design constraint, not a
view on the asset.

### 3. Mcap floor and listing age — semi-mechanical

Curation rule of thumb (not enforced in code yet):

- mcap ≥ ~$200M at curation time
- listed on Bybit ≥ 180 days
- 90-day average daily volume ≥ ~$50M

These were checked manually against DeFiLlama and Bybit at
curation time. They are *not* re-checked at build time. Task 07
turns this into automated gating.

### 4. Category coverage — judgment

Targeting at least five of six portfolio buckets — L1, L2, DeFi, RWA,
AI, Infra — so the optimizer's `--max-per-category` constraint actually
binds (otherwise an undercount lets one sector dominate). The picks
favour 2-5 candidates per bucket so the optimizer has room to drop
1-2 on coverage gaps.

The category labels are *mine*. There is no industry-standard taxonomy
for these tokens, and several names sit on category boundaries
(ENA-DeFi vs RWA, RENDER-AI vs Infra, LINK-Infra vs DeFi-data). I
made the assignments below by stated primary use case; defensible
arguments exist for several alternates.

### 5. "Institutional grade" — taste

Subjective filter applied on top of (1)-(4):

**Excluded:**
- **Memes** (PEPE, WIF, BONK, DOGE) — explicit per book mandate. β-vs-BTC
  is too unstable, idiosyncratic risk doesn't compound.
- **Stablecoins** (USDT, USDC, DAI) — wrong asset class.
- **Liquid staking derivatives** (stETH, rETH, sUSDe) — beta to their
  underlying is ≈ 1 + tracking error, not a useful diversifier.
- **Recent buy-the-rumor names** (MOVE, IO at curation) — too short a
  history for a 60-day β estimate.
- **Names with active legal/regulatory overhangs** as of 2026-04.

**Borderline kept anyway:**
- **GMX** — "DeFi" but really a derivatives venue token, exposed to
  perp-volume cycles. Kept because it diversifies the DeFi sleeve away
  from pure lending (AAVE/MORPHO/UNI).
- **ENA** — categorized RWA because of USDe's t-bill backing, but it's
  arguably a stablecoin-issuer equity. Tracked.

## The 16 names — per-name rationale

### L1

| Name | β-BTC | Why |
|---|---|---|
| ETH | 1.24 | Default L1 hedge for any crypto book. Highest non-BTC liquidity, deepest derivatives market, fundamentally captures most of "non-Bitcoin crypto" theme. |
| SOL | 1.10 | Second-tier L1 with positive fees/usage trajectory. Lower β than ETH historically; complements ETH's exposure rather than duplicating it. |
| AVAX | 1.06 | Third L1 slot. Lower correlation to ETH than SOL has — different ecosystem (subnets, Coreth) than ETH-or-SOL pure-play DeFi. |

ETH is non-negotiable in any institutional crypto book.
SOL added when fee/usage trajectory turned positive in 2024; that view
hasn't reversed. AVAX is the diversifier — kept on conviction that
sovereign-chain narrative still has value.

**Considered but rejected:** SUI, APT (no EVM-readable price + new),
TON (regulatory uncertainty), DOT (declining usage), NEAR (β too
correlated to SOL).

### L2

| Name | β-BTC | Why |
|---|---|---|
| ARB | 1.06 | Largest Arbitrum-native token; tracks Arbitrum's TVL trajectory. Mature L2 with consistent fee revenue. |
| OP | 1.11 | Optimism's stack token (OP Stack adoption). Picks up Base, Worldchain, etc. via stack royalties — broader exposure than ARB. |

Two L2s in different stacks (Nitro vs OP Stack) for diversification.
**Rejected:** MATIC/POL (post-zkEVM messy), STRK (Starknet, low
liquidity at curation), BLAST (recent unlock overhang), BASE-coin
(none — Coinbase L2 has no token).

### DeFi

| Name | β-BTC | Why |
|---|---|---|
| AAVE | 1.36 | Largest DeFi blue-chip; lending captures the biggest revenue line in DeFi. High β reflects sensitivity to overall crypto risk-on. |
| UNI | 1.09 | Largest DEX. Fee-switch optionality (theoretical $1B+ ARR if turned on) is a free call option embedded in the token. |
| MORPHO | 0.91 | Outperforming peer in lending — highest-momentum lending name. Lower β than AAVE means it adds factor diversity, not just sector concentration. |
| PENDLE | 1.41 | Yield-trading primitive — orthogonal to lending/DEX. High β tells you it amplifies risk-on moves; pair with lower-β longs. |
| GMX | 1.12 | Perp DEX. Diversifies DeFi away from spot-heavy lending/DEX names; GMX revenue tied to perp volume cycles, different driver than AAVE. |

Five names because DeFi has the most distinct sub-categories
(lending, AMM, yield, perps). Five gives the `--max-per-category 4`
cap actual binding power.
**Rejected:** CRV (gov dynamics), LDO (LSD, separate exclusion), MKR
(now SKY — re-evaluate post-rebrand), SUSHI (declining), CAKE (BSC
risk concentration).

### RWA

| Name | β-BTC | Why |
|---|---|---|
| ONDO | 1.01 | Pure-play RWA — the "tokenized treasuries" theme. β ≈ 1 means it tracks crypto risk-on, but with fundamental yield underneath. |
| ENA | 1.58 | Synthetic-dollar protocol. Categorized RWA because of t-bill backing in USDe. High β — basis-trade collapse risk. Position size capped accordingly. |

RWA universe is small; these are the two biggest names. The β = 1.58
on ENA is a yellow flag — kept, but acknowledged as the highest single-name
risk slot in the book.
**Rejected:** TRU (lending RWA, low liquidity), MPL (delisted on Bybit
at curation), BLOCK (USDC-tracker, redundant with ENA).

### AI

| Name | β-BTC | Why |
|---|---|---|
| FET | 1.42 | Fetch.ai post-merger ASI alliance — largest AI-narrative token. High β = pure narrative play. |
| RENDER | 1.08 | GPU-rendering compute network. Lower β than FET because it has actual usage revenue (rendering), not just narrative. |
| TAO | 0.84 | Bittensor — most "fundamental" AI token in the sense that subnet rewards = real cash flow. Lowest β in the AI sleeve, real diversifier. |

Three AI candidates so the `--max-per-category` cap can pick the right
two. TAO especially valuable for its low β.
**Rejected:** WLD (Worldcoin, regulatory tail risk), AGIX (post-merger
already covered by FET), AKT (low liquidity), NEAR (categorized L1).

### Infra

| Name | β-BTC | Why |
|---|---|---|
| LINK | 1.05 | Chainlink — oracle infrastructure, the only mature "Infra" name. Defensive sleeve — LINK tends to lose less than DeFi blue-chips in drawdowns. |

Single name in the bucket. The Infra category exists more for
diversification accounting than for offering choice — there isn't a
second mature oracle/data-layer token to pair LINK with.
**Considered:** GRT (subgraph indexing — kept on watchlist for v2),
PYTH (newer oracle, β too volatile at curation).

### Hedge slot — BTC

Always exactly one name, always short. Bybit `BTCUSDT.P` is the deepest
perp in crypto by every measure (open interest, daily volume, bid/ask
depth at 50bps). The macro-hedge math assumes β = 1.0 vs the BTC
reference, which is true by definition.

## What this curation is NOT

- **Not a quantitative screen.** No Sharpe ranking, no momentum scoring,
  no fundamental ratios. The systematic version is task 07.
- **Not stable across regimes.** The 2026-04 picks reflect 2026-04
  market structure. KAITO, USUAL, ETHFI etc. would have been candidates
  six months earlier; some of these may be candidates six months from
  now. Hand curation drifts.
- **Not optimal.** The optimizer takes whatever universe it gets and
  produces equal-RC weights inside it. A *different* 16-name universe
  could deliver materially different results. The curation here aims
  for "defensibly diversified across crypto subsectors", not "optimal
  Sharpe".
- **Not version-controlled per-pick.** When I add or remove a name in
  `portfolio_universe.py`, this doc and any saved `PortfolioBook` get
  out of sync. The systematic version (task 07) emits a versioned
  universe JSON which solves this.

## Known gaps and biases

1. **Survivorship bias** — anything that 10x'd or -90%'d between
   curation and build day is silently ignored. Recent winners (would
   have been bought near top) and recent losers (would have been
   excluded near bottom) both distort future-looking PnL estimates.

2. **No funding-rate filter** — perps with extreme funding (some alt
   perps spiked to 80%+ APR in 2024 alt-season) are silently included.
   Long-side funding bleed and short-side funding profit aren't
   considered in the universe selection (only in the optimizer's
   `expected_carry_bps` field).

3. **No on-chain liquidity floor** — the Bybit listing implies CEX
   liquidity. It does not imply spot/DEX liquidity, which matters if
   the operator wants to fall back to non-Bybit execution during a
   venue outage.

4. **No volatility-regime check** — names that recently went through
   a 5x or -80% blow up the covariance estimate. Currently mitigated
   by the 0.2 shrinkage, but a regime-change detector that drops the
   offending name would be cleaner.

5. **Hardcoded category labels** — operator can't disagree with
   "ENA is RWA" without editing source. The systematic version emits
   category as a derived field from DeFiLlama tags + a documented
   override map.

6. **Fixed at 16 — no growth path** — if the right answer for a
   particular regime is 24 names, today's universe forces you to
   stay at 16. Task 07 makes the universe size a function of the
   filter pass-through, not a hard-coded list length.

## How to override the curation today

Without task 07 shipped, the operator's escape valve is the
`--exclude` and `--include-only` flags on `portfolio-build`:

```bash
# Drop a name you disagree with:
token-research portfolio-build v3 --exclude FET

# Run only your own subset:
token-research portfolio-build defi --include-only AAVE,UNI,MORPHO,PENDLE

# Run only your watch-list:
token-research portfolio-build hi-conv --include-only ETH,SOL,AAVE,MORPHO,ONDO,LINK
```

These do NOT add names that aren't already in the curated 16. To add
a name, edit `portfolio_universe.py` and update this document with
rationale.

## What replaces this doc when task 07 ships

The systematic filter will emit a JSON like:

```json
{
  "version": "2026-05-15",
  "filter_run_at": "2026-05-15T00:00:00Z",
  "filters_applied": {
    "min_mcap_usd": 200000000,
    "min_listing_age_days": 180,
    "min_avg_daily_volume_usd": 50000000,
    "max_funding_rate_avg_apr": 0.30,
    "max_drawdown_90d": 0.70
  },
  "passed": [
    {"symbol": "ETH", "category": "L1", "mcap": ..., "score": ...},
    ...
  ],
  "rejected": [
    {"symbol": "PEPE", "category": "Meme", "rejection_reason": "memes_excluded"},
    ...
  ]
}
```

`portfolio-build` will read this JSON instead of the hardcoded list,
and this curation doc will move from "current normative reference" to
"historical record of how the first universe was assembled".

## See also

- `docs/strategies/bullish-hedged-book.md` — the playbook this universe feeds.
- `docs/tasks/06-bullish-hedged-portfolio.md` — the design doc.
- `docs/tasks/07-systematic-universe-filter.md` — the planned replacement for this hand-curated approach.
- `src/token_research/prices/portfolio_universe.py` — the universe in code.
