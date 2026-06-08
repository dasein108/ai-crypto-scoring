# Trading Signals Protocol

How to use the `token-research` pipeline to produce **mid/long-term and swing
trading signals** on governance tokens. Think of this as the trading-desk
complement to [`risk_revenue_estimation.md`](risk_revenue_estimation.md): the
risk doc tells you how to size a position; this doc tells you *when* to enter,
hold, or exit.

Sibling reviews that ground the metrics below in real data:
[`morph_risk_revenue.md`](../examples/morpho-deep-analysis.md),
[`ondo_analysis.md`](../examples/ondo-analysis.md),
[`katana_analysis.md`](../archived/docs/katana_analysis.md),
[`hbar_analysis.md`](../archived/docs/hbar_analysis.md).

---

## TL;DR

The pipeline emits ~40 metrics across 18 commands. Most are noise for
trading purposes. The **6 highest-signal inputs** for a quant trader are:

1. **`overhang_ratio`** (from `compare.supply`) — structural supply overhang
2. **`fees_to_mcap_ratio`** + **`revenue_model`** (from `yields`) — fundamental yield
3. **`liquidity_to_float_usd`** (from `compare.market`) — exit-liquidity stress test
4. **`effective_top10_pct_of_supply` delta** (from `trend`) — accumulation / distribution
5. **`flows.netflows[cex]`** direction — short-term momentum (1–2 week lead)
6. **New top-30 wallets** (from `trend`) — whale entry / exit

Two commands aggregate these:

- **`trend`** — stores snapshots and computes deltas over a `--window` (default 30 days)
- **`signal`** — applies a weighted formula to current state + trend deltas to produce a single `signal_score` ∈ [−10, +10] with a verdict (`strong_accumulate` / `accumulate` / `neutral` / `reduce` / `strong_reduce`)

---

## The 6 high-impact metrics in detail

### 1. `effective_overhang_ratio` — structural supply pressure

**Source:** `compare.supply.effective_overhang_ratio` (and `effective_overhang_pct_of_supply`)

**Formula:**

```
effective_overhang_pct = protocol_owned_pct + max(0, effective_top10_pct - 20)
effective_overhang_ratio = effective_overhang_pct / (100 - effective_overhang_pct)
```

The 20% top-10 ceiling is the "reasonable healthy distribution" threshold. Concentration above it is treated as structural overhang regardless of whether it's in contracts or EOAs.

**Why the `max(0, effective_top10 - 20)` term exists:** Without it, the formula only counts contract-held supply (DAO treasuries, wrappers, multisigs). That works for mature DeFi tokens like Morpho and ONDO where most of the concentration lives in GnosisSafes. It **breaks** for freshly-launched tokens where team/investor allocations sit in EOAs rather than multisigs — the KAITO case.

**Reference readings (the formula preserves prior behavior where EOA concentration is low):**

| Token | `protocol_owned_pct` | `effective_top10_pct` | `effective_overhang_pct` | **`effective_overhang_ratio`** |
|---|---|---|---|---|
| Morpho | 68.43% | 11.41% | 68.43% | **2.17×** (severe — correct) |
| ONDO | 60.04% | 14.31% | 60.04% | **1.50×** (elevated — correct) |
| Pendle | 35.46% | 11.41% | 35.46% | **0.55×** (healthy — correct) |
| **KAITO** | **12.96%** | **82.25%** | **75.21%** | **3.04×** (severe — FIXED) |

The legacy `protocol_owned / effective_circulating` formula gave KAITO a "distributed" read of **0.15×** — the wrong answer by 20× because 82% of KAITO's "effective float" is held by 10 EOAs that behave like locked supply but aren't in contracts.

**Reading table (unchanged):**

| `effective_overhang_ratio` | Reading | Action |
|---|---|---|
| **≤ 0.3** | Distributed, low near-term dilution | **Accumulate** on weakness |
| 0.3–0.8 | Healthy | Size normally |
| 0.8–1.5 | Moderate | Watch vesting events |
| 1.5–2.5 | Elevated (ONDO class 1.50×, Morpho class 2.17×) | **Wait for dilution**, then buy the dip |
| **≥ 2.5** | Severe (KAITO class 3.04×) | **Avoid spot longs**; hedged structures only |

**Why the 20% ceiling is the right threshold:** A "healthy" governance token distribution has top-10 holders controlling ~10–20% of supply. Above 20%, the effective top-10 is no longer "retail + MMs" — it's "insiders and whales who can move price." That concentration is functionally equivalent to a vesting cliff even when the tokens are technically transferable.

**Signal quality:** **Very high** for all tokens, especially tokens <2 years old. Decays to noise once vesting completes AND top-10 concentration stabilizes below 20%.

**Signal weight in the `signal` command: 1.5** (dominant structural force)

---

### 2. `fees_to_mcap_ratio` × `revenue_model`

**Source:** `yields.fees_to_mcap_ratio` + `yields.revenue_model`

**Why it matters:** Tells you what fraction of market cap is generated in fees annually, and whether those fees *reach token holders* (`protocol_capture`) or *pass through to LPs/users* (`fees_pass_through`). The combination defines four quadrants:

|  | **High fees/mcap (≥8%)** | **Low fees/mcap (<3%)** |
|---|---|---|
| **`protocol_capture`** | ★ Strong buy — real cash flow yield + reinvestment optionality | Hold — priced for growth, not yield |
| **`fees_pass_through`** | Speculative buy — optionality on future fee-switch vote | Marginal — nothing to anchor value |

**Reference points from the sibling reviews:**

| Token | fees/mcap | revenue_model | Reading |
|---|---|---|---|
| **Morpho** | **13.65%** | `fees_pass_through` | Highest theoretical fee-switch payoff in dataset; bet is governance activation timing |
| **ONDO** | 4.30% | `fees_pass_through` | Moderate optionality; trade on RWA category leadership, not fee number |
| **Pendle** | 3.82% | `protocol_capture` | Real cash flow but thin — value capture is already priced |

**The `signal` formula weights `protocol_capture` at full strength and `fees_pass_through` at 50%** — optionality is real but uncertain.

**Signal weight: 1.5**

---

### 3. `liquidity_to_float_usd`

**Source:** `compare.market.liquidity_to_float_usd`

**Why it matters:** Tells you **how much of the effective-circulating float you can actually sell without moving price**. Not a directional signal — a **sizing constraint**.

| liq / float | Action |
|---|---|
| **< 0.5%** | Very thin — DCA over 30+ days, expect 3–10% slippage on exit |
| 0.5–2% | Normal DeFi token — standard market-maker depth |
| **≥ 5%** | Highly liquid — trade it like a blue chip |

**Combined with overhang:** `liquidity_to_float < 1%` + `overhang_ratio > 2` = the "Morpho-class sell-pressure" setup. Any dilution event will move price severely. Size accordingly.

**Signal weight: 1.0** (size limit, not direction)

---

### 4. `effective_top10_pct_of_supply` delta (accumulation / distribution)

**Source:** `trend.deltas.supply.effective_top10_pct_of_supply`

**Why it matters:** **The single best mid-term directional signal the pipeline can produce.** The current snapshot tells you how concentrated the effective float is today; the **delta over 30 days** tells you whether smart money is accumulating or distributing.

| Δ `effective_top10` (30d) | Interpretation | Typical price lead |
|---|---|---|
| **+10% or more** | Strong accumulation — non-protocol holders concentrating | 2–8 weeks before upward re-rating |
| +3% to +10% | Accumulation | 4–12 weeks |
| −3% to +3% | Flat / noise | — |
| −3% to −10% | Distribution | 2–8 weeks before drawdown |
| **−10% or more** | Strong distribution | 1–4 weeks |

**Why the leads are that long:** on-chain accumulation shows up in the top-10 *before* the price move because sophisticated wallets typically build positions over weeks, exit CEX inventory, and only then does price follow as retail catches on.

**Signal weight: 2.0** (highest in the formula)

---

### 5. `flows.netflows[cex]` direction

**Source:** `flows.netflows` (requires `TOKEN_RESEARCH_DUNE_API_KEY`)

**Why it matters:** Token transfers to/from centralized exchanges are a 1–2 week leading indicator of sell/hold intent.

| CEX net (7d) | Reading |
|---|---|
| **Strongly negative** (outflow to self-custody) | **Accumulation to cold storage** — bullish |
| Mildly negative | Organic holding — neutral-bullish |
| Mildly positive | Rotation — some profit-taking |
| **Strongly positive** (inflow to exchanges) | **Sell intent building** — bearish |

**Signal quality:** Very high for 1–2 week swing trades, decays fast. This is the highest-value signal on a weekly cadence, provided the Dune integration is stable (nonce fix shipped in this round).

**Signal weight: 1.5**

---

### 6. New top-30 wallets (whale entry / exit)

**Source:** `trend.holder_changes`

**Why it matters:** When a wallet appears in the top-30 that wasn't there last snapshot, that's a new large holder starting a position. Conversely, a wallet leaving the top-30 is a holder liquidating. The `trend` command also tracks holders that stayed in the top-30 but grew or shrunk their position by ≥10%.

**Reading:**

- **New top-30 address + no treasury distribution event** → whale opened a position (bullish, especially in multiples)
- **Top-30 address grew significantly** → existing holder added (mild bullish)
- **Top-30 address left** → holder fully exited (bearish)
- **Top-30 address shrunk significantly** → partial exit (mild bearish)
- **2+ new whales in a single window** → coordinated accumulation, possible narrative forming

**Signal weight: 1.5**

---

## The `signal` composite formula

Implemented in [`commands/signal.py`](../src/token_research/commands/signal.py). Each component is a bounded score in the range [−2, +2]. The weighted sum is then clamped to [−10, +10].

| Component | Weight | Score range |
|---|---|---|
| `overhang` | 1.5 | −2 (severe) … +2 (distributed) |
| `fee_yield` (structural) | 1.5 | 0 … +2 |
| `exit_liquidity` | 1.0 | −1 (very thin) … +1 (deep) |
| `cex_flows` | 1.5 | −1 (inflow) … +1 (outflow) |
| `accumulation_trend` (from trend) | 2.0 | −2 (strong dist) … +2 (strong acc) |
| `fee_momentum` (from trend) | 1.0 | −1.5 (decay) … +1.5 (momentum) |
| `whale_events` (from trend) | 1.5 | −2 … +2 |

**Maximum possible score** (perfect conditions): `1.5×2 + 1.5×2 + 1.0×1 + 1.5×1 + 2.0×2 + 1.0×1.5 + 1.5×2` = **16.0**, clamped to **+10**.

**Verdict thresholds:**

| Score | Verdict |
|---|---|
| ≥ +4 | `strong_accumulate` |
| +1.5 to +4 | `accumulate` |
| −1.5 to +1.5 | `neutral` |
| −4 to −1.5 | `reduce` |
| ≤ −4 | `strong_reduce` |

When `coverage_ratio < 0.5` (half the signals have no data — usually because `trend` has no baseline yet), the verdict is downgraded to `*_low_confidence`.

---

## Operational workflow

### Step 1 — Build your watchlist

Create a list of tokens you want to track. For each, run once to prime the snapshot store:

```bash
for token in MORPHO ONDO PENDLE LDO AAVE; do
  token-research trend $token --chain ethereum --address 0x...
done
```

Each run persists a snapshot under `$TOKEN_RESEARCH_DATA_DIR/trends/ethereum-$token/$DATE.json`.

### Step 2 — Establish a cadence

Run `trend` on the watchlist on a schedule. The pipeline doesn't need daily resolution — **weekly is typically ideal** for mid-term signals, daily is overkill unless you're running swing trades off the CEX netflow signal.

```bash
# crontab entry — Monday 09:00 UTC
0 9 * * 1 cd /path/to/crypto-research && ./run-weekly-trends.sh
```

### Step 3 — Pull signals when making a decision

```bash
# Single-token signal
token-research signal MORPHO --chain ethereum --address 0x58D97B... --window 30

# Compare two tokens before rotating
token-research signal MORPHO --chain ethereum --address 0x58D97B... --format json | jq .metrics.signal_score
token-research signal ONDO --chain ethereum --address 0xfABA6f... --format json | jq .metrics.signal_score
```

The `signal_score` is directly comparable across tokens, so you can rank a watchlist by it.

### Step 4 — Cross-check with risk

`signal` tells you *direction*; `risk` tells you *size*. The right workflow:

```bash
# 1. Score the position size ceiling
token-research risk MORPHO --chain ethereum --address 0x58D97B... --trade-size 0.05

# 2. Decide direction
token-research signal MORPHO --chain ethereum --address 0x58D97B... --window 30

# 3. Combine:
# - final_risk 5/10 + signal_score +3 → enter 5% position
# - final_risk 9/10 + signal_score +5 → still pass (risk too high even with positive signal)
# - final_risk 3/10 + signal_score −2 → pass (good structure but wrong timing)
```

---

## Accumulation phase detection — what to look for

You asked specifically about detecting "accumulation phase is over." Here are the concrete patterns:

### Accumulation in progress (all bullish, look for clusters)

1. **`effective_top10_pct_delta_30d` positive** (non-protocol holders concentrating)
2. **`flows.netflows[cex]` net outflow** (tokens moving to cold storage)
3. **2+ new wallets in top-30** appearing in consecutive windows
4. **`fees_to_mcap_ratio` rising without price rising** — yield compression, pre-rerating
5. **`protocol_owned_pct` stable or slightly falling** as distribution happens to retail, not insiders
6. **`liquidity_by_dex` diversifying** — new venues appearing
7. **`yield_pool_count` rising** — protocol integrations deepening

### Accumulation ending / distribution starting (all bearish)

1. **`effective_top10_pct_delta_30d` plateaus then flips negative**
2. **`flows.netflows[cex]` net inflow** — first weekly flip is the signal
3. **Whales leaving top-30** — appear as `departed_from_top30` in the trend output
4. **`liquidity_to_float` declining** while mcap stable (market makers pulling depth)
5. **`fees_to_mcap_ratio` declining** while price is flat or rising (fundamentals detaching from price)
6. **New 1%+ wallet appearing that's NOT a known multisig** — new insider distribution address being used

### The composite "phase" reading

```
If all of:
  - overhang_ratio ≤ 1.5
  - effective_top10_delta_30d > +3%
  - cex_net_7d < 0
  - fees_to_mcap > 5%
→ Accumulation phase confirmed, mid-term bullish

If all of:
  - effective_top10_delta_30d < −3%
  - cex_net_7d > 0
  - ≥1 wallet departed top-30
  - fees_to_mcap_delta_30d < 0
→ Distribution phase confirmed, reduce exposure
```

---

## What's intentionally NOT in the formula

- **CPD total score** — too slow-moving / qualitative. Good for risk sizing, not entry timing.
- **`fundamental_support` pillar** — lags price by months because DeFiLlama TVL lags.
- **Verdict strings from `risk`** — nominal thresholds; use the numerical `conservative_adjusted_apy` if you need it.
- **Nakamoto-51 raw value** — misleading for any protocol with DAO treasury.
- **Composite `score.composite_score`** — the 5-pillar composite is designed for risk assessment, not trading. Don't mix the two.

---

## Limitations of this approach

1. **30-day minimum lead time** — the trend signals require a baseline. You can't run `trend` once and get delta signals; you need at least two runs separated by your window.
2. **CEX flow signal depends on Dune** — when Dune is slow / stuck, the flow component becomes `no_data`, dropping coverage_ratio below 0.5. The Dune nonce fix (shipped alongside these commands) mitigates the stuck-execution problem but network latency remains.
3. **New-whale detection is raw-balance-based** — we don't currently convert raw uint256 to percentages (decimals aren't always available in the top-holder snapshot). Known enhancement: store decimals in the snapshot so whale sizes can be expressed as % of supply.
4. **CEX flows are a 7-day window** — suitable for 1–2 week swing decisions, not daily. If you need daily momentum, run it daily and track the series of net_raw values over time.
5. **No price history** — the pipeline doesn't yet store historical price to compute return-based regressions. A future enhancement would be to pull OHLCV from DefiLlama's historical price endpoint for each snapshot.
6. **Signals work best on mid-cap DeFi governance tokens** — the categories these metrics work for are: lending, DEX, RWA, yield, derivatives. They work less well on: memecoins (overhang is meaningless), L1 native tokens (use `chain-report` instead), stablecoins (yield-only).
7. **The formula is not machine-learned** — it's a hand-crafted heuristic based on the reasoning in this doc. A future enhancement would be to backtest the composite against historical returns and re-weight.

---

## File map

| File | Role |
|---|---|
| `docs/trend_signals.md` | This document |
| `docs/risk_revenue_estimation.md` | Sizing protocol (sibling) |
| `src/token_research/commands/trend.py` | Snapshot + delta tracking |
| `src/token_research/commands/signal.py` | Composite scoring formula |
| `src/token_research/commands/compare.py` | Current-state supply / market / fees / yield ratios |
| `src/token_research/commands/flows.py` | CEX / DEX / bridge netflows (Dune-gated) |
| `src/token_research/commands/holders.py` | Top-30 holder snapshot (Blockscout-gated) |
| `src/token_research/commands/yields.py` | Protocol fees / revenue / revenue_model |

Storage layout:

```
$TOKEN_RESEARCH_DATA_DIR/
  trends/
    ethereum-morpho/
      2026-04-11.json
      2026-04-18.json
      2026-04-25.json
      ...
```

Each file is ~2–5 KB. A watchlist of 20 tokens run weekly accumulates ~2 MB/year of history.
