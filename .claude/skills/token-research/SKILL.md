---
name: token-research
description: On-chain analysis of an EVM crypto token — risk, structural quality (6-pillar score), liquidity, holders, locks/unlocks, and full dossiers — by driving the token-research CLI. Use when the user wants to research, score, or risk-assess a specific EVM token (Ethereum/Base/Arbitrum/Optimism) by ticker, project name, or contract address, or compare several EVM tokens. For non-EVM L1s (HBAR, SOL, ATOM), pre-TGE programs, or purely qualitative trust/DD, defer to the cpd-crypto-analysis skill instead.
allowed-tools: Bash, Read, Write
---

# token-research (CLI-driven EVM token analysis)

Drive the `token-research` CLI to produce on-chain-grounded, provenance-tracked token analysis. The CLI is the engine; this skill routes the request to the right command(s) and reads back the structured output.

## Step 0: Route first (data regime)

Apply this BEFORE running anything:

```
EVM token with a known or derivable address (Ethereum / Base / Arbitrum / Optimism)?
 ├─ yes → use this skill (below)
 └─ no  → STOP. Hand off to the `cpd-crypto-analysis` skill:
            • non-EVM L1 (HBAR, SOL, ATOM, TIA, TON, native BTC…)
            • pre-TGE / no token yet (e.g. a points program)
            • a purely qualitative trust / team / governance question
```

The CLI is EVM-first and token-centric. Its refusals are the hand-off signal, not a failure. See `docs/skills-architecture.md`.

## Setup (once)

```bash
pip install -e .[dev]            # installs the `token-research` console script
# optional, for momentum/portfolio price features:
pip install -e ".[ccxt]"
```

Run with the console script `token-research <command> …`, or without installing via `PYTHONPATH=src python3 -m token_research <command> …`. Add `TOKEN_RESEARCH_OFFLINE=1` to skip all network calls (deterministic, for smoke checks).

Always pass `--address` and `--chain` when known — it pins the exact contract and skips resolver guesswork (squatter-token defense). Default output is JSON; `deep-report` also supports `--format markdown`.

## Modes (pick by purpose × depth)

### Mode: `quick` — triage in seconds

For "is this worth a closer look?" Run the two scoring systems and read the headlines:

```bash
token-research score <TOKEN> --chain <CHAIN> --address <ADDR>   # 6-pillar structural quality 0–100
token-research risk  <TOKEN> --chain <CHAIN> --address <ADDR>   # CPD 2.0 risk %/tier
```

Report: composite score + the weakest pillar, risk tier, and any `warnings` / `coverage_gaps`. A high score with many coverage gaps is a weak read — say so.

### Mode: `deep` — full dossier

For a real write-up. One command runs every collector + the 6-pillar score + narrative:

```bash
token-research deep-report <TOKEN> --chain <CHAIN> --address <ADDR> --format markdown
```

Read the output structure: `scores` (pillars + composite + coverage_ratio), `narrative`, `evidence_summary`, per-module `metrics`, `warnings`, `coverage_gaps`. Persisted to `.token-research/reports/` unless `--no-persist`.

When a pillar surprises you, drill in with the targeted command: `compare` (effective circulation / protocol-owned vs EOA), `holders` (concentration, gini, nakamoto), `liquidity` (depth, LP locks, slippage), `unlocks` (cliff overhang; add `--with-premium` for curated Tokenomist data), `locks` / `staking` (custody fingerprinting), `yields` (TVL, fees, revenue model).

### Mode: `compare` — several tokens side by side

The `compare` command is a single-token effective-circulation view, so for head-to-head run the same command per token and diff:

```bash
for each token: token-research score <TOKEN> --chain <CHAIN> --address <ADDR>
```

Present a table: composite, the driving pillars, and risk tier per token. Reconcile differences — two tokens with the same composite can have very different pillar shapes (see `docs/walkthrough.md`).

## Reading the output (the contract)

Every command returns a `CommandResult`: `metrics`, `sources`, `warnings`, `coverage_gaps`, `resolved_identity`, `generated_at`. Three rules:

1. **Trace, don't trust** — every metric carries its `sources` (on-chain RPC > explorer > vendor).
2. **Coverage gaps are signal** — they list what couldn't be answered and how to fill it; the composite is penalized for them. Surface them, never hide them.
3. **Pillars over composite** — report which pillar is weak, not just the number. Pillar definitions, thresholds, and market-dependency: `docs/scoring-overview.md` and `articles/01-estimation-methodology.md`.

## Compose with CPD for a complete report

For a full "is this a good, *trustworthy* token?" answer, run this skill for the on-chain numbers, then the `cpd-crypto-analysis` skill for the sourced trust layer, and merge. The CLI `risk` and the CPD score share one source of truth (`docs/risk_revenue_estimation.md`); where they diverge, that divergence is itself a finding worth reporting.

## Reference

- Command surface + flags + output schema: `docs/cli-reference.md`
- Scoring systems (score / risk / signal / fairlaunch): `docs/scoring-overview.md`
- End-to-end walkthrough: `docs/walkthrough.md`
- Skill routing rationale: `docs/skills-architecture.md`
