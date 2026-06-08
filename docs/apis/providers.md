# Data Providers

Single source of truth for `token-research`'s data providers. **Status is tied to the code** — a provider is `LIVE` only if it has a module in `src/token_research/providers/` *and* a `ProviderDescriptor` in `registry.py`. Everything else is declared-but-not-wired (a descriptor exists, but no collector calls it yet) or fully planned.

This file supersedes the older `source-catalog.md`, `endpoint-playbook.md`, and `domains/tool-stack.md`.

## Status at a glance

| Provider | Module | Access tier | Status | Provides | Env var | Docs |
|---|---|---|---|---|---|---|
| **RPC** | `providers/rpc.py` | local | **LIVE** | `eth_call` (totalSupply/decimals/balanceOf), `eth_getCode`, VestingWallet / TokenTimelock / ERC-4626 / Synthetix-staking fingerprinting | `TOKEN_RESEARCH_<ETH\|BASE\|ARB\|OP>_RPC_URL` | [eips.ethereum.org](https://eips.ethereum.org/) |
| **DexScreener** | `providers/dexscreener.py` | free | **LIVE** | Pool discovery, liquidity snapshots, price | — | [docs](https://docs.dexscreener.com/api/reference) |
| **DeFiLlama** | `providers/defillama.py` | free | **LIVE** | Protocol directory, fees/revenue, yields pools, price-by-contract | — | [api-docs](https://api-docs.defillama.com/) |
| **Blockscout** | `providers/blockscout.py` | free / key | **LIVE** | Token info, holder snapshots, address tags / public labels | `TOKEN_RESEARCH_BLOCKSCOUT_API_KEY` (rate limits only) | [docs](https://docs.blockscout.com/devs/apis/rest) |
| **Dune** | `providers/dune.py` | key | **LIVE** | Async SQL for flows + labeled addresses | `TOKEN_RESEARCH_DUNE_API_KEY` | [docs](https://docs.dune.com/api-reference/executions/endpoint/execute-sql) |
| **Tokenomist** | `providers/tokenomist.py` | premium | **LIVE** (opt-in `--with-premium`) | Curated unlock schedules + allocation buckets | `TOKEN_RESEARCH_TOKENOMIST_API_KEY` | [docs](https://docs.unlocks.app/api-documents/api-endpoints) |
| **CCXT** | `prices/` (separate module) | opt-in extra | **LIVE** (install `[ccxt]`) | CEX OHLCV cache for momentum/portfolio/β | — | — |
| Etherscan | — | key | declared, no module | (future) explorer-family access | `TOKEN_RESEARCH_ETHERSCAN_API_KEY` | [docs](https://docs.etherscan.io/) |
| Footprint | — | key | declared, no module | (future) historical SQL | `TOKEN_RESEARCH_FOOTPRINT_API_KEY` | [docs](https://docs.footprint.network/reference/post_native-async) |
| Flipside | — | key | declared, no module | (future) supplemental analytics | `TOKEN_RESEARCH_FLIPSIDE_API_KEY` | [docs](https://docs.flipsidecrypto.com/api) |
| Mobula | — | key | declared, no module | (future) holder / LP-lock enrichment | `TOKEN_RESEARCH_MOBULA_API_KEY` | [docs](https://docs.mobula.io/rest-api-reference/introduction) |
| Arkham | — | premium | declared, no module | (future) entity attribution | `TOKEN_RESEARCH_ARKHAM_API_KEY` | [docs](https://intel.arkm.com/api/docs) |
| Messari | — | premium | declared, no module | (future) curated unlocks/categories | `TOKEN_RESEARCH_MESSARI_API_KEY` | [docs](https://docs.messari.io/user-guides/intel/token-unlocks) |
| GeckoTerminal / Token Terminal | — | — | catalog-only (no descriptor) | (future) secondary pool discovery / fundamentals | — | — |

> The `etherscan`/`footprint`/`flipside`/`mobula`/`arkham`/`messari` descriptors appear in the provider registry (so they show up as *advertised sources* and gate on their env var) but **no collector calls them today**. Treat their env vars as reserved. See [getting-started.md](../getting-started.md#recognized-but-not-yet-wired-into-behavior).

## Canonical-truth priority

When more than one source can answer the same question, prefer in this order (`CLAUDE.md`: "on-chain first"):

1. **On-chain contract calls & logs** (RPC) — canonical numeric truth.
2. **Explorer APIs** (Blockscout) — holder snapshots, labels.
3. **SQL analytics** (Dune) — historical windows, categorized flows.
4. **Curated tokenomics / entity vendors** (Tokenomist) — treated as *claims*, not truth.
5. **AI-generated inference** — last resort, always flagged.

Curated schedules (Tokenomist etc.) are claims to validate against on-chain reality, never canonical.

## By research need → which LIVE provider

| Need | Use today | Notes |
|---|---|---|
| Token → pool discovery, price, liquidity | DexScreener | Fastest DEX-pair discovery |
| Price by contract (cross-check) | DeFiLlama | Confidence-scored |
| Protocol fundamentals: fees / revenue / TVL / yields | DeFiLlama | `/protocols`, `yields.llama.fi` |
| Total supply / decimals | RPC → Blockscout fallback | Direct contract call preferred |
| Top-holder snapshot | Blockscout | EOA vs contract via `eth_getCode` |
| Categorized 7-day netflows | Dune (key) | `flows` command |
| Address labels / categories | Dune labels + Blockscout tags | `labels` command |
| Vesting / timelock / staking custody | RPC fingerprinting | `locks`, `staking` |
| LP-lock state | Blockscout LP-holder analysis | Distinct from pool liquidity |
| Unlock calendar (curated) | Tokenomist (`--with-premium`) | Plus free on-chain projection |
| CEX OHLCV / β-vs-BTC | CCXT (`[ccxt]` extra) | Populates `prices/` cache |

## Endpoint reference (LIVE providers)

**DexScreener** — [docs](https://docs.dexscreener.com/api/reference)
- `GET https://api.dexscreener.com/token-pairs/v1/{chainId}/{tokenAddress}` — pairs by token
- `GET https://api.dexscreener.com/latest/dex/pairs/{chainId}/{pairId}` — pair details

**DeFiLlama** — [api-docs](https://api-docs.defillama.com/)
- `GET https://coins.llama.fi/prices/current/{coins}` — current price by contract
- `GET https://api.llama.fi/protocols` — protocol directory (resolver cross-check)
- `https://yields.llama.fi/pools` — yield pools

**Blockscout** — [docs](https://docs.blockscout.com/devs/apis/rest)
- `GET {blockscout_base}/api/v2/tokens/{tokenAddress}/holders` — holder snapshot
- `GET {blockscout_base}/api/v2/tokens/{tokenAddress}` — token info

**Dune** — [docs](https://docs.dune.com/api-reference/executions/endpoint/execute-sql)
- Parameterized async SQL execution for historical balances, labels, and flows.

**Tokenomist** — [docs](https://docs.unlocks.app/api-documents/api-endpoints)
- `GET https://api.unlocks.app/v1/token/{SYMBOL}` — curated unlock schedule + allocation buckets for one token

### On-chain standards used by RPC fingerprinting

- OpenZeppelin VestingWallet / Finance: https://docs.openzeppelin.com/contracts/5.x/api/finance
- TimelockController: https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/governance/TimelockController.sol
- Uniswap V2 pair: https://docs.uniswap.org/contracts/v2/reference/smart-contracts/pair
- EIP-4626 (vaults): https://eips.ethereum.org/EIPS/eip-4626

## Planned / not yet wired

These were scoped in the original design memos but have no working collector. Endpoints kept here for whoever implements them:

- **Footprint** — `POST https://api.footprint.network/api/v1/native/async`, `GET …/{execution_id}/results` (historical SQL)
- **Mobula** — `GET /api/2/token/details`, `/holder-positions`, `/security` (holder + LP-lock enrichment)
- **Arkham** — entity attribution, top-holder intelligence ([endpoint list](https://intel.arkm.com/llms.txt))
- **Messari Token Unlocks** — curated unlock + allocation dataset
- **GeckoTerminal** — secondary pool discovery; **Token Terminal** — standardized fundamentals

## Integration rules (for new adapters)

- Treat endpoint field names as unstable until wrapped by an internal adapter.
- Store raw and normalized payloads separately (`cache.py` persists raw to `.token-research/raw/`).
- Keep rate-limiting and retries in the adapter layer.
- Never let premium-only coverage leak into free-mode logic without explicit guards.

See [Adding a new provider](../../CLAUDE.md#adding-a-new-provider) for the registration steps.
