# Walkthrough — research a token end-to-end

This is the "I have a token, now what?" tutorial. We use **MORPHO** (Morpho, a mature Ethereum lending token) throughout:

- Symbol: `MORPHO`
- Chain: `ethereum`
- Address: `0x58D97B57BB95320F9a05dC918Aef65434969c2B2`

If you haven't installed yet, see [getting-started.md](getting-started.md). Everything below works in free mode; an Ethereum RPC URL makes the on-chain numbers canonical.

---

## Step 1 — Resolve, and why you pass `--address`

```bash
token-research resolve MORPHO --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2
```

The resolver turns a ticker / project name / address into one **canonical identity** (symbol, project name, chain, token address). It cross-checks DeFiLlama's `/protocols` directory and falls back to DexScreener search filtered by EVM chains + a minimum pool-liquidity floor.

**Why pass `--address` explicitly:** ticker resolution is ambiguous. Squatter tokens and memecoins frequently share or mimic tickers, and pre-TGE programs have no canonical address at all. Passing `--address` skips the guessing entirely and pins the exact contract. **Rule of thumb: if you know the address, always pass it.** (For pre-TGE projects with no token yet, use `prelaunch` instead — `resolve` can't help there.)

The output is a `CommandResult` (see the output contract in [cli-reference.md](cli-reference.md)): `resolved_identity`, `sources`, `warnings`, `coverage_gaps`.

---

## Step 2 — Run the full dossier

```bash
token-research deep-report MORPHO --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 --format markdown
```

`deep-report` runs every collector (supply, holders, liquidity, locks, staking, unlocks, yields, flows, labels, relations, compare, risk, fairlaunch, …), folds them into a single dossier, computes the 6-pillar `score`, generates a narrative + evidence summary, and (unless `--no-persist`) saves JSON to `.token-research/reports/ethereum-morpho-deep-report.json`.

`--format markdown` is human-readable and **only available for `deep-report`**. Every other command emits JSON (`--format json`).

---

## Step 3 — Read the output

### The 6 scoring pillars

`score` (embedded in `deep-report`) produces a composite **0–100** from six weighted pillars. See [scoring-overview.md](scoring-overview.md) for the full table; the short version:

| Pillar | Weight | "Good" looks like |
|---|---|---|
| `supply_pressure` | 0.22 | High protocol-owned %, real revenue model, low unlock overhang |
| `ownership_quality` | 0.22 | Low EOA-only top-10 share, high nakamoto, low gini (protocol custody excluded) |
| `liquidity_quality` | 0.22 | Deep DEX liquidity, spread across venues, LP locked |
| `fundamental_support` | 0.08 | High TVL, efficient mcap/TVL, multi-chain, healthy fees/mcap |
| `governance_commitment` | 0.13 | Real vesting/staking + protocol-owned supply under multisig custody |
| `token_capture` | 0.13 | The token itself accrues protocol economics (holder accrual), not just TVL |

**Composite = weighted average, then a coverage-ratio penalty** of −15 points × (1 − coverage_ratio). So a token with lots of missing inputs can't accidentally score well — the penalty drags it down and the gaps are listed explicitly.

### Warnings and coverage gaps — read these first

- **`warnings`**: `{code, message, severity}` — things that look off (e.g. thin liquidity, concentration, stale data).
- **`coverage_gaps`**: `{metric, reason, suggested_source}` — what the tool *couldn't* answer and how to fix it (often "set an RPC URL" or "needs a Dune key"). A high score with many coverage gaps is a *weak* high score — trust the gaps.

Each metric also carries its `sources`, so you can trace any number back to the provider that produced it (on-chain RPC > explorer > vendor).

---

## Step 4 — Drill down

`deep-report` is the overview; reach for individual commands when a pillar surprises you:

| Question | Command |
|---|---|
| How reliable is the team / code / stack? Rug risk? | `risk MORPHO --chain ethereum --address 0x58D9…` (CPD 2.0) |
| Effective circulation, protocol-owned vs EOA holders | `compare …` |
| Holder concentration (top-10/20, gini, nakamoto) | `holders …` |
| Upcoming unlock cliffs / emission overhang | `unlocks …` (`--with-premium` for curated) |
| DEX depth, venue concentration, LP locks | `liquidity …` |
| Vesting / timelock / staking custody contracts | `locks …`, `staking …` |
| Categorized 7-day netflows (needs Dune key) | `flows …` |
| Was it launched fairly? | `fairlaunch …` |
| Should I enter/exit *now*? | `signal …` (needs `trend` history first) |

---

## Interpreting the result

- **Healthy mature token** (what MORPHO tends to look like): high `score` with *few* coverage gaps, deep multi-venue liquidity, low EOA concentration, real fees/revenue, supply largely protocol-owned/vested, low `risk` %.
- **Concerning**: high score driven by missing data (many coverage gaps), liquidity concentrated in one pool, high EOA top-10 share, large near-term unlock overhang, or a high `risk` % from young/unaudited code.

Remember the four scoring systems answer different questions — don't read `score` as a buy signal. See [scoring-overview.md](scoring-overview.md) for which to use when, and the worked examples [morph_risk_revenue.md](../examples/morpho-deep-analysis.md), [ondo_analysis.md](../examples/ondo-analysis.md), [katana_analysis.md](../archived/docs/katana_analysis.md), and [hbar_analysis.md](../archived/docs/hbar_analysis.md).

→ Full command surface and output schema: [cli-reference.md](cli-reference.md).
