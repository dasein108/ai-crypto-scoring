# Research Literature And Guides

This file collects the theory and guide layer that should shape the CLI's methodology.

## Tokenomics and issuance research

### ICOs and disclosure quality

- NBER Working Paper 24774
- Link: https://www.nber.org/system/files/working_papers/w24774/w24774.pdf
- Use in this project: motivates a disclosure and commitment pillar in the fairness score rather than relying only on price and FDV

### Token value and entrepreneur commitment

- NBER Working Paper 24418
- Link: https://www.nber.org/papers/w24418
- Use in this project: supports measuring utility sinks, fee capture, buybacks, burns, or required token usage

## Distribution and decentralization metrics

### Multi-metric concentration analysis

- How centralized is decentralized?
- Link: https://arxiv.org/pdf/2207.01340.pdf
- Use in this project: supports using Gini, entropy, and Nakamoto together instead of a single concentration metric

### Wealth inequality interpretation

- Characterizing Wealth Inequality in Cryptocurrencies
- Link: https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2021.730122/full
- Use in this project: warns that different concentration metrics react differently to large holders and should be tracked over time

## Address clustering and attribution

### Foundational clustering work

- A Fistful of Bitcoins
- Link: https://cseweb.ucsd.edu/~smeiklejohn/files/imc13.pdf
- Use in this project: justifies graph-based clustering and evidence-backed attribution, while also reminding us that heuristics can leak privacy

### Limits of heuristic clustering

- Assessing the Efficacy of Heuristic-Based Address Clustering for Bitcoin
- Link: https://arxiv.org/pdf/2403.00523
- Use in this project: supports confidence-scored inference and argues against hard-coding low-evidence relations as fact

## Implementation guides

### On-chain lock primitives

- OpenZeppelin VestingWallet docs
- https://docs.openzeppelin.com/contracts/5.x/api/finance

### Governance delay primitives

- OpenZeppelin TimelockController source
- https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/governance/TimelockController.sol

### Vault-based staking wrappers

- EIP-4626
- https://eips.ethereum.org/EIPS/eip-4626

### LP reserve math

- Uniswap V2 pair reference
- https://docs.uniswap.org/contracts/v2/reference/smart-contracts/pair

## Methodological implications

- fairness is a composite judgment, not a single valuation ratio
- schedules and investor categories often need curated providers
- on-chain validation is still mandatory around unlock dates
- raw address concentration and labeled entity concentration should both be reported
- AI should generate hypotheses and narratives on top of evidence, not replace deterministic metrics
