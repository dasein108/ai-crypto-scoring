# CLI Architecture

> **Status: Live.** The command surface itself is documented authoritatively in [../cli-reference.md](../cli-reference.md) — this doc covers the *architecture* (request flow, flags model, output contract, implementation rules), not the full command list.

## CLI name

`token-research`. It accepts a ticker, project name, or token address and normalizes that input into a canonical project identity.

## Command surface

~30 commands across identity, market structure, supply/ownership, locks/unlocks, inference/reporting, and a trading-research stack. See [../cli-reference.md](../cli-reference.md) for the full list, and `cli.py:COMMANDS` for the registry.

## Flags

**Global flags** (all commands):

- `--chain ethereum|base|arbitrum|optimism`
- `--address 0x...` — pin the exact contract, bypassing resolver guesswork
- `--format json|markdown` — `markdown` is only valid for `deep-report`; everything else is `json`
- `--with-premium` — add premium provider slots (Tokenomist / Arkham / Messari), each still gated on its own key

Per-command flags are added in the `build_parser` loop in `cli.py` (e.g. `risk --cpd-stage`, `prelaunch --cpd-stage`, `deep-report --no-persist`, the momentum/portfolio knobs). There is **no** `--format md`/`table`, `--as-of`, `--with-arkham`, `--with-tokenomist`, or `--out` flag — those were scoped in an early draft and never built.

## Request flow

1. **CLI** (`cli.py`) parses args, walks up from `__file__` to find `.env`, loads `AppConfig.from_env()`, dispatches to the command handler.
2. **Resolver** (`resolver.py`) classifies the query (address / ticker / project_name), checks known aliases, cross-checks DeFiLlama `/protocols` for a canonical address, then falls back to DexScreener filtered by EVM chains + minimum pool liquidity + same-chain preference.
3. **Command handler** (`commands/<domain>.py`) calls `resolve_with_sources()`, runs domain collectors, returns a `CommandResult`.
4. **`deep-report`** runs all command modules, aggregates, generates narrative + evidence summary, dedupes coverage gaps, and optionally persists to `$TOKEN_RESEARCH_DATA_DIR/reports/`.

## Output contract

Every command emits a `CommandResult` with:

- `command`
- `input`
- `resolved_identity`
- `metrics`
- `sources`
- `warnings`
- `coverage_gaps`
- `generated_at`

`deep-report` additionally emits `scores`, `narrative`, and `evidence_summary`. (See the canonical schema in `CLAUDE.md` → "Output contract".)

## Implementation rules

- One adapter per source in the **provider registry** (`providers/registry.py`); keep source-specific parsing out of business logic.
- Store raw responses (`cache.py` → `.token-research/raw/`) for replayable debugging.
- Cache hot read paths — many upstreams are rate-limited (`providers/_memoize.py`).
- Make metric functions pure over normalized source payloads where possible.
- Canonical numeric truth is on-chain first (RPC > explorer > vendor); curated vendor data is a claim, not truth.
