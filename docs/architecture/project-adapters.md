# Project Adapters

## Scope

The CLI should be EVM-first, but it should not assume that all projects expose the same surfaces. Use adapters.

## Adapter layers

### Resolver adapter

Responsibilities:

- map ticker or project name to token contracts
- handle multiple contracts and wrappers
- detect ambiguous symbols

### Chain adapter

Responsibilities:

- RPC configuration
- explorer selection
- log decoding
- native DEX conventions

### Project adapter

Responsibilities:

- known treasury wallets
- known vesting contracts
- protocol-specific staking contracts
- protocol-specific liquidity venues
- project-specific caveats

## Resolution strategy

When the input is a ticker or name:

1. resolve candidates from multiple metadata sources
2. score candidates by chain, symbol, verified metadata, and liquidity
3. force user selection only if ambiguity remains material

## Initial target set

Phase 1 should optimize for:

- Ethereum
- Base
- Arbitrum
- Optimism
- BNB Chain
- Monad when public infrastructure is stable enough for production use

## Why adapters matter

Ticker-only workflows fail fast when:

- symbols collide across chains
- wrappers and bridged assets share names
- governance token and gas token concepts differ
- staking or vesting contracts are protocol-specific
