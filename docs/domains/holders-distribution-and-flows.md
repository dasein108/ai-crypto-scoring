# Holders, Distribution, And Flows

## Objective

The CLI should answer who holds the token, how concentrated ownership is, and whether important clusters are accumulating, holding, or distributing.

## Minimum holder outputs

- top holders snapshot
- labeled holder breakdown by category
- top 10 and top 100 share of supply
- Gini coefficient
- Nakamoto coefficient at one explicit threshold
- entropy or Theil as a second concentration metric
- concentration trend over daily or weekly windows

## Labeling strategy

Use a layered approach:

1. Explorer and indexer labels
2. Dune `labels.addresses`
3. Arkham entity attribution
4. Project-specific known wallets from docs or governance forums
5. Internal heuristics for unlabeled clusters

Every label should preserve:

- source
- confidence
- evidence reference
- last seen timestamp

## Flow analysis surfaces

The most important flow paths are:

- vesting or treasury to self-custody
- vesting or treasury to CEX deposit wallets
- vesting or treasury to DEX router or pool interaction
- treasury to market-maker or bridge
- whales accumulating from CEX or redistributing to CEX

## Core computations

### Balance snapshots

- use Blockscout or Mobula for fast current snapshots
- rebuild balances from transfer logs in Dune, Footprint, or Flipside for historical analysis

### Daily concentration

- generate daily balances from transfer deltas
- filter out zero and dust balances
- compute concentration metrics on both raw addresses and labeled entities

### Behavior around unlock dates

For each unlock event window:

- compute net outflow from likely unlock wallets
- classify destinations into CEX, DEX, bridge, treasury, or self-custody
- compare amount moved with the published unlock amount

## Interpretation rules

- exchange wallets can make concentration look worse than real beneficial ownership
- staking or vault contracts can also look like whales if left unlabeled
- bridge escrow contracts can distort supply location by chain
- always publish both raw-address and labeled-entity views

## Research references

- Dune labels docs: https://docs.dune.com/data-catalog/curated/labels/address-labels
- Blockscout token API docs: https://docs.blockscout.com/devs/apis/rest
- Arkham docs: https://intel.arkm.com/api/docs
- Mobula holder positions: https://docs.mobula.io/rest-api-reference/endpoint/token-holder-positions
- Bitquery holder metrics overview referenced in the base memo
- Wealth concentration research: https://arxiv.org/pdf/2207.01340.pdf
- Wealth inequality study: https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2021.730122/full


---

## Implemented by

- **`holders`** — top-10 / top-20 share, **Gini**, `nakamoto_51` (Blockscout snapshot). *Note: top-100 share and entropy/Theil from the original design were not built; current concentration metrics are top-10/20 + Gini + Nakamoto.*
- **`flows`** — 7-day categorized netflows (CEX / DEX / bridge / treasury) via Dune SQL (key-gated).
- **`relations`** — evidence-graph view that joins holders + labels + flows.

See [../cli-reference.md](../cli-reference.md) for exact output fields.
