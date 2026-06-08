# Entity Intelligence And Relation Discovery

## Objective

The CLI should not only list addresses. It should infer operational clusters such as team, treasury, investors, lockers, market-makers, exchanges, and bridges, while keeping every inference contestable.

## Evidence model

Every relation should keep:

- source
- confidence
- evidence references such as tx hashes, contract calls, or provider record IDs
- competing labels when sources disagree

## Main relation types

- team or treasury controls wallet
- investor receives tokens from vesting or treasury
- wallet sends unlocked tokens to exchange
- wallet provides liquidity into project pools
- wallet interacts with market-maker, bridge, or OTC path

## Practical heuristics

### High confidence

- verified vesting contract transfers
- multisig named in governance or docs
- addresses labeled consistently across reputable sources

### Medium confidence

- repeated treasury funding patterns
- wallet receives from investor allocation cluster and later deposits to exchange
- locker contracts identified by known bytecode or known protocol labels

### Low confidence

- timing-only similarity
- indirect path overlap with no ownership proof

Low-confidence relations should not affect the headline score without explicit configuration.

## Storage model

Recommended graph nodes:

- token
- address
- contract
- entity
- pool
- unlock event

Recommended edges:

- transfer
- labeled_as
- controls
- vests_to
- stakes_to
- provides_liquidity_to
- unlocks_to

## Safe defaults

- do not infer private-person identity
- keep the system organization-first
- show evidence before narrative
- treat AI output as hypothesis generation, not canonical labeling

## Useful sources

- Arkham docs: https://intel.arkm.com/api/docs
- Arkham endpoint index: https://intel.arkm.com/llms.txt
- Dune labels docs: https://docs.dune.com/data-catalog/curated/labels/address-labels
- Mobula docs: https://docs.mobula.io/rest-api-reference/introduction
- Address clustering research: https://cseweb.ucsd.edu/~smeiklejohn/files/imc13.pdf
- Heuristic clustering limits: https://arxiv.org/pdf/2403.00523


---

## Implemented by

- **`relations`** — evidence graph across holders + labels + locks + staking + flows.
- **`labels`** — Blockscout tags + Dune labeled-address set (key-gated).

Entity attribution stays probabilistic: evidence + confidence, never hard-coded certainty. See [../cli-reference.md](../cli-reference.md).
