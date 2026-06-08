# Long-Only Crypto Book — Strategy Playbook

> **Companion to** `docs/strategies/bullish-hedged-book.md` (the hedged
> sibling) and `docs/cli-reference.md` (`portfolio-build --long-only`).
>
> Operator-facing: theory, runbook, when to choose this over the hedged
> book, and what to watch for during the holding period.

## TL;DR

A self-funded (gross = net = 100%) **long-only** crypto portfolio of
8–14 institutional-grade tokens with Bybit USDT-perp listings,
risk-parity weighted across categories. No hedge, no shorts, no
β-neutralization. Pure market exposure sized so each long contributes
equal portfolio variance.

Built once, saved, rebalanced weekly. PnL is the sum of long-leg
returns, weighted toward equal *risk* contribution rather than equal
*dollars*. There is no funding bleed and no hedge basis risk —
the trade-off is full crypto-beta exposure.

The same `portfolio-build` command produces it via `--long-only`, and
`portfolio-rebalance` works on the saved book without modification.

## Why long-only — and when

This is the right structure when:

- You have a **bullish thesis** and want maximum upside capture.
- You can **tolerate full-cycle drawdowns** (-50% to -70% in deep bear
  regimes are normal for crypto long-only books).
- **Funding cost on perp shorts is unacceptable** — the BTC-perp short
  in the hedged book bleeds ~5-15% APR depending on regime, which adds
  up over a multi-year hold.
- You want **operational simplicity** — one direction, no perp account,
  spot tokens are enough.
- You're running **cold capital** or non-leveraged retirement-style
  exposure, where the broker / venue restricts derivatives.

Don't run this when:

- You can't sleep through a -40% drawdown.
- You're using **leverage** anywhere else in your stack — the
  unhedged β stacks on top of leverage and you blow up faster.
- You **only have a few months** of horizon and need drawdown
  control. Crypto long-only inside short windows looks like a
  random walk.
- Your benchmark is **stable** (HFB, USD, money-market). Long-only
  crypto has the wrong shape for those.

## Long-only vs hedged — decision matrix

| Question | Long-only | Hedged β=0.4 | Hedged β=0.0 (mkt-neutral) |
|---|---|---|---|
| Captures BTC +100% rally | +108% | +43% | ~0% |
| Drawdown on BTC -50% | -54% | -22% | ~0% |
| Annual funding cost | 0 | ~5-15% APR on short notional | ~10-25% APR on short notional |
| Operational complexity | low (spot only) | medium (perp account) | high (factor stack + perps) |
| Idiosyncratic alpha | full benefit | reduced (hedge dilutes some) | full benefit |
| Maximum loss in a 90% crypto winter | -90% | ~-50% | regime-dependent, often single-digits |

Long-only and hedged are not enemies — many funds run a tactical
allocation: 70% long-only sleeve + 30% hedged sleeve. The hedged
sleeve adds drawdown floor; the long-only sleeve provides the
beta torque.

## Portfolio theory framework

Same risk-parity solver as the hedged book — the only differences:

1. **No β neutralization** — the macro-hedge equation
   `long_pct = (target_β + β_short) / (β_long_avg + β_short)`
   is not invoked. Instead `long_pct = 1.0` and the realized net beta
   is whatever the long sleeve happens to deliver (typically 1.0–1.3).

2. **No alpha shorts** — `--alpha-shorts N` is ignored under
   `--long-only` and a warning surfaces. Bearish tokens you'd otherwise
   short get *excluded* from the long sleeve via `--exclude` instead.

3. **Single-position cap matters more** — without a hedge to soak
   idiosyncratic risk, a single position blowing up hits PnL 1:1.
   Default `--max-single-pct 0.15` still applies; a stricter 0.10 is
   sensible for tighter risk budgets.

4. **Universe filter is more aggressive** — names with negative
   Sharpe or deteriorating fundamentals can stay in the hedged book
   (the optimizer needs them for diversification, the BTC short
   covers them). In long-only they're pure drag and should be dropped.

The rest is identical: equal-risk-contribution weights from a
shrunk daily-log-return covariance matrix, single-position cap,
category diversification, persistence + rebalance lifecycle.

## Universe

Same 16-name curated universe as the hedged book
(`src/token_research/prices/portfolio_universe.py`). The hedge slot
(`HEDGE_ASSET = BTC`) is **ignored** under `--long-only` — it never
gets a position.

> **Per-name rationale, exclusion criteria, and known biases** are
> documented in `docs/strategies/portfolio-universe-curation.md`. The
> systematic replacement is planned in
> `docs/tasks/07-systematic-universe-filter.md`.

For a long-only book the operator typically further trims via
`--include-only` to a high-conviction subset — the playbook below uses
9 names selected through the research lens described in
"Selection logic for long-only" below.

## Selection logic for long-only

Without a hedge to absorb losers, the universe filter is sharper.
Apply this filter on top of the curated 16:

1. **Drop negative-Sharpe names that aren't real diversifiers.** A
   name with Sharpe < -1.5 and ρ_BTC > 0.85 is just lower-return
   beta — it adds drag without diversification.
2. **Keep low-correlation diversifiers even at modest negative
   Sharpe.** A name with Sharpe -0.5 but ρ_BTC < 0.65 still
   improves portfolio Sharpe.
3. **Avoid pairs with ρ > 0.90.** Pick the better-Sharpe of the two.
4. **Always include the L1 anchor (ETH).** Even at negative Sharpe in
   bear regimes, ETH is the second-most-liquid hedge against
   "non-Bitcoin crypto" risk.
5. **Cap any single high-conviction outlier at the max-single rule.**
   A +1.89 Sharpe MORPHO is data point of one — risk-parity already
   prevents over-concentration but the cap is the explicit floor.

In Q1-Q2 2026 bear regime data, this filter eliminates:

| Dropped | Rationale |
|---|---|
| AVAX | ρ_SOL 0.91, ρ_ETH 0.89 — redundant L1 |
| LINK | ρ_ETH 0.95 — pure proxy for ETH |
| OP | Sharpe -3.07, ρ_ARB 0.86 — worst L2 |
| UNI | Sharpe -2.28, ρ_AVAX 0.90 — redundant DeFi |
| ENA | Sharpe -2.34, β 1.33 — regime-broken RWA |
| PENDLE | Sharpe -1.40 + high vol, no diversification |
| FET | Sharpe -0.22, RENDER + TAO cover AI better |

Resulting 9-name long sleeve: ETH, SOL, ARB, AAVE, MORPHO, GMX,
ONDO, RENDER, TAO.

This is a regime-conditioned filter. **Repeat the analysis at every
full rebuild** — names that get filtered today may pass next quarter
and vice versa.

## Sample output

A 9-position long-only book built 2026-05-08 against real Binance data
(2026-01-01 → 2026-04-29):

```bash
TOKEN_RESEARCH_DATA_DIR=/tmp/btc_test \
token-research portfolio-build longonly-research \
    --long-only --min-longs 9 --max-longs 9 \
    --include-only ETH,SOL,ARB,MORPHO,AAVE,GMX,ONDO,TAO,RENDER
```

| # | Symbol | Category | Bybit | Notional % | β-BTC | RC % | Entry $ |
|---|---|---|---|---|---|---|---|
| 1 | ETH | L1 | ETH/USDT:USDT | 11.23% | 1.242 | 11.11% | 2,252.90 |
| 2 | SOL | L1 | SOL/USDT:USDT | 11.24% | 1.097 | 11.11% | 83.04 |
| 3 | ARB | L2 | ARB/USDT:USDT | 11.62% | 1.064 | 11.11% | 0.1253 |
| 4 | AAVE | DeFi | AAVE/USDT:USDT | 10.49% | 1.363 | 11.11% | 93.31 |
| 5 | MORPHO | DeFi | MORPHO/USDT:USDT | 10.93% | 0.908 | 11.11% | 1.963 |
| 6 | GMX | DeFi | GMX/USDT:USDT | 12.57% | 1.119 | 11.11% | 7.35 |
| 7 | ONDO | RWA | ONDO/USDT:USDT | 10.82% | 1.008 | 11.11% | 0.2635 |
| 8 | RENDER | AI | RENDER/USDT:USDT | 10.47% | 1.082 | 11.11% | 1.716 |
| 9 | TAO | AI | TAO/USDT:USDT | 10.63% | 0.842 | 11.11% | 254.00 |

- **Gross = Net = 100.00%** ✓ (self-funded, all long)
- **Net β = 1.081** (= long-leg β, no hedge offset)
- 5 categories represented (L1, L2, DeFi, RWA, AI)
- Equal RC = 11.11% per name (= 1/9) ✓
- Optimizer converged in 37 iterations
- Single-position cap not triggered (max GMX 12.57% < 15%)
- Largest single name: GMX at 12.57% (vs BTC short 32.7% in the
  hedged sibling — long-only has no concentrated short slot, so
  individual longs are 1.5× larger to fill the same gross)

## Setup runbook

Identical to the hedged book except for the build step:

```bash
export DATA=$HOME/.token-research

# 1. Seed BTC reference (still needed for β computation, even though
#    no BTC position will be opened).
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research price-history --reference btc --since 2024-05-01

# 2. Seed non-EVM members.
for ref in eth sol avax tao render link; do
    TOKEN_RESEARCH_DATA_DIR=$DATA \
        token-research price-history --reference $ref --since 2024-05-01
done

# 3. Seed EVM members. Repeat for the names you want.
TOKEN_RESEARCH_DATA_DIR=$DATA token-research price-history MORPHO \
    --chain ethereum --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 \
    --since 2024-05-01 --exchange binance

# 4. Build with --long-only.
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research portfolio-build longonly-v1 \
        --long-only --max-longs 9 --max-per-category 4
```

## Rebalance workflow

`portfolio-rebalance` works without modification on long-only books —
the saved JSON has zero short positions, so the drift loop simply
iterates fewer rows.

```bash
# Refresh cache (incremental — only new bars get appended).
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research price-history --reference btc --since 2024-05-01

# Mark to market and emit drift list.
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research portfolio-rebalance longonly-v1
```

The drift logic is identical: positions where
`|current_pct − target_pct| ≥ drift_threshold_pct` flip
`needs_trade=True`. Trade direction:

- **Drift positive** (position grew above target) → `side = "sell"`
- **Drift negative** (position shrank below target) → `side = "buy"`

No special-case for short positions because there are none.

## When to do a full rebuild instead of just drift rebalance

Same heuristic as the hedged book, plus one long-only-specific signal:

- **Realized long-leg β shifted materially** — if the trailing 60-day
  β of your sleeve drifts from 1.08 → 1.40 over a quarter, the book
  now has more market exposure than you signed up for. The hedged
  sibling auto-resizes the BTC short on rebuild; long-only has no
  such mechanism. **Action**: drop the highest-β name and re-run.
- **A new high-Sharpe name appears in the universe** — under
  long-only, every dollar in a -1.5 Sharpe name is a dollar not in a
  +1.5 Sharpe name. Worth a full rebuild whenever the watchlist has
  a Sharpe-positive entrant.
- **The 5×-pump rule** — if any constituent runs +200% inside the
  rebalance window, mean-reversion risk + position-cap pressure
  warrants a full rebuild rather than just trimming the winner.

A practical cadence: **drift rebalance weekly**, full rebuild monthly,
defensive rebuild any time long-leg β drifts > 0.20 from build-time.

## When to retune the strategy itself

| Signal | Adjustment |
|---|---|
| Drawdowns deeper than expected | Switch to hedged book at β = 0.4 — long-only is the wrong product for you |
| One name drifts hot every week | Tighter `--max-single-pct` (0.15 → 0.10) |
| Optimizer not converging | Increase `--shrinkage` (0.2 → 0.3) |
| Universe has too many losers | Tighter `--include-only` filter; shrink to 6-7 high-conviction names |
| Beta drifting too high | Drop the highest-β name and rebuild; or `--exclude` it |
| Want concentration on top picks | Lower `--max-longs` (15 → 8 → 6) |
| New high-conviction name | `--include-only` extended; full rebuild |

## Risks and explicit caveats

1. **Full-cycle drawdown exposure**. -70% to -85% drawdowns in a deep
   crypto winter are mathematically expected. Long-only has no
   built-in protection. **Mitigation**: combine with a separate
   stable-yield sleeve outside this book, or keep allocation small
   enough that -70% on this slice doesn't ruin total portfolio.

2. **Sample-size β estimates**. 60-90 days of daily returns is too
   little to estimate β confidently for thin-volume names like TAO or
   RENDER. The risk-parity weights inherit that uncertainty —
   small-cap positions get sized as if their measured vol was
   accurate, but realized vol can be 1.5× higher.

3. **Positive feedback loop with risk parity**. Risk parity gives the
   *least* weight to the *most* volatile name — but the most volatile
   name is also typically the highest-conviction outperformer
   (MORPHO in this regime). The result: a +78% Sharpe-1.89 name gets
   only ~11% weight, just like a flat -10% performer with the same
   vol. Risk parity is regime-agnostic by design; that's a feature in
   bull regimes (caps the high-flier exposure) and a bug in late-bull
   regimes (you're under-weighting the winner).

4. **Survivorship in universe**. Names that delisted between
   curation and build day are silently absent. Past-performance
   numbers assume today's universe always existed.

5. **Bybit single-venue dependence**. If Bybit goes down, you can't
   rebalance. With long-only on spot tokens this is less acute than
   the hedged book (where the BTC perp short would be stuck) — you
   can fall back to manual orders on Binance or Coinbase using the
   same `bybit_symbol` resolved to its spot equivalent.

6. **No idiosyncratic-loss insurance**. In the hedged book, the
   BTC short cushions a -30% MORPHO surprise. In long-only, MORPHO
   -30% = ~3.3% portfolio drawdown, no offset. **Mitigation**:
   tighter single-position cap.

7. **Net β drift over the holding period**. If MORPHO outperforms
   and grows from 11% → 18% of the book before rebalance, and its
   β is 0.91 (lower), realized net β actually drifts *down* — your
   book becomes less market-correlated over time. Sounds nice but
   the converse is also true (a high-β winner pulls β up). Rebuild
   monthly to keep this honest.

## FAQ

**Q: Why not just buy ETH?**
A: ETH is 35% of crypto market cap and 70% of "professional" crypto
exposure outside BTC. Buying ETH alone gives you ETH-specific risk
(Layer 1 competition, gas-fee dynamics, staking yield mechanics). A
9-name diversified book mutes single-protocol risk while keeping
overall β similar. ETH's β-vs-BTC of 1.24 is similar to this book's
1.08 — small β saving, much bigger idiosyncratic-risk reduction.

**Q: Why not equal-weight (1/N) instead of risk parity?**
A: Equal-dollar weight gives the highest-vol name the largest risk
share. With one 100%-vol meme-coin in a basket of 30%-vol L1s, that
single position dominates portfolio variance. Risk parity scales each
weight by `~1/σ_i` so risk shares equalize. For a curated universe
without memes, the difference is modest (5-15% relative variance);
for unfiltered universes it's massive.

**Q: How do I scale to a target dollar AUM?**
A: Multiply each `target_pct` by your AUM. The output is unitless;
the command never asks for a dollar figure because it shouldn't care.

**Q: Why is BTC reference cache still required?**
A: β-vs-BTC is computed for each long even though no BTC position
opens. The realized long-leg β is reported and can drift over time —
without it you'd lose visibility into what market exposure you're
actually carrying.

**Q: Should I use spot or perps for the longs?**
A: For long-only, spot is cleaner — no funding cost, no
liquidation risk on the long side. The `bybit_symbol` field carries
the perp form (`BASE/USDT:USDT`) by default; the operator can swap
to the spot symbol (`BASE/USDT`) at execution time if their venue
supports it. Spot is the recommended default for long-only books.

**Q: Can I add more than 15 names?**
A: Yes — pass `--max-longs 20` or higher. The optimizer scales fine
to ~30-40 names; beyond that the covariance matrix gets numerically
unstable even with shrinkage. Practical sweet spot is 8-15: enough
diversification, low enough position count that you can sanity-check
each line manually.

**Q: What's the expected Sharpe?**
A: Depends on the long sleeve's idiosyncratic alpha. If your 9 longs
collectively deliver an average 0% alpha vs BTC, the book's Sharpe ≈
BTC's Sharpe over the same period. If they deliver +20% annualized
alpha, the book's Sharpe is 0.5-0.8 above BTC's. Past sample-of-one
results don't generalize.

## See also

- `docs/strategies/bullish-hedged-book.md` — the hedged sibling that
  uses the same engine with a BTC-perp short.
- `docs/strategies/portfolio-universe-curation.md` — rationale + audit
  for the curated 16-name universe.
- `docs/tasks/07-systematic-universe-filter.md` — planned systematic
  replacement for the hand-curated universe.
- `docs/cli-reference.md#portfolio-build` — flag-level reference,
  including `--long-only`.
- `tests/test_portfolio.py::test_portfolio_build_long_only_has_no_short`
  — math invariants under long-only mode.
