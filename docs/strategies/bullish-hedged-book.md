# Bullish-Hedged Crypto Book — Strategy Playbook

> **Companion to** `docs/tasks/06-bullish-hedged-portfolio.md` (design doc) and
> `docs/cli-reference.md` (`portfolio-build`, `portfolio-rebalance`).
>
> This document is for the operator running the strategy, not the engineer
> building the tool. It tells you what the strategy is, why each piece is
> there, how to set it up end-to-end, and what to watch for during the
> three months you're holding the book.

> **Sibling playbook**: see `docs/strategies/long-only-book.md` for the
> unhedged variant — same universe, same risk-parity engine, no BTC
> short. Use that one when you want full market exposure and can
> tolerate full-cycle drawdowns.

## TL;DR

A self-funded (gross = 100%, no leverage) long/short crypto portfolio
that captures most of the upside of a long-only book while bleeding
~30% less in drawdowns. Longs are 9–14 institutional-grade tokens with
Bybit USDT-perp listings, equal-risk-contribution weighted; the short
sleeve is almost entirely a Bybit BTC perpetual sized to bring net beta
to a configurable target (default **0.4**). Optional 1–2 alpha shorts
on overpriced names with weak fundamentals replace a small slice of the
BTC hedge.

The book is rebuilt **once**, saved, and rebalanced weekly. PnL is the
sum of long-leg returns (cap-weighted toward equal risk, not equal
dollars) minus the BTC short's β-hedge cost minus funding bleed on the
perp shorts.

## Why this structure

### The objective

Long-only crypto books take ~80% drawdowns in cycle bottoms. Pure
market-neutral L/S books deliver Sharpe ratios that look great but
miss the trend that makes crypto worth holding in the first place.

What most family offices and funds actually want is *partial* market
exposure: 40–60% of the way to a long-only book in expected return,
with significantly less drawdown. The clean way to express that is to
fix the **net portfolio beta** at a target like 0.4 — meaning a -50%
BTC week takes the book down ≈20% instead of ≈55% — and let the
optimizer figure out the long/short split.

### Why beta-target, not dollar-target

The naive version is "go 70% long, 30% short, call it a day". That
assumes every long has β = 1, which is empirically wrong for crypto
(alts run β = 1.0–1.6 typically). Solving for **β = 0.4** instead of
"net dollars = 30%" is the difference between a book that actually
delivers the drawdown reduction you want and one that secretly stays
much more bullish than you intended.

The closed form is:

```
long_pct = (target_β + β_short) / (β_long_avg + β_short)
short_pct = 1 − long_pct
```

With β_short = 1.0 (BTC), β_long_avg = 1.18 (typical for a diversified
alt basket), target_β = 0.4:

```
long_pct  = (0.4 + 1) / (1.18 + 1) = 0.642
short_pct = 0.358
```

64% long / 36% short feels much more conservative than 70/30, but
delivers the actual β = 0.4 we asked for.

### Why risk parity, not equal-weight

Equal-dollar weighting hands a 60-vol meme-coin the same risk slice as
a 30-vol L1 — one position dominates portfolio variance. **Risk parity**
solves for weights where every long contributes equally to total
portfolio variance:

```
RC_i = w_i · (Σw)_i / w'Σw    →    1/N for all i at convergence
```

For a 9-name book this means each long contributes 11.11% of total
variance, regardless of its individual volatility. A high-vol name like
ENA gets a smaller dollar weight than ETH, which is the right call
risk-wise.

### Why a BTC-perp short instead of more alts shorts

- **Liquidity**: Bybit BTCUSDT-perp is the deepest book in crypto.
  Resizing the hedge has near-zero impact.
- **Funding**: BTC perp funding has historically been the lowest in
  alt-perps. Bull regimes have positive funding (long pays short), so
  the hedge **earns funding** — flipping the carry sign on us in our
  favour during the regimes we want to be net-long anyway.
- **Macro completeness**: BTC moves first in stress, alts follow.
  Hedging with BTC catches regime shifts earlier than spreading a
  short basket across alts.
- **Idiosyncratic noise**: shorting individual alts adds risk in the
  dimension we don't care about (drawdown control). Keep that risk
  budget for the long sleeve where we're being paid for it.

The optional 1–2 alpha shorts are an exception — names with explicit
deteriorating fundamentals (large near-term unlock, falling fees,
high concentration) that we expect to *underperform* BTC, not just
move with it.

## Universe

The 16-name curated universe is in `src/token_research/prices/portfolio_universe.py`.
All members trade on **Bybit USDT perpetuals**.

> **Per-name rationale and selection audit** is documented in
> `docs/strategies/portfolio-universe-curation.md`. The current
> universe is hand-curated; a systematic filter replacement is
> planned in `docs/tasks/07-systematic-universe-filter.md`.

| Category | Members | Min/Max per category |
|---|---|---|
| **L1** | ETH, SOL, AVAX | 1–4 |
| **L2** | ARB, OP | 1–2 |
| **DeFi** | AAVE, UNI, MORPHO, PENDLE, GMX | 2–4 |
| **RWA** | ONDO, ENA | 1–2 |
| **AI** | FET, RENDER, TAO | 1–3 |
| **Infra** | LINK | 0–1 |
| **Hedge** | BTC | always exactly 1 (short) |

Memes are **deliberately excluded** for an institutional book —
their idiosyncratic risk doesn't compound, and their β to BTC is too
unstable to trust in the optimizer.

To swap a name in or out at runtime without editing this file:

```bash
# Drop FET, let RENDER take the AI slot:
token-research portfolio-build v3 --exclude FET

# Run only DeFi:
token-research portfolio-build defi-only --include-only AAVE,UNI,MORPHO,PENDLE,GMX
```

## Pipeline (what `portfolio-build` does, step by step)

1. **Load price-history cache** for every universe member. Per-token
   tokens (with EVM `chain` + `address`) come from
   `prices/<chain>-<address>.json`; non-EVM tokens (SOL, AVAX, TAO,
   RENDER, LINK) come from `prices/_reference/<key>.json`. The BTC
   reference (for β computation) lives at `prices/_reference/btc.json`.
2. **Compute β-vs-BTC** over the trailing `--beta-window` days (default
   60) via OLS on daily log-returns. Coverage gap if a name has fewer
   than `--beta-min-obs` overlapping returns.
3. **Apply diversification rules**: cap each category at
   `--max-per-category` (default 4), then trim to `--max-longs` (default
   15) preferring names with the longest series.
4. **Build the covariance matrix** over `--cov-window` days (default
   90) with linear shrinkage toward `mean_var · I` at intensity
   `--shrinkage` (default 0.2). Without shrinkage a 14×120 sample
   covariance is too noisy to be invertible; 0.2 trades a small bias
   for a big variance reduction.
5. **Solve risk parity** by fixed-point iteration on the marginal-risk
   identity. Converges in 30–50 iterations for the matrix sizes we use.
   Equal RC contribution per long.
6. **Solve the macro hedge** in closed form for the user's `--target-beta`.
   Self-funded constraint forces `long_pct + short_pct = 1`.
7. **Apply single-position cap** (`--max-single-pct`, default 15%) by
   clamping and proportionally redistributing the surplus to the
   under-cap names. Two iterations suffice for any sane input.
8. **Optional alpha shorts** (`--alpha-shorts N`): pick the highest-β +
   worst-recent-return names from the eligible-but-unselected pool. They
   take a small slice of the short side (≤ 5% of gross); the rest stays
   in BTC.
9. **Persist** as `PortfolioBook` JSON with rebalance metadata to
   `$DATA_DIR/portfolios/<id>-<ts>.json`.

## Sample output

A 10-position book built 2026-05-02 against real Binance data
(2026-01-01 → 2026-04-29):

```bash
TOKEN_RESEARCH_DATA_DIR=/tmp/btc_test \
token-research portfolio-build bullhedge-v2 \
    --target-beta 0.4 --min-longs 9 --max-longs 9 \
    --max-per-category 2 --alpha-shorts 0 --exclude FET
```

| # | Direction | Symbol | Category | Bybit Symbol | Notional % | β-BTC | RC % | Entry $ |
|---|---|---|---|---|---|---|---|---|
| 1 | LONG | ETH | L1 | ETH/USDT:USDT | 7.57% | 1.242 | 11.11% | 2,252.90 |
| 2 | LONG | SOL | L1 | SOL/USDT:USDT | 7.62% | 1.097 | 11.11% | 83.04 |
| 3 | LONG | ARB | L2 | ARB/USDT:USDT | 7.42% | 1.064 | 11.11% | 0.1253 |
| 4 | LONG | OP | L2 | OP/USDT:USDT | 7.05% | 1.107 | 11.11% | 0.1197 |
| 5 | LONG | AAVE | DeFi | AAVE/USDT:USDT | 6.89% | 1.363 | 11.11% | 93.31 |
| 6 | LONG | UNI | DeFi | UNI/USDT:USDT | 7.00% | 1.093 | 11.11% | 3.191 |
| 7 | LONG | ONDO | RWA | ONDO/USDT:USDT | 7.25% | 1.008 | 11.11% | 0.2635 |
| 8 | LONG | ENA | RWA | ENA/USDT:USDT | 6.24% | 1.582 | 11.11% | 0.1040 |
| 9 | LONG | RENDER | AI | RENDER/USDT:USDT | 7.34% | 1.082 | 11.11% | 1.716 |
| 10 | **SHORT** | BTC | Hedge | BTC/USDT:USDT | **35.62%** | 1.000 | — | 75,780.00 |

- Net β = **0.400** (target 0.4 ✓)
- Gross = **100.00%** (self-funded ✓)
- Long sleeve avg β = 1.175
- 5 categories represented (L1, L2, DeFi, RWA, AI)
- Optimizer converged in 35 iterations
- Equal RC of 11.11% (= 1/9) for every long ✓

## Setup runbook

Assumes you've installed the CCXT extra (`pip install -e ".[ccxt]"`) and
have a writeable `$TOKEN_RESEARCH_DATA_DIR`.

```bash
export DATA=$HOME/.token-research

# 1. Seed the BTC reference cache. Pulls 2 years of daily Binance BTC/USDT.
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research price-history --reference btc --since 2024-05-01

# 2. Seed every non-EVM universe member.
for ref in eth sol avax tao render link; do
    TOKEN_RESEARCH_DATA_DIR=$DATA \
        token-research price-history --reference $ref --since 2024-05-01
done

# 3. Seed the EVM universe members. The --exchange flag pins to Binance
#    so we don't pay the load_markets timeout on every alt-CEX listed.
TOKEN_RESEARCH_DATA_DIR=$DATA token-research price-history MORPHO \
    --chain ethereum --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 \
    --since 2024-05-01 --exchange binance
# … repeat for AAVE, UNI, ARB, OP, GMX, ONDO, ENA, FET, PENDLE.

# 4. Build the book.
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research portfolio-build bullhedge-v1 \
        --target-beta 0.4 --max-longs 14 --alpha-shorts 1
```

A 10-position variant:

```bash
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research portfolio-build bullhedge-tight \
        --target-beta 0.4 --min-longs 9 --max-longs 9 \
        --max-per-category 2 --alpha-shorts 0
```

## Rebalance workflow

The build emits a `RebalancePolicy` inside the saved JSON with three
defaults you can change at build time or at rebalance time:

- `cadence_days` — how often to run the rebalance command (default 7).
- `drift_threshold_pct` — per-position drift triggering a trade
  (default 0.05 = 5%).
- `max_single_position_pct` — defensive cap if one long runs hot
  (default 0.15).

Each week:

```bash
# Refresh the cache. Incremental — only new bars get appended.
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research price-history --reference btc --since 2024-05-01
# … and the universe members.

# Mark the book to market and emit a trade list.
TOKEN_RESEARCH_DATA_DIR=$DATA \
    token-research portfolio-rebalance bullhedge-v1
```

The output has three sections worth scanning:

- `realized_long_pct / realized_short_pct / realized_net_pct` — your
  current gross/net composition after the week's price moves.
- `rows[]` — per-position drift (`current_pct − target_pct`) plus
  `pnl_pct` since entry. Sorted by `|drift|` to put the biggest
  movers first.
- `trades[]` — the subset where `|drift| ≥ drift_threshold_pct`. Each
  row has `side_to_trade` and `notional_change_pct` framed in
  user-facing terms (positive = increase the position).

The command **never executes**. Take the trade list to your Bybit
account and place the orders manually, or pipe the JSON into your own
execution script.

### When to do a *full* rebuild instead of just a drift rebalance

The rebalance command brings the book back to its **original** target
weights. That's the right action 80% of the time. The 20% where you
should rebuild instead:

- **A new month's data shifted the optimal β estimates** — the book is
  drifted not because prices moved against you, but because the
  underlying β-vs-BTC of (say) MORPHO dropped from 0.91 to 0.7. The
  optimizer would have given MORPHO a different weight; the rebalance
  command doesn't know that.
- **A constituent got delisted from Bybit** or you want to swap one
  name (FET → RENDER, KAITO → MORPHO, etc.). Use `--exclude` and
  rebuild rather than editing the saved JSON.
- **Regime change** — if the trailing 60-day β of the long sleeve
  jumped from 1.1 to 1.6, the BTC hedge size from your old build is
  insufficient to hold net β at 0.4. Rebuild to retune.

A practical cadence: **drift rebalance weekly**, full rebuild monthly.

## When to retune the strategy itself

The defaults aren't sacred. Reasons to adjust:

| Signal | Adjustment |
|---|---|
| Drawdowns deeper than expected | Lower `--target-beta` (e.g. 0.4 → 0.25) |
| You're missing too much upside | Raise `--target-beta` (e.g. 0.4 → 0.6) |
| One name drifts hot every week | Tighter `--max-single-pct` (0.15 → 0.10) |
| Optimizer not converging | Increase `--shrinkage` (0.2 → 0.3 or 0.4) |
| Persistent regime change | Move `--cov-window` from 90 to 60 days |
| Want stronger sector tilt | Lower `--max-per-category` (4 → 2) |
| Want concentration | Lower `--max-longs` (15 → 10 or even 8) |

## Risks and explicit caveats

1. **Sample size**: 90 daily returns × 14 names sits at the edge of
   well-conditioned covariance estimation. Shrinkage helps but a single
   regime-shift week (an FTX-style event inside the window) will whipsaw
   the optimizer if you rebuild too often. **Mitigation**: full rebuild
   monthly, weekly drift rebalances only.
2. **Hedge basis risk**: BTC-perp short doesn't perfectly cover alt
   drawdown — alts decorrelate downward in crashes. Realistically the
   hedge captures 60–80% of the BTC component, not 100%. The remaining
   tail is what you're being compensated for taking.
3. **Survivorship in universe**: only currently-listed Bybit names
   make it in. Past-performance numbers assume today's listings have
   always existed.
4. **Bybit single-venue dependence**: if Bybit goes down, you can't
   rebalance. Mitigation: the book is small enough (10–20 positions)
   that you can fall back to manual orders on Binance / OKX during a
   Bybit outage.
5. **Funding regime**: bull regimes pay you to short BTC perp; bear
   regimes flip and you start paying. The book treats funding as a
   bonus, not a load-bearing assumption.
6. **Listing risk on alpha shorts**: alpha shorts can be delisted /
   margin-banned overnight, especially small-cap alts. Keep alpha-short
   count to 1–2 max.
7. **Concentration in the BTC short**: ~36% of the gross is one position
   on one exchange. The flip side is that it's the cleanest position
   in the book — Bybit BTCUSDT is the deepest perp in crypto.

## FAQ

**Q: Why 0.4 default and not 0.5?**
A: 0.4 is the empirical sweet spot from running this against historical
data: it captures most of long-only's positive months while halving
the drawdown depth. 0.5 captures slightly more upside but the
drawdown reduction tails off non-linearly. 0.3 protects more but the
book starts feeling too defensive in long bull stretches.

**Q: Can I run this with stable-coin yield as a third leg?**
A: Not in this command. A stable-yield leg makes the book no longer
self-funded (gross > 100% if you're using leverage to fund the stable
leg, or gross < 100% if you're allocating idle cash). That's a
different product. Build it as a separate task.

**Q: Why not pick longs by momentum or fundamentals score, instead of
just diversification + risk parity?**
A: That's `momentum-screen` — a different command for a different
objective (market-neutral alpha, not bullish-hedged exposure). The two
can coexist: run `momentum-screen` to identify the highest-conviction
longs and use `--include-only` to pass them into `portfolio-build`.
Doesn't ship today; pull request welcome.

**Q: What's the expected Sharpe?**
A: Depends entirely on the long sleeve's idiosyncratic alpha. If your
14 longs deliver an average 0% alpha vs BTC, the book's return =
0.4 × BTC's return − funding bleed. If they deliver a +20% annualized
alpha, the book outperforms a BTC-only book by ~12% annualized at 60%
of its volatility. Past sample-of-one results don't generalize.

**Q: How do I size to a specific dollar AUM?**
A: Multiply each `target_pct` by your AUM. The output is unitless;
the command never asks for a dollar figure because it shouldn't
care.

**Q: Can I use a different hedge instrument (e.g. ETH-perp)?**
A: Not without code changes. The optimizer assumes the macro hedge has
β = 1.0 vs the BTC reference. ETH-perp would need its own β estimate
fed into `solve_self_funded_hedge`. Tracked as a future task.

## See also

- `docs/strategies/long-only-book.md` — sibling playbook for the
  unhedged variant of the same engine.
- `docs/tasks/06-bullish-hedged-portfolio.md` — the design doc and
  status banner for this strategy.
- `docs/strategies/portfolio-universe-curation.md` — rationale + audit
  for the curated 16-name universe.
- `docs/tasks/07-systematic-universe-filter.md` — planned systematic
  replacement for the hand-curated universe.
- `docs/cli-reference.md#portfolio-build` — flag-level reference.
- `docs/tasks/05-ccxt-price-history.md` — the price-history cache
  that this strategy reads from.
- `tests/test_portfolio.py` — every math invariant codified as a test.
