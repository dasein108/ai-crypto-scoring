# Getting Started

A concise install + first-run guide for `token-research`.

## What this is

`token-research` is an **EVM-first crypto token research CLI**. You give it a ticker, project name, or token address; it resolves a canonical identity, runs domain-specific collectors (supply, holders, liquidity, locks, staking, yields, flows, …) against a provider registry, and emits structured **JSON** (or Markdown for `deep-report`) with full provenance, warnings, and explicit coverage gaps. Every metric carries the source it came from, and every command tells you what it *couldn't* answer.

It is designed to run **fully free** with zero API keys. Provider keys and an RPC URL only *improve* coverage — they don't gate the core workflow.

## Requirements

- **Python >= 3.11**
- The only runtime dependency is `python-dotenv` (for `.env` autoloading). All HTTP and JSON handling use the standard library.

## Install

Editable install with dev dependencies (just `pytest`):

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

This installs the `token-research` console entry point.

### Optional `[ccxt]` extra

The CEX price-history features (`price-history`, real β-vs-BTC in `momentum-screen`, and the `momentum-backtest` / `portfolio-*` price cache) require the `ccxt` library. It is **opt-in**:

```bash
pip install -e ".[ccxt]"
```

Without it, the core research commands still work; only the price-cache / momentum / portfolio commands need it.

### Running without installing

```bash
PYTHONPATH=src python3 -m token_research deep-report ETH --format markdown
```

## `.env` setup

The CLI **walks up from its own module path to find a `.env`** automatically, so you can run it from any directory. Explicit shell environment variables always beat `.env` values.

Create a `.env` in the project root if you want to enable optional providers. All variables are prefixed `TOKEN_RESEARCH_`.

### What actually changes behavior today

| Variable | Effect today | Without it |
|---|---|---|
| *(nothing)* | **Most commands work fully free.** DexScreener, DeFiLlama, and Blockscout are free and always on. | — |
| `TOKEN_RESEARCH_ETH_RPC_URL` | Enables on-chain truth on Ethereum: `totalSupply`/`decimals` (canonical `supply`), `eth_getCode` contract detection, and vesting/staking/timelock fingerprinting used by `locks`, `staking`, `unlocks`, `score`, `fairlaunch`. | Supply falls back to Blockscout; on-chain custody fingerprinting is skipped (coverage gaps emitted). |
| `TOKEN_RESEARCH_BASE_RPC_URL` | Same, for the `base` chain. | — |
| `TOKEN_RESEARCH_ARB_RPC_URL` | Same, for `arbitrum`. | — |
| `TOKEN_RESEARCH_OP_RPC_URL` | Same, for `optimism`. | — |
| `TOKEN_RESEARCH_DUNE_API_KEY` | Unlocks `flows` (7-day categorized netflows) and enriches `labels` with Dune's labeled-address set. | `flows` can't run; `labels` falls back to Blockscout-only tags. |
| `TOKEN_RESEARCH_TOKENOMIST_API_KEY` | With `--with-premium`, unlocks the **curated** unlock schedule + allocation buckets in `unlocks`. | `unlocks` still runs in free mode using the on-chain vesting projection. |
| `TOKEN_RESEARCH_BLOCKSCOUT_API_KEY` | Higher Blockscout rate limits. | Blockscout free tier still works. |

> **Plain version:** most commands work with no keys at all. An **RPC URL** is the single highest-value addition (it makes on-chain numbers canonical). A **Dune key** unlocks `flows`/`labels`. **Tokenomist** (with `--with-premium`) unlocks curated unlocks.

### Recognized but not yet wired into behavior

These are read into config but don't change command output today; they exist for future providers: `TOKEN_RESEARCH_ETHERSCAN_API_KEY`, `TOKEN_RESEARCH_FOOTPRINT_API_KEY`, `TOKEN_RESEARCH_FLIPSIDE_API_KEY`, `TOKEN_RESEARCH_MOBULA_API_KEY`, `TOKEN_RESEARCH_ARKHAM_API_KEY`, `TOKEN_RESEARCH_MESSARI_API_KEY`, `TOKEN_RESEARCH_BSC_RPC_URL`.

### Premium providers

Premium provider slots (Tokenomist, Arkham, Messari) only appear in the registry when you pass `--with-premium`, and each still requires its own key to actually return data:

```bash
token-research unlocks ETH --with-premium
```

## Offline mode

Set `TOKEN_RESEARCH_OFFLINE=1` to **skip all network calls**. Useful for tests and for inspecting the CLI shape without hitting any API. The test suite runs in this mode by default.

```bash
TOKEN_RESEARCH_OFFLINE=1 token-research resolve ETH
```

Explicit shell env wins over `.env`, so this works even if `.env` sets it differently. Accepted truthy values: `1`, `true`, `yes`, `on`.

Other config knobs:

| Variable | Default | Purpose |
|---|---|---|
| `TOKEN_RESEARCH_REQUEST_TIMEOUT_SECONDS` | `4.0` | Per-request HTTP timeout (float, must be > 0). |

## Data directory

Defaults to `.token-research/` in the current working directory. Override with `TOKEN_RESEARCH_DATA_DIR`. The CLI creates these subdirectories on startup and writes artifacts as commands run:

| Path | What lands there |
|---|---|
| `cache/` | Cached intermediate data. |
| `raw/<provider>/<command>/` | Raw API responses, persisted for debugging / provenance. |
| `reports/` | `deep-report` JSON output (`<chain>-<symbol>-deep-report.json`), unless `--no-persist`. |
| `trends/<chain>-<symbol>/<date>.json` | Per-token `trend` snapshots used for direction-of-travel signals. |
| `baskets/` | Saved `momentum-screen` baskets. |
| `prices/` | CEX OHLCV cache (per-token + `_reference/` for BTC/ETH/SOL) consumed by `momentum-backtest` and `portfolio-*`. Requires the `[ccxt]` extra to populate. |

## 60-second first run

Resolve a token to its canonical identity, then run the full dossier:

```bash
# 1. Resolve (pass --address to skip resolver guesswork — see the walkthrough)
token-research resolve MORPHO --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2

# 2. Full report in Markdown (markdown is only available for deep-report)
token-research deep-report MORPHO --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 --format markdown
```

Every other command emits JSON. The two valid `--format` values are `json` (all commands) and `markdown` (`deep-report` only).

## Next steps

- **[walkthrough.md](walkthrough.md)** — research a token end-to-end, and how to read the scoring pillars, warnings, and coverage gaps.
- **[cli-reference.md](cli-reference.md)** — the authoritative command surface: every command, flag, provider, and output field.
