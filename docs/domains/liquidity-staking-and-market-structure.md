# Liquidity, Staking, And Market Structure

## Objective

The CLI should measure whether the token can absorb supply, where that liquidity lives, and whether LP ownership is safe or removable.

## Minimum outputs

- discovered pools by chain and DEX
- liquidity by pool in token units and USD
- share of total token liquidity concentrated in the top pools
- LP ownership distribution where data exists
- LP burned, locked, and unlocked percentages when an enrichment source supports it
- staking TVL or deposited token amount
- liquidity to unlock ratio for the next 30, 90, and 180 days

## Pool discovery workflow

Preferred order:

1. DexScreener for token-to-pair discovery and quick ranking
2. GeckoTerminal as a second pool discovery surface
3. On-chain factory lookup for DEX-specific exactness if needed

## LP present versus LP locked

These are different metrics:

- `token_in_lp`: how many tokens currently sit inside pools
- `lp_locked`: whether the LP position itself is burned or locked and therefore difficult to remove

The CLI must report both. High token liquidity does not imply that the liquidity is durable.

## Staking surfaces

Track the following classes separately:

- native staking or validator bonding
- protocol staking contracts
- ERC-4626 vault wrappers
- liquidity mining positions

Do not collapse them into a single number unless the methodology states exactly what qualifies as locked supply.

## Derived risk metrics

- next unlock amount divided by total DEX liquidity
- next unlock amount divided by top-pool liquidity
- percent of liquidity controlled by the project team or treasury
- LP lock duration and locker concentration when available

## Useful sources

- DexScreener API: https://docs.dexscreener.com/api/reference
- GeckoTerminal docs: https://apiguide.geckoterminal.com/
- DeFiLlama API docs: https://api-docs.defillama.com/
- Mobula token security cookbook: https://docs.mobula.io/cookbooks/token-security-liquidity-analysis
- Uniswap V2 pair reference: https://docs.uniswap.org/contracts/v2/reference/smart-contracts/pair


---

## Implemented by

- **`pools`** / **`liquidity`** — DexScreener pool discovery + liquidity snapshots; Blockscout LP-holder analysis incl. **LP-lock** enrichment.
- **`staking`** — staking-custody fingerprinting (see also tokenomics-locks-and-unlocks).

See [../cli-reference.md](../cli-reference.md) for exact output fields.
