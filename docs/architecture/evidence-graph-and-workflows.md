# Evidence Graph And Workflows

## Main workflow

1. Resolve token identity from ticker, project name, or address.
2. Fetch core metadata and verify decimals, symbol, and chain.
3. Discover pools and liquidity surfaces.
4. Fetch current holders and historical transfer surfaces.
5. Detect lock, vesting, timelock, and staking contracts.
6. Pull curated unlock schedules if enabled.
7. Build labeled entities and relation graph.
8. Compute scoring and generate the final dossier.

## Data model

Recommended entities:

- `Token`
- `Address`
- `Contract`
- `Entity`
- `Pool`
- `UnlockEvent`
- `MetricObservation`

Recommended relation edges:

- `TRANSFER`
- `HAS_LABEL`
- `CONTROLS`
- `LOCKS`
- `STAKES`
- `PROVIDES_LIQUIDITY`
- `UNLOCKS_TO`

## Provenance contract

Every observation should keep:

- source system
- endpoint, query ID, or contract method
- fetched timestamp
- block number when relevant
- confidence
- raw record reference

## Confidence policy

- on-chain contract reads: high confidence
- verified explorer data: high confidence
- SQL aggregations over raw events: medium to high confidence
- curated unlock schedules: medium confidence
- entity heuristics and AI inferences: low to medium confidence

## Coverage gap handling

If the pipeline cannot support a metric:

- emit `null`
- add a machine-readable warning
- state whether the gap is due to missing source coverage, unsupported chain, or ambiguous attribution

## Workflow split

Build the CLI in two execution modes:

- `snapshot` mode for current state
- `forensics` mode for historical windows around events such as unlocks, treasury transfers, or listing events
