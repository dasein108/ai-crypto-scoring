# Fair Value And Risk Scoring

## Principle

There is no single fair-value formula for crypto projects. The CLI should produce a transparent composite score and a set of comparable metrics instead of pretending that one valuation number is objective.

## Proposed scoring pillars

### 1. Supply pressure

- current unlocked float
- future unlock pressure
- inflation or emissions path
- treasury discretion risk

### 2. Ownership quality

- labeled concentration
- raw concentration
- investor or team dominance
- exchange and bridge distortion adjustment

### 3. Liquidity quality

- DEX liquidity depth
- LP durability
- liquidity concentration across venues
- unlock-to-liquidity ratio

### 4. Fundamental support

- protocol fees or revenue where applicable
- staking demand
- treasury runway if measurable
- utility sinks such as burns, buybacks, or required staking

### 5. Governance and commitment quality

- timelock usage
- multisig transparency
- verified vesting contracts
- disclosure quality and consistency with on-chain behavior

## Output recommendation

Expose:

- a normalized score per pillar from 0 to 100
- the raw metrics behind every score
- a weighted composite score
- a narrative summary that states the biggest bullish and bearish drivers

## Valuation rules

- avoid hard-coding FDV as fair value
- treat fully diluted valuation as a supply ceiling, not a conclusion
- show sensitivity to supply growth and liquidity depth
- make category weights configurable by strategy style

## Suggested research anchors

- ICO finance and disclosure quality: https://www.nber.org/system/files/working_papers/w24774/w24774.pdf
- Token value and commitment framing: https://www.nber.org/papers/w24418
- Concentration metrics for decentralization studies: https://arxiv.org/pdf/2207.01340.pdf
- Wealth inequality interpretation: https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2021.730122/full


---

## Implemented by

The conceptual pillars here map onto **four** distinct shipped systems — see [../scoring-overview.md](../scoring-overview.md) for which to use when:

- **`score`** — 6-pillar structural-quality composite (0–100). *(This doc's 5 conceptual pillars are the ancestor; the implemented set is supply_pressure, ownership_quality, liquidity_quality, fundamental_support, governance_commitment, token_capture.)*
- **`risk`** — CPD 2.0 reliability/risk % ([../risk_revenue_estimation.md](../risk_revenue_estimation.md)).
- **`fairlaunch`** — 15-point launch-fairness rubric.
