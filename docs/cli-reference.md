# CLI Reference

Up-to-date as of **2026-04-11**.

## Usage

```
token-research <command> <query> [options]
```

`<query>` can be a ticker (`ETH`), project name (`Ondo`), or token address (`0x...`).

## Global options

| Flag | Description |
|---|---|
| `--chain <name>` | Target chain (default: `ethereum`). Supported: `ethereum`, `base`, `arbitrum`, `optimism`, `monad`. |
| `--address <0x...>` | Explicit token address override, bypassing the resolver. Required for non-`resolve` commands when the resolver picks the wrong candidate. |
| `--with-premium` | Enable premium provider slots (Tokenomist, Arkham, Messari). |
| `--format <json\|markdown>` | Output format. `markdown` is **only available for `deep-report`**; all other commands emit JSON. |

`deep-report` also accepts `--no-persist` to skip writing the report JSON to disk.

## Environment variables

All optional except where noted. Prefix: `TOKEN_RESEARCH_`.

| Variable | Purpose |
|---|---|
| `TOKEN_RESEARCH_DATA_DIR` | Data directory (default: `.token-research`). Raw responses, reports, and caches live here. |
| `TOKEN_RESEARCH_OFFLINE` | Set to `1` to skip all network calls. Explicit shell env wins over `.env`. |
| `TOKEN_RESEARCH_REQUEST_TIMEOUT_SECONDS` | HTTP timeout (default: `4.0`). |
| `TOKEN_RESEARCH_ETH_RPC_URL` | Ethereum JSON-RPC endpoint. |
| `TOKEN_RESEARCH_BASE_RPC_URL` | Base JSON-RPC endpoint. |
| `TOKEN_RESEARCH_ARB_RPC_URL` | Arbitrum JSON-RPC endpoint. |
| `TOKEN_RESEARCH_OP_RPC_URL` | Optimism JSON-RPC endpoint. |
| `TOKEN_RESEARCH_BLOCKSCOUT_API_KEY` | Blockscout API key. |
| `TOKEN_RESEARCH_ETHERSCAN_API_KEY` | Etherscan API key. |
| `TOKEN_RESEARCH_DUNE_API_KEY` | Dune Analytics API key — enables `flows` and richer `labels`. |
| `TOKEN_RESEARCH_FOOTPRINT_API_KEY` | Footprint Analytics API key. |
| `TOKEN_RESEARCH_FLIPSIDE_API_KEY` | Flipside Crypto API key. |
| `TOKEN_RESEARCH_MOBULA_API_KEY` | Mobula API key. |
| `TOKEN_RESEARCH_ARKHAM_API_KEY` | Arkham Intelligence API key. |
| `TOKEN_RESEARCH_TOKENOMIST_API_KEY` | Tokenomist API key (required for premium `unlocks`). |
| `TOKEN_RESEARCH_MESSARI_API_KEY` | Messari API key. |

The CLI walks up from `__file__` to find `.env` automatically — you don't need to run it from the project root. Explicit shell env vars always beat `.env` values.

---

## Typical workflows

### Research a token

```bash
token-research deep-report MORPHO --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 --format markdown
```

### Evaluate a pre-TGE farming program

```bash
token-research prelaunch Katana --cpd-stage beta
```

### Analyze a non-EVM L1

```bash
token-research chain-report Hedera
token-research chain-report Solana
```

### Track a watchlist and generate trading signals

```bash
# 1. prime the snapshot store
token-research trend MORPHO --chain ethereum --address 0x58D97B...
token-research trend ONDO  --chain ethereum --address 0xfAbA6f...
token-research trend PENDLE --chain ethereum --address 0x808507...

# 2. (run weekly on cron) — just re-run the above

# 3. when making a decision, pull the composite signal
token-research signal MORPHO --chain ethereum --address 0x58D97B... --window 30
```

See [`docs/trend_signals.md`](trend_signals.md) for the trading framework.

### Size a position with risk ceiling + directional signal

```bash
# Risk ceiling
token-research risk MORPHO --chain ethereum --address 0x58D97B... --trade-size 0.05

# Directional signal (combine with the risk number)
token-research signal MORPHO --chain ethereum --address 0x58D97B... --window 30
```

### Screen the market for growth candidates

```bash
# Default: TVL $500K–$500M, top 10
token-research screen growth

# Filter to lending, wider funnel
token-research screen growth --category lending --top 20 --stage3-top 50

# Micro-cap focus
token-research screen growth --min-tvl 100000 --max-tvl 10000000 --top 15
```

---

## Commands at a glance

| Command | Purpose | Key providers |
|---|---|---|
| `resolve` | Query → canonical token address | DexScreener + DeFiLlama cross-check |
| `identity` | Contract metadata verification | Blockscout |
| `price` | Spot price with cross-source validation | DexScreener + DeFiLlama |
| `pools` | DEX pool discovery | DexScreener |
| `liquidity` | Depth + venue distribution + **LP lock analysis** | DexScreener + Blockscout |
| `supply` | Total supply + decimals + vendor metrics | RPC + Blockscout |
| `holders` | Top holder snapshot + concentration + **Gini** | Blockscout |
| `flows` | Categorized netflows (7d window) | Dune |
| `labels` | Address labels (public + Dune) | Blockscout + Dune |
| `locks` | Vesting contract auto-discovery | RPC + Blockscout |
| `staking` | Staking contract auto-discovery | RPC + Blockscout |
| `unlocks` | **Curated + on-chain projected** unlock schedule | Tokenomist + locks |
| `yields` | **Protocol fees, revenue, fee-switch optionality, APY pools** | DeFiLlama + yields.llama.fi |
| `compare` | **Cross-metric ratios** (custody / float / fees / yield) | Derived from the above |
| `risk` | **CPD 2.0 risk model + revenue-vs-risk verdict** | Derived from yields + compare |
| `prelaunch` | **Pre-TGE programs via DeFiLlama directly** (no token) | DeFiLlama `/protocols` |
| `chain-report` | **Non-EVM L1 chain analysis** (Hedera, Solana, Sui, etc.) | DeFiLlama `/chains` |
| **`trend`** | **Per-token snapshot + delta tracking — accumulation / distribution detection** | Derived from compare + holders + flows |
| **`signal`** | **Composite buy/sell score + verdict** | Derived from compare + trend + flows + yields |
| `relations` | Entity graph (holders + labels + locks + staking + flows) | All of the above |
| `score` | **6-pillar composite score** | All of the above |
| **`screen`** | **Broad market growth-token screening funnel** | DeFiLlama + DexScreener + Blockscout + RPC |
| **`fairlaunch`** | **15-point fair launch scoring** ([fairlaunch.org](https://fairlaunch.org/) rubric) | supply + holders + locks + staking + compare |
| **`momentum-screen`** | **Beta-neutral long/short basket builder (paper mode)** | DeFiLlama + DexScreener |
| **`momentum-rank`** | **Single-token z-score against a saved basket** | Reuses last momentum-screen output |
| **`momentum-backtest`** | **Replay a saved basket against price history (synthetic or real)** | Local price cache |
| **`price-history`** | **Fetch CEX OHLCV (Binance/OKX/Bybit/Coinbase/Kraken) into the price cache** | CCXT (optional extra) |
| **`portfolio-build`** | **Bullish-hedged 100%-gross long/short book (Bybit-tradable, risk-parity longs + BTC-perp hedge)** | Local price cache + reference cache |
| **`portfolio-rebalance`** | **Drift detection + trade list for a saved portfolio** | Local price cache |
| `deep-report` | Full dossier (JSON or Markdown) | All of the above |

---

## resolve

Query → canonical token identity, with DeFiLlama cross-check to catch squatter tokens.

```bash
token-research resolve ONDO
token-research resolve Morpho --chain ethereum
token-research resolve 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3
```

**Providers:** DexScreener (search pairs), DeFiLlama (protocol directory cross-check).

**Resolution logic (priority order):**

1. **KNOWN_ALIASES** — hardcoded canonical mappings for a few well-known symbols.
2. **DeFiLlama protocol `address`** — if the protocol directory has an entry matching the query with a canonical `address` field and TVL ≥ $100K, that address wins. Fixes the `MintBurnTeamToken` / memecoin-squatter class of bugs.
3. **DexScreener search** — filtered to EVM supported chains (`ethereum`, `base`, `arbitrum`, `optimism`, `monad`) and pools with ≥ $10K liquidity. Same-chain hits are a hard sort preference.

**Output metrics:**
- `resolved_identity` — `{query, normalized_query, query_type, symbol, project_name, chain, token_address}`
- `candidates` — ranked list of DexScreener matches (also emitted for disambiguation)

**Known limit:** pre-TGE projects (no token yet) will fall through to a memecoin because DeFiLlama has no `address` field. Use `prelaunch` instead for those cases.

---

## identity

Explorer-sourced contract metadata.

```bash
token-research identity ONDO --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3
```

**Providers:** Blockscout (token info + address info).

**Output metrics:** `symbol_verified`, `name_verified`, `decimals`, `chain_supported`, `explorer`, `token_info`.

---

## price

Spot price with cross-validation.

```bash
token-research price ONDO --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3
```

**Providers:** DexScreener (primary), DeFiLlama coins (secondary for cross-check).

**Output metrics:**
- `spot_price_usd` — best available price
- `market_cap` — DexScreener mcap
- `fdv` — fully diluted valuation
- `best_pair` — full pair summary from the top pool
- `defillama_price`, `defillama_confidence`

---

## pools

DEX pool discovery.

```bash
token-research pools PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Output:** `pools` — top 10 by liquidity with chain, dex, pair address, price, liquidity, FDV, market cap.

---

## liquidity

DEX liquidity depth, venue distribution, and **LP lock analysis**.

```bash
token-research liquidity PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** DexScreener + Blockscout (for LP-holder inspection of v2-style pools).

**Output metrics:**
- `total_dex_liquidity_usd`
- `top_pools` — top 10 summaries
- `liquidity_by_dex` — breakdown per DEX
- `lp_locked_percent` / `lp_unlocked_percent` — computed by analyzing LP-token holders on the top pool, matching against burn addresses + a conservative registered-locker registry (Team Finance, Unicrypt V2, PinkSale). Works for Uniswap-v2-style fungible LP pairs; Uniswap v3/v4 pool IDs correctly fall through to `None`.
- `lp_lock_analysis` — detailed LP holder breakdown when available.

---

## supply

Total supply from canonical sources.

```bash
token-research supply PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** RPC (primary: `totalSupply`, `decimals`), Blockscout (fallback).

**Output metrics:**
- `total_supply_raw`, `decimals`, `total_supply_adjusted`
- `total_supply_source` — `rpc` or `blockscout`
- `circulating_supply_vendor`, `holders_count`, `exchange_rate`, `token_type`

---

## holders

Top holder snapshot + concentration (**including Gini coefficient**).

```bash
token-research holders PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** Blockscout (top 30 holders + token info for total supply).

**Output metrics:**
- `top_holders` — list of holder entries with address and value
- `concentration.top10_share`, `top20_share`
- `concentration.top_holders_gini` — Gini from the top-N snapshot (computed with the unbiased formula; clearly documented as an upper-bound approximation of the full-distribution Gini)
- `concentration.gini` — `None` (full-distribution version requires historical reconstruction)
- `concentration.nakamoto_51` — minimum number of holders needed to exceed 51% of supply

---

## flows

Categorized token flows (7-day window).

```bash
token-research flows PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** Dune Analytics (SQL query over `erc20_ethereum.evt_Transfer` joined with `labels.addresses`).

**Requires:** `TOKEN_RESEARCH_DUNE_API_KEY`.

**Output metrics:**
- `netflows` — list of `{category, inflow_raw, outflow_raw, net_raw, tx_count}` entries. Categories: `cex`, `dex`, `bridge`, `treasury`, `unknown`.
- `destination_buckets` — the category labels used.

**Known issue:** Dune's query-result deduplication can return stale executions for identical SQL. If flows returns `did not complete`, cancel on the Dune side or wait for the cache to expire.

---

## labels

Address labels merged from multiple sources.

```bash
token-research labels PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** Blockscout (public_tags, private_tags, watchlist_names, contract names) + Dune (rich labeled addresses).

**Requires Dune for full coverage:** without a Dune key, output falls back to Blockscout-only labels.

**Output metrics:**
- `labels` — merged, deduped list of `{address, source, label, type}` entries
- `merge_strategy` — ordered provenance list

---

## locks

**Auto-discovers** vesting contracts by scanning top holders.

```bash
token-research locks PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** RPC (OZ VestingWallet + TokenTimelock fingerprinting via `start()`, `duration()`, `released()`, `releaseTime()`, `token()`, `beneficiary()`) + Blockscout (contract name fingerprint fallback using keywords like `vest`, `timelock`, `escrow`).

**Requires:** `TOKEN_RESEARCH_ETH_RPC_URL` / `_BASE_RPC_URL` / `_ARB_RPC_URL` / `_OP_RPC_URL`.

**Output metrics:**
- `vesting_contracts` — OZ VestingWallet / TokenTimelock matches (high confidence)
- `timelock_balances` — generic contract holders (incl. GnosisSafes, wrappers, proxies) — low confidence
- `locked_onchain_total` — aggregate fingerprinted lock balance
- `contract_holders_inspected` — how many contract addresses were RPC-inspected
- `non_contract_holders_skipped`

---

## staking

**Auto-discovers** staking contracts among top holders.

```bash
token-research staking PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** RPC (ERC-4626 `asset()`/`totalAssets()`, Synthetix-style `stakingToken()`, name-fingerprint fallback for `stak`, `reward`, `vault`, `ve*`, `xToken`, `MasterChef`).

**Output metrics:** `staking_contracts`, `staked_total`, `contract_holders_inspected`.

---

## unlocks

Curated schedule (premium) **plus** on-chain projected unlock events derived from detected vesting contracts, plus validation windows comparing expected unlocks to observed flows.

```bash
token-research unlocks PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827 --with-premium
```

**Providers:** Tokenomist (curated, premium) + locks (on-chain projection) + flows (validation).

**Output metrics:**
- `curated_schedule` — Tokenomist events (when `--with-premium` + key set)
- `allocations` — category breakdowns from Tokenomist
- `onchain_schedule` — linear-vesting projections derived from detected OZ VestingWallets (`start`, `duration`, `released`, `token_balance`)
- `upcoming_onchain_events` — events within the next 90 days (linear slices or cliff releases)
- `validation_windows` — cross-check records with `verdict` field (`flow_activity_aligned`, `flow_activity_lower_than_expected`, `no_flow_observed`)

---

## yields  *(new)*

**Protocol fees, revenue, fee-switch optionality, and APY pool data.**

```bash
token-research yields PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** DeFiLlama `/protocols`, `/summary/fees/{slug}`, `yields.llama.fi/pools`.

**Output metrics:**
- `protocol_slug`, `protocol_name`, `protocol_category`
- `tvl_usd`, `mcap_usd`, **`mcap_source`** — `defillama_protocol` / `dexscreener_best_pair` / `dexscreener_fdv`. Fallback chain handles protocols whose DeFiLlama entry has no mcap (e.g. Morpho V1, Ondo Yield Assets).
- `protocol_age_band` — `0-1` / `1-2` / `2-3` / `3-5` / `>5`, derived from DeFiLlama `listedAt`
- `protocol_fees` — `{total24h, total7d, total30d, total1y, change_1m}`
- `protocol_revenue` — same shape
- `annualized_fees_usd`, `annualized_revenue_usd` — 30d × 12
- **`fees_to_mcap_ratio`** — the fundamental-yield headline
- `revenue_to_mcap_ratio`
- **`revenue_model`** — `fees_pass_through` / `protocol_capture` / `unknown`. `fees_pass_through` means the protocol generates fees but routes them all to LPs / depositors (Morpho V1, Ondo Yield Assets style).
- `yield_pool_count` — all pools
- **`meaningful_pool_count`** — pools with TVL ≥ $10M AND APY > 0 (excludes collateral-only pools)
- `best_apy`, **`best_apy_meaningful`**, **`apy_p95`**
- `weighted_avg_apy`, **`weighted_avg_apy_meaningful`**
- `total_yield_tvl_usd`, `meaningful_yield_tvl_usd`
- `yield_pools` — compact summaries of the top 10

The `meaningful_*` variants are what downstream commands (`risk`, `compare`) use for verdicts. The raw `best_apy` / `weighted_avg_apy` are preserved for backwards compatibility but are often polluted by zero-APY collateral pools and micro-TVL outliers.

---

## compare  *(new)*

**Cross-metric ratios** derived from supply / holders / liquidity / locks / staking / yields / price. No new network calls beyond what those commands fetch.

```bash
token-research compare PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Output metrics** (five blocks):

### `supply` block
- `total_supply_tokens`, `decimals`, `price_usd`, `total_supply_usd`
- `locked_onchain_tokens`, `staked_onchain_tokens`, `custody_tokens` — fingerprinted
- `contract_held_tokens_total`, `protocol_owned_tokens`, `protocol_owned_pct_of_supply` — includes non-fingerprinted contract holders (GnosisSafes, wrappers, proxies)
- `float_tokens_est`, `float_pct_of_supply` — legacy (excludes only fingerprinted custody)
- `effective_circulating_tokens`, `effective_circulating_pct_of_supply` — excludes ALL protocol-owned tokens
- `top10_pct_of_supply` — raw
- `effective_top10_pct_of_supply` — EOA-only top-10 share after excluding protocol-owned holders. Used by `ownership_quality`.
- **`effective_overhang_pct_of_supply`**, **`effective_overhang_ratio`** — structural supply pressure combining contract-held tokens AND concentrated EOA top-10:
  - `effective_overhang_pct = protocol_owned_pct + max(0, effective_top10_pct - 20)`
  - `effective_overhang_ratio = effective_overhang_pct / (100 - effective_overhang_pct)`
  - Reference readings: Morpho 2.17× (severe), ONDO 1.50× (elevated), Pendle 0.55× (healthy), KAITO **3.04× (severe)**
  - Used by both `signal.overhang` component and `score.supply_pressure` pillar as the preferred overhang signal, with fallback to the legacy protocol_owned formula when unavailable
- `non_protocol_holders_considered`

### `market` block
- `mcap_usd`, `tvl_usd`, `dex_liquidity_usd`, `lp_locked_percent`, `yield_pool_tvl_usd`
- `mcap_to_tvl`, `liquidity_to_mcap`, `liquidity_to_float_usd`, `liquidity_to_supply_usd`, `yield_pool_to_protocol_tvl`

### `fees` block
- `annualized_fees_usd`, `annualized_revenue_usd`
- `revenue_share_of_fees`
- `fees_to_mcap`, `fees_to_tvl`, `revenue_to_mcap`, `revenue_to_tvl`
- `fees_to_custody_usd`, `fees_to_staked_usd`
- `implied_staker_apy_if_fees_distributed`

### `custody` block
- `locked_tokens_vs_staked`, `custody_vs_float`, `custody_vs_dex_liquidity_usd`
- `lp_locked_vs_token_custody_pct`

### `yield` block
- `best_apy_percent`, `weighted_avg_apy_percent`, `pool_count`
- `implied_staker_apy_percent`, `market_apy_vs_implied_apy`

Plus a `notes` list explaining any estimation limits (missing price, missing supply, etc.).

---

## risk  *(new)*

**CPD 2.0 risk model + revenue-vs-risk verdict.** See [`docs/risk_revenue_estimation.md`](risk_revenue_estimation.md) for the full model protocol.

```bash
# Fully auto — infers everything from pipeline data
token-research risk PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827

# With overrides for dimensions the auto-derivation can't see
token-research risk PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827 \
  --trade-size 0.05 --nesting 2 \
  --cpd-age 3-5 --cpd-stage audit --cpd-hacks 0-1 \
  --primitive-a 1 --primitive-b 3
```

**Risk-specific flags:**

| Flag | Default | Description |
|---|---|---|
| `--trade-size <0.01..0.10>` | `0.10` | Trade size as fraction of portfolio |
| `--nesting <1..10>` | `1` | Nesting depth (composed primitives) |
| `--primitive-a <1..10>` | auto | DeFi primitive level for axis A (1 = native staking … 10 = custom) |
| `--primitive-b <1..10>` | auto | Same for axis B |
| `--cpd-chain <I\|II\|III>` | inferred | Chain stack tier |
| `--cpd-protocol <I\|II\|III>` | inferred from TVL | Protocol stack tier |
| `--cpd-dapp <I\|II\|III>` | inferred from pool/chain count | Dapp stack tier |
| `--cpd-stage <mvp\|alpha\|beta\|release\|audit>` | `release` | Lifecycle stage |
| `--cpd-age <0-1\|1-2\|2-3\|3-5\|>5>` | inferred from listedAt | Age band |
| `--cpd-code <proprietary_new\|proprietary_old\|open_source_new\|open_source_old\|oso_audit>` | inferred from Blockscout | Code band |
| `--cpd-hacks <">2"\|2\|0-1\|0\|0_dev>` | `0` (optimistic) | Hacks band |

**Output metrics:**
- `cpd` — `{inputs, inputs_source, sub_scores, total_score, max_score, percent_of_max, tier, risk_input, explanation}`
- `risk` — `{base_risk, defi_multiplier, final_risk, explanation, primitive_a_name, primitive_b_name}`
- `revenue_vs_risk` — `{weighted_avg_apy_percent, best_apy_percent, risk_score, linear_adjusted, conservative_adjusted, apy_per_unit_risk, verdict_weighted, verdict_best}`
- `source_summary` — provenance for the inputs (mcap_source, revenue_model, protocol_age_band, etc.)

**Verdicts:** `strong_reward_for_risk` / `acceptable` / `marginal` / `poor_reward_for_risk` / `no_yield_data` / `negative_yield`.

---

## prelaunch  *(new)*

**Pre-TGE programs via DeFiLlama directly.** For farming programs like Katana Pre-Launch where the real project has no token yet and DexScreener would return a memecoin squatter.

```bash
token-research prelaunch Katana
token-research prelaunch Katana --cpd-stage beta --cpd-code proprietary_old
token-research prelaunch "MegaETH" --min-tvl 50000
```

**Prelaunch-specific flag:**

| Flag | Default | Description |
|---|---|---|
| `--min-tvl <USD>` | `100000` | Minimum DeFiLlama TVL for a protocol to be considered a match |

All the same `--cpd-*` and `--trade-size` / `--nesting` / `--primitive-*` flags as `risk`, with different defaults (`--trade-size 0.05`, `--nesting 2`).

**Resolution path:** DeFiLlama `/protocols` directory, filtered by name substring and TVL, ranked by TVL descending. Picks the top match; returns the next 4 as `alternates` for disambiguation.

**Output metrics:**
- `protocol` — `{name, slug, category, symbol, chains, tvl_usd, mcap_usd, change_1d_pct, change_7d_pct, listedAt, listedAt_iso, audits, url, twitter, description, module, parent_protocol}`
- `alternates` — up to 4 same-name protocols ranked by TVL
- `cpd` — full CPD block (auto-infers `stage: mvp` when "Pre-Launch" is in the name)
- `risk` — full risk block
- `revenue_vs_risk` — always `no_yield_data` (pre-TGE programs don't have measurable APY)

---

## chain-report  *(new)*

**Non-EVM L1 chain analysis** via DeFiLlama chain endpoints. Partial coverage for chains the EVM-first pipeline can't otherwise analyze (Hedera, Solana, Sui, Aptos, TON, NEAR, Cosmos, etc.). See [`archived/docs/hbar_analysis.md`](../archived/docs/hbar_analysis.md) for a worked example.

```bash
token-research chain-report Hedera
token-research chain-report Solana --min-pool-tvl 1000000
token-research chain-report Sui --trade-size 0.05
```

**chain-report-specific flag:**

| Flag | Default | Description |
|---|---|---|
| `--min-pool-tvl <USD>` | `100000` | Minimum yield pool TVL to include. |

Plus all the `--cpd-*` / `--trade-size` / `--nesting` / `--primitive-*` flags from `risk`.

**Providers:** DeFiLlama `/chains`, `/v2/historicalChainTvl/{chain}`, `/overview/fees/{chain}`, `yields.llama.fi`, `coins.llama.fi`.

**Output metrics:**
- `chain` — `{name, gecko_id, token_symbol, chain_id, cmc_id, tvl_usd}`
- `native_token` — `{symbol, price_usd, price_source, price_timestamp, confidence}`
- `trajectory` — TVL history summary: `{earliest_date, earliest_tvl, peak_date, peak_tvl, current_date, current_tvl, drawdown_from_peak_pct, change_30d_pct, change_1y_pct, data_points}`
- `chain_fees` — `{total24h, total7d, total14dto7d, total48hto24h}` when indexed
- `protocols_on_chain` — `{count, top}` with top-15 summaries
- `yield_pools` — `{count, top, total_tvl_usd, weighted_avg_apy, best_apy}`
- `cpd` + `risk` — full CPD 2.0 blocks with L1-specific seed tiers

**Known limits:**
- No on-chain holder / custody analysis (needs chain-native indexer)
- No native-chain staking APY (not indexed by yields.llama.fi)
- No treasury / council allocation tracking

---

## trend  *(new — trading signals)*

**Snapshot + delta tracking** for token metrics. Stores compact JSON snapshots under `$TOKEN_RESEARCH_DATA_DIR/trends/<chain>-<symbol>/<YYYY-MM-DD>.json` and diffs the current reading against the closest historical snapshot within `--window` days.

This is the primitive that turns static pipeline metrics into *direction-of-travel* trading signals. See [`docs/trend_signals.md`](trend_signals.md) for the full trading framework.

```bash
# First run — stores baseline, emits no deltas yet
token-research trend MORPHO --chain ethereum --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2

# Second run (some days later) — stores new snapshot + diffs against baseline
token-research trend MORPHO --chain ethereum --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2

# With custom window
token-research trend ONDO --chain ethereum --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3 --window 14

# Dry run — compute deltas without persisting
token-research trend PENDLE --chain ethereum --address 0x808507121B80c02388fAd14726482e061B8da827 --no-save
```

**trend-specific flags:**

| Flag | Default | Description |
|---|---|---|
| `--window <days>` | `30` | Window in days to diff the current snapshot against. |
| `--no-save` | off | Compute deltas without persisting the current snapshot. |

**Providers:** Internally calls `compare` (for supply/market/fees/yield), `holders` (for top-30), and `flows` (for netflows).

**What a snapshot contains** (compact — just the fields that change over time):
- `timestamp`, `date_iso`, `symbol`, `chain`, `token_address`
- `supply` — `{total_supply_tokens, price_usd, total_supply_usd, protocol_owned_tokens, protocol_owned_pct_of_supply, effective_circulating_pct_of_supply, top10_pct_of_supply, effective_top10_pct_of_supply}`
- `market` — `{mcap_usd, tvl_usd, dex_liquidity_usd, mcap_to_tvl, liquidity_to_mcap, liquidity_to_float_usd}`
- `fees` — `{annualized_fees_usd, annualized_revenue_usd, fees_to_mcap, fees_to_tvl, revenue_share_of_fees}`
- `yield` — `{pool_count, weighted_avg_apy_percent, best_apy_percent}`
- `top_holders` — list of top-30 addresses (for set-diff new-whale detection)
- `top_holder_balances` — address → raw balance (for size-delta tracking)
- `netflows_7d` — `{cex, dex, bridge, treasury}` net raw amounts from the flows command

**Output metrics:**
- `snapshot` — the current snapshot just captured
- `baseline` — the historical snapshot picked for comparison (or `null` if none)
- `deltas` — `{window_days, has_baseline, baseline_date_iso, supply, market, fees, yield}`, where each sub-block is a dict of `{field: {previous, current, delta, delta_pct}}`
- `holder_changes` — `{new_in_top30, departed_from_top30, grew_significantly, shrunk_significantly}`
- `signals` — list of human-readable high-level signal strings:
  - `ACCUMULATION: effective top-10 share rose X%`
  - `DISTRIBUTION: effective top-10 share fell X%`
  - `TREASURY_DISTRIBUTION: protocol-owned share fell X%`
  - `TREASURY_ACCUMULATION: protocol-owned share rose X%`
  - `FEE_MOMENTUM_UP: fees/mcap rose X%`
  - `FEE_MOMENTUM_DOWN: fees/mcap fell X%`
  - `LIQUIDITY_DRAIN: liq/float ratio fell X%`
  - `LIQUIDITY_BUILD: liq/float ratio rose X%`
  - `NEW_WHALES: N new address(es) entered top-30`
  - `WHALES_EXITED: N address(es) left top-30`
- `history` — `{snapshots_available, earliest_date, latest_date}`
- `saved_to` — path where the snapshot was persisted (or `null` if `--no-save`)

**Operational workflow:**

1. Prime the watchlist once: `for token in MORPHO ONDO PENDLE; do token-research trend $token --chain ethereum --address 0x...; done`
2. Run weekly on a cron
3. Pull deltas whenever making a decision — even the first run after baseline establishes useful signals

**When no baseline exists** (first run on a token), `has_baseline` is `false`, `deltas` is mostly empty, and `signals` is empty. A coverage gap is emitted pointing the user at "re-run after the next observation interval."

---

## signal  *(new — trading signals)*

**Composite buy/sell score** combining 7 components into a single number in the range [−10, +10] with a verdict string. See [`docs/trend_signals.md`](trend_signals.md) for the full scoring rubric and component weights.

```bash
token-research signal MORPHO --chain ethereum --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2
token-research signal ONDO --chain ethereum --address 0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3 --window 14
```

**signal-specific flag:**

| Flag | Default | Description |
|---|---|---|
| `--window <days>` | `30` | Window in days for trend-based components (accumulation_trend, fee_momentum, whale_events). |

**Providers:** Internally calls `compare`, `trend` (with `--no-save`), `flows`, `yields`.

**Score components** (each bounded [−2, +2]):

| Component | Weight | Input | Positive → | Negative → |
|---|---|---|---|---|
| `overhang` | 1.5 | **`compare.supply.effective_overhang_ratio`** (prefers combined contract+EOA metric; falls back to `protocol_owned / effective_circulating`) | distributed (≤0.3×) | severe (≥2.5×) |
| `fee_yield` | 1.5 | `compare.fees.fees_to_mcap × revenue_model` | strong capture | no fees |
| `exit_liquidity` | 1.0 | `compare.market.liquidity_to_float_usd` | deep (≥5%) | very thin (<0.5%) |
| `cex_flows` | 1.5 | `flows.netflows[cex]` (7d) | net outflow | net inflow |
| `accumulation_trend` | 2.0 | `trend.deltas.supply.effective_top10_pct_of_supply.delta_pct` | rising (≥3%) | falling (≤−3%) |
| `fee_momentum` | 1.0 | `trend.deltas.fees.fees_to_mcap.delta_pct` | rising (≥5%) | falling (≤−5%) |
| `whale_events` | 1.5 | `trend.holder_changes` | new top-30 entries | departures |

**Verdict thresholds:**

| Score | Verdict |
|---|---|
| ≥ +4 | `strong_accumulate` |
| +1.5 to +4 | `accumulate` |
| −1.5 to +1.5 | `neutral` |
| −4 to −1.5 | `reduce` |
| ≤ −4 | `strong_reduce` |

When `coverage_ratio < 0.5` (most signals are `no_data`), the verdict is downgraded with a `_low_confidence` suffix. This happens automatically on the first run against a token because `trend` has no baseline yet.

**Output metrics:**
- `signal_score` — clamped to [−10, +10]
- `verdict` — string from the table above
- `coverage_ratio` — 0.0 to 1.0 (fraction of components that had usable data)
- `has_trend_baseline` — `true` if `trend` found a historical snapshot to diff against
- `components` — per-component `{score, weight, note}` breakdown so you can see what's driving the composite
- `inputs_snapshot` — the raw input values used, so the score is reproducible

**Typical pattern** (first run sequence):

```bash
# Step 1: prime the trend store
token-research trend MORPHO --chain ethereum --address 0x58D97B... 

# Step 2 (a week later): get the signal
token-research signal MORPHO --chain ethereum --address 0x58D97B... --window 7
```

**Cross-check with risk before acting:**

```bash
# Size ceiling
token-research risk MORPHO --chain ethereum --address 0x58D97B... --trade-size 0.05

# Direction
token-research signal MORPHO --chain ethereum --address 0x58D97B... --window 30
```

- `risk.final_risk 5` + `signal.signal_score +3` → enter 5% position
- `risk.final_risk 9` + `signal.signal_score +5` → still pass (risk too high)
- `risk.final_risk 3` + `signal.signal_score −2` → pass (good structure, wrong timing)

---

## relations

Entity graph built from holders + labels + locks + staking + flows.

```bash
token-research relations PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Output metrics:**
- `entities` — classified holders with `{address, type, label, balance, top_holder_share, confidence, evidence}`. Types include `wallet`, `burn`, `vesting`, `staking`, `locker`, `contract`, plus any Dune-label-driven category (`dex`, `cex users`, `nft`, etc.).
- `clusters` — grouped by entity type with `{name, members, total_balance, share_of_top_holders}`
- `relations` — edges: per-entity `holds_as_*` edges plus `co_*` cluster edges plus **flow-derived `net_inflow_from_*` / `net_outflow_to_*` edges** sourced from the flows command

---

## score

**6-pillar composite structural-quality score.** No longer uses fixed baselines for pillars with structural data available. Weights live in `commands/score.py:PILLAR_WEIGHTS`.

```bash
token-research score PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827
```

**Providers:** Internally calls `supply`, `holders`, `liquidity`, `locks`, `staking`, `yields`, `compare`.

**Output metrics:**
- `pillars` — `{supply_pressure (0.22), ownership_quality (0.22), liquidity_quality (0.22), fundamental_support (0.08), governance_commitment (0.13), token_capture (0.13)}`
- `raw_inputs` — full provenance for each pillar
- `composite_score` — weighted average with coverage penalty
- `coverage_ratio` — fraction of pillars that could be scored
- `pillar_weights`

### Pillar logic (current)

**`supply_pressure`** — reads `compare.supply.protocol_owned_pct_of_supply` + `yields.revenue_model`. Protocols with severe structural overhang (≥65% protocol-owned) and no fee-switch score in the 30s. Distributed, fee-capturing protocols score 80+. Falls back to baseline 65 when compare data is absent.

**`ownership_quality`** — reads `compare.supply.effective_top10_pct_of_supply` when available (the EOA-adjusted top-10 that excludes protocol-owned holders). Skips `nakamoto_51 ≤ 2` and `gini > 0.7` penalties when raw concentration is driven by protocol-owned holders (avoids double-counting what is actually custody). Falls back to raw top-10 otherwise.

**`liquidity_quality`** — total DEX liquidity tier + venue concentration penalty + LP lock bonus when `liquidity.lp_locked_percent` is available.

**`fundamental_support`** — TVL tier (0–35) + mcap/TVL efficiency (0–25) + chain diversity (0–10) + 1m TVL trend (±10) + **fees/mcap tier (0–20, new)**. Uses `yields.mcap_source` fallback to handle DeFiLlama protocols with null mcap.

**`governance_commitment`** — credits fingerprinted OZ VestingWallets / TokenTimelocks / ERC-4626 vaults (high confidence), Synthetix-style staking (high confidence), **and non-fingerprinted protocol-owned supply from compare** (lower confidence). A protocol with 60% of supply in foundation GnosisSafes now scores ~65, not ~25.

**`token_capture`** — the VC-lens pillar. Isolates the *holder-accrual* component: does protocol economics (fee-share, buyback) actually reach the token, as opposed to just sitting in TVL/fees. Distinct from `fundamental_support`, which credits raw TVL + fees/mcap. Reads `yields` revenue-model signals.

---

## fairlaunch  *(new — fair launch scoring)*

Score a token against the [Fair Launch Handbook](https://fairlaunch.org/) 15-point rubric using on-chain data.

```bash
token-research fairlaunch MORPHO --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2
```

**Scoring dimensions (3 points each, 15 total):**

| Dimension | Auto-scored criteria | Manual-check criteria |
|---|---|---|
| **Pricing** | Market-determined (DEX exists) | Presale existence, uniform pricing |
| **Allocation** | Public >30%, team <20%, on-chain visible | Hidden allocation buckets |
| **Vesting** | VestingWallet/Timelock detected, cliff >=12m, staking exists | Custom vesting inspection |
| **Transparency** | Contract verified (RPC), ownership safe, not upgradeable | Wallet disclosure, 7-day notice |
| **Access** | Nakamoto >=5, Gini <0.6, holders >=10K | Wallet caps, whitelist, MEV prevention |

**Score bands:**

| Range | Band | Meaning |
|---|---|---|
| 0–3 | **avoid** | Major fairness red flags |
| 4–7 | **caution** | Some concerns, manual verification needed |
| 8–12 | **fair** | Meets most fairness criteria |
| 13–15 | **exemplary** | Best-in-class fair distribution |

**Output metrics:**
- `fairness_score` / `max_score` / `band`
- `auto_scored_criteria` / `manual_check_criteria`
- `dimensions.{pricing,allocation,vesting,transparency,access}` — per-dimension score + detail array
- Each detail: `{criterion, points, max, note, auto, value?}`

**Providers:** RPC (supply, owner, proxy), Blockscout (holders, token info), DexScreener (compare), DeFiLlama (compare).

**Included in `deep-report`** — fairlaunch score appears in the `modules.fairlaunch` block.

---

## screen  *(new — market screening)*

Multi-stage growth-token screening funnel. Starts from the full DeFiLlama protocol universe (~3800 entries) and narrows to a ranked shortlist using progressively expensive data sources.

```bash
# Default screen: TVL $500K–$500M, top 10 results
token-research screen growth

# Lending protocols only, wider funnel
token-research screen growth --category lending --top 20 --stage3-top 50

# Micro-cap focus
token-research screen growth --min-tvl 100000 --max-tvl 10000000

# Narrow funnel (faster)
token-research screen growth --stage3-top 15 --stage4-top 8 --top 5
```

**Flags:**

| Flag | Default | Description |
|---|---|---|
| `--min-tvl` | $500K | Minimum protocol TVL (USD) |
| `--max-tvl` | $500M | Maximum protocol TVL (USD) — filters out mega-caps |
| `--category` | none | DeFiLlama category substring filter (e.g. `lending`, `dex`, `yield`) |
| `--stage3-top` | 30 | How many growth-scored protocols to check on DexScreener |
| `--stage4-top` | 15 | How many market-filtered protocols to check holders/security |
| `--top` | 10 | Final result count |

**Funnel stages:**

| Stage | Source | Cost | What it does |
|---|---|---|---|
| 1. Universe | DeFiLlama `/protocols` | 1 call | Filter by chain + TVL band + category |
| 2. Growth scoring | DeFiLlama `yields.llama.fi/pools` | 1 call | TVL momentum, mcap/TVL, age×growth, yield, chain diversity, pool count, audit signal |
| 3. Market structure | DexScreener per-token | ~30 calls | DEX liquidity, pair count, volume, liq/mcap |
| 4. Ownership | Blockscout + RPC per-token | ~30 calls | Top-10 holder share, owner(), proxy admin |
| 5. Final ranking | Derived | 0 calls | Growth score + liquidity bonus + concentration penalty |

**Growth signal weights:**

| Signal | Weight |
|---|---|
| TVL momentum (7d + 1m) | 0.25 |
| mcap/TVL value ratio | 0.20 |
| Age × growth (young + growing) | 0.15 |
| Yield attractiveness (best APY) | 0.15 |
| Chain diversity | 0.10 |
| Pool ecosystem depth | 0.10 |
| Audit signal | 0.05 |

**Output metrics:**
- `metrics.funnel` — survivor counts at each stage
- `metrics.filters` — applied filter values
- `metrics.results` — ranked list of `{rank, name, symbol, category, chain, address, tvl_usd, mcap_usd, change_7d_pct, change_1m_pct, best_apy_pct, dex_liquidity_usd, top10_share_pct, is_upgradeable, growth_score, final_score, growth_signals}`

**API budget:** ~62 calls total for default settings vs. ~64,600 if running deep-report on every protocol (99.9% reduction).

**Providers:** DeFiLlama (bulk), DexScreener (per-token), Blockscout (per-token), RPC (per-token).

---

## deep-report

Run all commands and produce a dossier.

```bash
# JSON (default)
token-research deep-report PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827

# Markdown — with Revenue-vs-risk, Cross-metric comparison, Protocol yield sections
token-research deep-report PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827 --format markdown

# Skip persistence
token-research deep-report PENDLE --address 0x808507121B80c02388fAd14726482e061B8da827 --no-persist
```

**Output metrics:**
- `metrics.modules` — dict containing the full output of every command (18 modules)
- `scores` — composite score + pillar breakdown + coverage ratio
- `narrative` — dynamic analytical summary generated from module outputs (composite band, liquidity one-liner, top-10 concentration, on-chain custody count, unlock posture, flow posture, protocol yield, cross-metric ratios, CPD/risk/verdict, open coverage gaps)
- `evidence_summary` — per-module provenance bullets with `[ok]`/`[empty]` status
- `coverage_gaps` — deduplicated across modules
- `saved_to` — file path (unless `--no-persist`)

**Markdown sections:**

1. Identity table
2. Verdict (composite + pillar scores)
3. Narrative bullets
4. Key metrics — Price · Supply · Liquidity · Holders · Locks · Staking · Unlocks · Flows · Protocol yield · Revenue vs risk (CPD 2.0) · Cross-metric comparison · Relations graph
5. Warnings
6. Coverage gaps table
7. Evidence summary
8. Sources table

**Persistence:** Reports are saved to `$TOKEN_RESEARCH_DATA_DIR/reports/<chain>-<symbol>-deep-report.json`.

---

## Output structure (shared across all commands)

```json
{
  "command": "<command-name>",
  "input": {
    "query": "<original-query>",
    "chain": "<chain-or-null>",
    "address": "<address-or-null>",
    "include_premium": false
  },
  "resolved_identity": {
    "query": "PENDLE",
    "normalized_query": "PENDLE",
    "query_type": "ticker",
    "symbol": "PENDLE",
    "project_name": "Pendle",
    "chain": "ethereum",
    "token_address": "0x808507121b80c02388fad14726482e061b8da827"
  },
  "metrics": { /* command-specific */ },
  "sources": [ /* provider registry with enabled/disabled flags */ ],
  "warnings": [ /* non-fatal issues from this run */ ],
  "coverage_gaps": [ /* metrics that couldn't be populated */ ],
  "generated_at": "2026-04-11T00:00:00+00:00"
}
```

---

## momentum-screen  *(new — portfolio research)*

Builds a **beta-neutral long/short basket** from the EVM token universe using a multi-factor momentum composite. Paper-mode only — no exchange execution. The output basket JSON is consumed by `momentum-backtest`.

```bash
# Default config — 200-token universe, 5 longs / 5 shorts.
token-research momentum-screen v1

# Tighter universe and looser thresholds.
token-research momentum-screen v2 --top 100 --long-z 1.0 --short-z -1.0
```

**Pipeline:**

1. **Universe** — DeFiLlama `/protocols` filtered to supported EVM chains (TVL band $1M–$10B by default). Capped at `--top` by mcap.
2. **Enrichment** — DexScreener pair stats (liquidity, volume, h24 price change, mcap) + DeFiLlama yield join.
3. **Features** — 11 cross-sectional metrics across 5 pillars: trend, flows, fundamental, concentration, liquidity. See `docs/tasks/03-momentum-portfolio.md`.
4. **Composite** — z-score per pillar → weighted blend → coverage penalty `0.15 × (1 − coverage_ratio)`.
5. **Selection** — top-K longs (`z ≥ long_z`), bottom-K shorts (`z ≤ short_z`), with hard exclusions for short-leg fundamentals risks (high APY + many pools = squeeze risk).
6. **Sizing** — equal-inverse-vol inside each leg, then scale shorts so `Σ(w·β)_long = Σ(w·β)_short` → net beta ≈ 0. (β defaults to 1.0 in v1; populate the price cache via `price-history` to enable real β computation later.)
7. **Persistence** — basket JSON written to `$TOKEN_RESEARCH_DATA_DIR/baskets/<tag>-<ts>.json`.

**Key flags:**
- `--top N` — universe cap (default 200)
- `--k N` — number of longs / shorts each (default 5)
- `--long-z`, `--short-z` — z-score thresholds (default ±1.5)
- `--min-dex-liq USD` — per-token DEX liquidity floor (default $1M)
- `--short-funding-bps` — assumed perp funding APR in bps (default 1500 = 15%)
- `--no-persist` — don't write the basket JSON

**Output metrics:**
- `basket.longs` / `basket.shorts` — list of `PaperPosition` (symbol, chain, address, weight, notional_pct, beta, z_score, expected_funding_bps, features)
- `basket.gross_pct` / `basket.net_pct` / `basket.net_beta`
- `basket.expected_carry_bps` — annualized funding bleed (negative)
- `basket.coverage_ratio` — mean feature coverage of selected names
- `basket.feature_matrix` — full feature snapshot for replay determinism

**Beta-vs-BTC** is read from the price-history cache:
- BTC reference: `prices/_reference/btc.json` (populate with `price-history --reference btc`)
- Per-token: `prices/<chain>-<address>.json` (populate with `price-history --from-basket latest`)

The screen runs OLS regression of token vs BTC daily log-returns over the trailing `--beta-window` days (default 60). Tokens with fewer than `--beta-min-obs` overlapping returns (default 20) fall back to `--beta-default` (default 1.0). The output exposes `metrics.beta_diagnostics` with `real_count`, `fallback_count`, and per-token reasons so you know how much of the basket is using the fallback.

**Beta flags:**
- `--beta-window N` — daily-return window for the regression
- `--beta-min-obs N` — minimum overlapping returns needed for a real β
- `--btc-reference NAME` — reference cache key (default `btc`)
- `--beta-default V` — fallback β when cache is missing or short

---

## momentum-rank  *(new — portfolio research)*

Project a **single token** onto the z-score basis of the most recent saved basket. Useful for asking "where would TOKEN sit if it had been in yesterday's basket?".

```bash
token-research momentum-rank PENDLE --chain ethereum
token-research momentum-rank ONDO --basket-file .token-research/baskets/v1-...json
```

**Providers:** Same enrichment as `momentum-screen` (DexScreener + DeFiLlama yields). Reads the saved basket's `feature_matrix` to derive cross-sectional means / stdevs without re-fetching the full universe.

**Output metrics:**
- `z_total` — composite z (with coverage penalty applied)
- `coverage_ratio`
- `side` — `long_candidate` / `short_candidate` / `neutral`
- `pillar_z` — per-pillar contribution
- `z_features` / `raw_features` — full per-feature breakdown

**Falls back to a coverage gap** when no basket exists yet — run `momentum-screen` first.

---

## momentum-backtest  *(new — portfolio research)*

Replay a saved basket against a daily price-history cache and emit equity curve + Sharpe + max DD + leg attribution. Pure research artefact — no live execution.

```bash
# Real-data mode: requires prices/<chain>-<address>.json files (populated by `price-history`).
token-research momentum-backtest v1 --start 2025-01-01 --end 2026-04-30 --rebalance weekly

# Synthetic mode: deterministic random walk per position. Always works offline.
token-research momentum-backtest latest --synthetic --synthetic-seed 7
```

**Cost model (configurable via flags):**
- `--spot-bps` — spot taker fee (default 10)
- `--perp-bps` — perp taker fee (default 5)
- `--funding-apr` — perp funding rate, decimal (default 0.15)
- `--rebalance` — `daily` / `weekly` / `monthly` / `none` (default `weekly`)

**Output metrics (under `result`):**
- `final_equity`, `total_return_pct`, `annualized_return_pct`
- `sharpe`, `max_drawdown_pct`, `hit_rate_pct`
- `long_attribution_pct` / `short_attribution_pct` — per-leg PnL contribution
- `total_funding_cost_pct` / `total_transaction_cost_pct`
- `periods` — full per-period breakdown for plotting / inspection
- `missing_price_series` — positions that had no data and were silently skipped (positive amount = real concern)

**Synthetic-mode behavior:** generates a deterministic random walk per position with longs given a small positive drift and shorts a small negative drift, so the synthetic backtest doesn't trivially print free PnL.

---

## price-history  *(new — CEX OHLCV cache)*

Fetch daily OHLCV bars from a centralized exchange via **CCXT** and write them into the `prices/` cache that `momentum-backtest` consumes. Two modes: single-token via `query`, or bulk via `--from-basket`.

**Requires the optional `ccxt` extra:**

```bash
pip install -e ".[ccxt]"
```

```bash
# Reference asset (BTC) — written to prices/_reference/btc.json. Powers β computation.
token-research price-history --reference btc --since 2024-01-01

# Single token.
token-research price-history ETH --chain ethereum \
    --address 0xC02aaa39b223FE8D0A0e5C4F27eAD9083C756Cc2 --since 2024-01-01

# Every position in the latest basket.
token-research price-history --from-basket latest --since 2024-01-01

# Force-refetch from a specific basket file, OKX-only.
token-research price-history --from-basket .token-research/baskets/v1-...json \
    --force --exchange okx
```

**Reference-asset mode** (`--reference btc|eth|sol`) skips the (chain, address) probe and pulls the canonical CEX pair directly. Mapping lives in `src/token_research/prices/reference.py`:
- `btc` → Binance `BTC/USDT`
- `eth` → Binance `ETH/USDT`
- `sol` → Binance `SOL/USDT`

This is the recommended way to seed BTC for β computation — no need for a wrapped-token proxy. Output goes to `prices/_reference/<name>.json` with metadata at `prices/_reference/_meta/<name>.json`.

**Resolution priority (per token):**

1. Hand-seeded alias registry (`prices/symbol_aliases.py`) — top tokens with known canonical pairs.
2. Probe each exchange's `markets` for the first quote currency in its priority list (USDT → USDC → USD → BUSD).
3. Perp fallback — same base, settle currency from the spec (`BASE/USDT:USDT`).

**Exchange priority order:** Binance → OKX → Bybit → Coinbase → Kraken. Override with `--exchange <id>` to pin a specific one. `--prefer-perp` flips the order so perp listings are probed first.

**Cache layout:**
- `$DATA_DIR/prices/<chain>-<address>.json` — `[{date_iso, price_usd}]`, sorted ascending. Schema matches `momentum-backtest` consumer.
- `$DATA_DIR/prices/_meta/<chain>-<address>.json` — sidecar (`exchange`, `symbol`, `kind`, `via`, `first_date`, `last_date`, `fetched_at`, `candle_count`).

**Negative cache:** if no listing exists on any exchange, the metadata file gets `kind: "no_listing"` with a 30-day TTL so re-runs short-circuit until newly listed tokens get re-probed.

**Incremental by default:** existing cache files are extended from `last_date + 1`. `--force` rewrites from `--since` instead.

**Key flags:**
- `--since YYYY-MM-DD` (default: 2 years before `--end`)
- `--end YYYY-MM-DD` (default: today UTC)
- `--timeframe {1d,1h,4h}` (default `1d`)
- `--from-basket <tag|path|latest>` — bulk mode
- `--max-parallel N` — capped at 10
- `--exchange <id>` — pin to one exchange
- `--prefer-perp` — perp before spot

**Output metrics:**
- `report.requested` / `report.cached` / `report.skipped_existing`
- `report.failed[]` — per-spec failure reasons (`no_listing_found`, `negative_cache_active`, `fetch_failed: ...`)
- `report.outcomes[]` — per-spec resolution + match metadata

**When CCXT is not installed:** the command exits cleanly with `verdict: "missing_ccxt"` and a warning pointing at the install hint. The rest of the pipeline keeps working.

**See:** `docs/tasks/05-ccxt-price-history.md` for the design doc.

---

## portfolio-build  *(new — bullish-hedged book)*

Builds a **self-funded (gross = 100%) bullish-hedged** long/short portfolio
across the curated Bybit-USDT-perp universe in `prices/portfolio_universe.py`.
Net beta is solved-for using a closed-form macro-hedge equation; long
weights inside the sleeve are equal-risk-contribution (risk parity).

```bash
# Default: net β = 0.4, 10–15 longs, weekly rebalance, 3-month horizon, BTC-only hedge.
token-research portfolio-build bullhedge-v1

# Tighter beta + 2 alpha shorts on the most overpriced names.
token-research portfolio-build v2 --target-beta 0.3 --alpha-shorts 2

# Larger universe ceiling, looser per-position cap.
token-research portfolio-build v3 --max-longs 18 --max-single-pct 0.20
```

**Universe** (16 names, all on Bybit USDT perpetuals):
- L1: ETH, SOL, AVAX
- L2: ARB, OP
- DeFi: AAVE, UNI, MORPHO, PENDLE, GMX
- RWA: ONDO, ENA
- AI: FET, RENDER, TAO
- Infra: LINK
- Hedge slot: BTC (always short, sized by the optimizer)

**Pipeline:**
1. Load each universe member's price-history from cache (per-token for EVM, reference cache for SOL/AVAX/TAO/RENDER/LINK).
2. Compute β-vs-BTC over `--beta-window` days (default 60).
3. Apply diversification: ≤ `--max-per-category` longs per category, then trim to `--max-longs`.
4. Build covariance matrix over `--cov-window` days (default 90) with linear shrinkage `--shrinkage` (default 0.2).
5. Run risk-parity solver → equal-RC weights inside the long sleeve.
6. Solve closed-form: `long_pct = (target_β + 1) / (β_long_avg + 1)`, `short_pct = 1 − long_pct`. Self-funded → gross = 100%.
7. Apply `--max-single-pct` cap with proportional redistribution.
8. Optional `--alpha-shorts N`: pick highest-β + worst-recent-return names from the eligible-but-unselected pool; reserve a small slice (≤ 5% of gross) split equally between them, the rest stays in BTC.
9. Persist `PortfolioBook` JSON to `$DATA_DIR/portfolios/<id>-<ts>.json`.

**Output schema (`PortfolioBook`)**:
- `target_net_beta` / `realized_net_beta` (within ±0.05 of target)
- `realized_long_avg_beta`
- `long_pct_total` / `short_pct_total` (sum to 1.0)
- `coverage_ratio` — share of selected names with real β (vs fallback)
- `cov_window_days` / `cov_observations`
- `optimizer_iterations` / `optimizer_converged`
- `category_breakdown` — net % per category (signed)
- `rebalance_policy` — `cadence_days`, `drift_threshold_pct`, `max_single_position_pct`, `min_categories`, `horizon_months`
- `positions[]` — per-position `(name, category, direction, bybit_symbol, target_pct, weight_in_sleeve, beta_btc, risk_contribution_pct, entry_price_usd, entry_date)`

**Key flags:**
- `--target-beta` (0.4)
- `--min-longs` (10) / `--max-longs` (15)
- `--max-per-category` (4) — diversification cap
- `--min-categories` (3) — book emits a warning if violated
- `--max-single-pct` (0.15) — single-position notional cap
- `--alpha-shorts` (0) — number of alpha shorts to add alongside BTC hedge
- `--cov-window` / `--shrinkage` — covariance estimation
- `--drift-threshold` (0.05) — embedded into the saved policy for `portfolio-rebalance`
- `--rebalance-days` (7) — embedded cadence
- `--horizon-months` (3)
- `--exclude NAME[,NAME…]` — drop named universe members before selection. Used for swap workflows: "exclude FET" lets RENDER take that AI slot.
- `--include-only NAME[,NAME…]` — whitelist mode; only the listed names are eligible. Useful for stress-testing a specific subset.
- `--long-only` — skip the BTC hedge entirely. Gross = net = 100% long; `--target-beta` and `--alpha-shorts` are ignored under this mode. See `docs/strategies/long-only-book.md` for the operator playbook.

**Long-only example (no hedge, all 9 high-conviction longs):**

```bash
token-research portfolio-build longonly-v1 --long-only \
    --max-longs 9 --max-per-category 4 \
    --include-only ETH,SOL,ARB,MORPHO,AAVE,GMX,ONDO,TAO,RENDER
```

Same `portfolio-rebalance` command works on long-only books — drift detection iterates fewer rows because there's no short side.

**Tighter book example (10 positions including hedge):**

```bash
token-research portfolio-build bullhedge-v2 \
    --target-beta 0.4 --min-longs 9 --max-longs 9 \
    --max-per-category 2 --alpha-shorts 0
```

**Swap a name without rebuilding the universe file:**

```bash
# Replace FET with RENDER in the AI slot — the optimizer reweights every
# other position to keep net β = 0.4 and gross = 100%.
token-research portfolio-build bullhedge-v2 \
    --max-longs 9 --max-per-category 2 --exclude FET
```

When `min_longs` is not satisfied (e.g. you ask for 9 longs but only 8
have a populated cache) the build emits a warning instead of aborting —
the optimizer still runs against the available names. A hard error only
fires when fewer than 2 longs survive the filters.

**Required cache:** populate via `price-history --reference btc` (always), then either `price-history --from-basket latest` or `price-history --reference <name>` for non-EVM members. Build emits a coverage gap listing the missing assets.

---

## portfolio-rebalance  *(new — bullish-hedged book)*

Loads a saved `PortfolioBook`, marks each position to current cache price,
and emits the trade list needed to bring the book back to its target weights.

```bash
# Latest saved portfolio.
token-research portfolio-rebalance latest

# Specific portfolio with tighter drift threshold.
token-research portfolio-rebalance bullhedge-v1 --drift-threshold 0.03

# Direct file path.
token-research portfolio-rebalance .token-research/portfolios/v1-...json
```

**Pipeline:**
1. Resolve `query` to a saved `PortfolioBook` (latest, by tag prefix, or absolute path).
2. For each position, look up current price (per-token cache → reference cache fallback by short name).
3. Mark to market: long value = `target_pct × (current_price / entry_price)`; short value = `target_pct × (2 − ratio)`.
4. Compute drift = `current_pct − target_pct` per position.
5. Flag positions where `|drift| ≥ drift_threshold_pct` for trading.
6. Emit per-position pnl + cumulative book pnl + trade list (no execution).

**Output:**
- `realized_long_pct` / `realized_short_pct` / `realized_net_pct` (mark-to-market)
- `cumulative_pnl_pct` — book PnL since build
- `days_since_build`
- `rows[]` — per-position `(target_pct, current_pct, drift_pct, side_to_trade, notional_change_pct, entry_price_usd, current_price_usd, pnl_pct)`
- `trades[]` — subset of `rows` where `needs_trade == True`
- `cache_misses[]` — positions whose price cache is stale or missing (drift treated as zero, surfaces as warning)

**Workflow:**

```bash
# Once: seed BTC reference + universe.
token-research price-history --reference btc --since 2025-01-01
for ref in eth sol avax tao render link; do
    token-research price-history --reference $ref --since 2025-01-01
done
# Per-token EVM:
token-research price-history MORPHO --chain ethereum --address 0x58D... --since 2025-01-01 --exchange binance
# … etc for AAVE, UNI, ARB, OP, ONDO, ENA, FET, GMX, PENDLE.

# Build the book.
token-research portfolio-build bullhedge-v1 --target-beta 0.4

# A week later — refresh cache and check drift.
token-research price-history --reference btc --since 2026-01-01  # incremental
token-research portfolio-rebalance bullhedge-v1
```

**See:** `docs/tasks/06-bullish-hedged-portfolio.md` for the design doc.

---

## Performance notes

As of 2026-04-11, the pipeline has an **in-process memoization layer** (`providers/_memoize.py`) applied to the hot read paths:

- `blockscout.get_token_info`, `get_token_holders`, `get_address_info`
- `defillama.get_protocols` (~3800ms cold → 0ms warm, ~760K× speedup)
- `defillama.get_yield_pools` (~5MB payload, cached for the run)
- `defillama._fees_summary` (shared between `get_protocol_fees` and `get_protocol_revenue`)
- `rpc.get_code` (used by `is_contract` — called per holder by both `locks` and `staking`)

This makes `deep-report` roughly 4× faster by eliminating the duplicate calls that `score → compare → locks/staking/holders/yields` used to trigger. Cache is per-process; each CLI invocation starts fresh.

---

## Worked examples

See the four reference reviews in this directory:

- [`examples/morpho-deep-analysis.md`](../examples/morpho-deep-analysis.md) — mature EVM DeFi token (lending)
- [`examples/ondo-analysis.md`](../examples/ondo-analysis.md) — mature EVM RWA token (Treasuries + Global Markets)
- [`archived/docs/katana_analysis.md`](../archived/docs/katana_analysis.md) — pre-TGE EVM program (uses `prelaunch`)
- [`archived/docs/hbar_analysis.md`](../archived/docs/hbar_analysis.md) — non-EVM L1 (documents pipeline limits)

And the scoring protocol itself:

- [`docs/risk_revenue_estimation.md`](risk_revenue_estimation.md) — CPD 2.0 formal model
