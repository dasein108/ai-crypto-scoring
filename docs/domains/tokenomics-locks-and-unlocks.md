# Tokenomics, Locks, And Unlocks

## Core distinction

The CLI must separate three different concepts that are often mixed together:

- `locked_onchain`: tokens currently custodied in contracts that restrict immediate transfer
- `staked_or_deposited`: tokens custodied in staking or vault contracts, which may or may not be withdrawable
- `scheduled_unlocks`: tokenomics releases that may be partially off-chain and require curated datasets

This distinction is critical. A project can have a large future unlock schedule even when very little supply is currently in explicit vesting contracts.

## Metrics to produce

- total supply
- max supply when meaningful
- current on-chain locked balance by contract
- releasable balance by vesting contract when ABI supports it
- current staked or deposited balance by staking contract
- next unlock events by amount, category, and date
- trailing unlock behavior validation: held, bridged, deposited to CEX, or sold on DEX

## Computation hierarchy

### 1. Vesting and timelock contracts

Preferred methods:

- detect verified vesting contracts from public docs, verified source, or ABI fingerprinting
- inspect contract balance of the token
- call `releasable`, `released`, `start`, `duration`, or equivalent methods when available

Interpretation:

- `token_balance` inside the contract is custody
- `releasable_now` is not still locked
- `token_balance - releasable_now` is the best estimate of still-locked balance when the ABI supports it

### 2. Staking and vault custody

Preferred methods:

- for ERC-4626 style vaults, use `totalAssets()`
- for staking contracts, look for `totalStaked()`, `balanceOf()`, or equivalent
- fallback to token balance held by the staking contract

Interpretation:

- treat staking as non-circulating only if the research methodology explicitly excludes readily withdrawable staking balances
- always present staking separately from hard locks

### 3. Scheduled unlocks

Preferred providers:

- Tokenomist
- Messari Token Unlocks
- DeFiLlama Pro emissions endpoints
- Mobula metadata fallback

Interpretation:

- a curated schedule is a planning surface, not canonical truth
- the CLI should cross-check each unlock window against observed on-chain flows

## Required red flags

- large investor or team unlock within 30, 90, or 180 days
- high unlock amount relative to average daily DEX liquidity
- low transparency: no verified vesting contracts and no credible curated schedule
- treasury or foundation contracts with unilateral transfer power

## Research references

- OpenZeppelin VestingWallet: https://docs.openzeppelin.com/contracts/5.x/api/finance
- OpenZeppelin TimelockController: https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/governance/TimelockController.sol
- EIP-4626 vaults: https://eips.ethereum.org/EIPS/eip-4626
- Tokenomist docs: https://docs.unlocks.app/api-documents/api-endpoints
- Messari Token Unlocks docs: https://docs.messari.io/user-guides/intel/token-unlocks
- Mobula token unlock guide: https://docs.mobula.io/guides/token-unlock


---

## Implemented by

- **`locks`** — RPC fingerprinting of OZ VestingWallet / TokenTimelock + auto-discovery + Blockscout name hints.
- **`staking`** — ERC-4626 / Synthetix-staking fingerprinting + auto-discovery.
- **`unlocks`** — free-mode on-chain vesting projection + flow validation; curated Tokenomist schedule with `--with-premium`.

See [../cli-reference.md](../cli-reference.md) for exact output fields.
