# token-research

An **EVM-first crypto token research CLI**. Give it a ticker, project name, or token address; it resolves a canonical identity, runs domain-specific collectors against a provider registry, and emits structured **JSON** (or Markdown for `deep-report`) with full provenance, warnings, and explicit coverage gaps.

It runs **fully free with zero API keys**. An RPC URL and optional provider keys only widen coverage.

There are **two ways to use it**:

1. **[Through an AI agent](#use-it-with-an-llm-the-skills) (Claude Code / Claude.ai)** — natural-language requests routed by two bundled skills. Best for "research this token", "is it safe?", "compare these three".
2. **[Directly on the CLI](#use-it-on-the-cli)** — deterministic commands you script yourself. Best for pipelines, watchlists, and reproducible output.

Both drive the *same engine*; the skills are a thin routing/reading layer on top of the CLI.

---

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]

# Resolve + full dossier (Markdown is deep-report only)
token-research deep-report MORPHO --chain ethereum \
  --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2 --format markdown
```

Run without installing:

```bash
PYTHONPATH=src python3 -m token_research deep-report ETH --format markdown
```

Offline (no network, deterministic — good for smoke checks):

```bash
TOKEN_RESEARCH_OFFLINE=1 token-research resolve ETH
```

Requires Python ≥ 3.11. Only runtime dependency is `python-dotenv`. The CEX price-history / momentum / portfolio features need the opt-in `[ccxt]` extra (`pip install -e ".[ccxt]"`).

---

## Use it with an LLM (the skills)

The repo ships **two Claude Code skills** in [`.claude/skills/`](.claude/skills/). They turn a plain-language request into the right command(s) and read the structured output back. Open this folder in Claude Code (or any agent that loads `.claude/skills/`) and just ask — the agent picks the skill.

| Skill | Engine | What it covers | When it fires |
|---|---|---|---|
| **[`token-research`](.claude/skills/token-research/SKILL.md)** | the CLI (on-chain truth) | EVM token risk, 6-pillar score, holders, liquidity, locks/unlocks, full dossiers, compare | EVM token with a known/derivable address (Ethereum / Base / Arbitrum / Optimism) |
| **[`cpd-crypto-analysis`](.claude/skills/cpd-crypto-analysis/SKILL.md)** | web research (sourced claims) | evidence-first CPD trust/due-diligence scoring | non-EVM L1 (HBAR, SOL, ATOM…), pre-TGE programs, purely qualitative trust questions |

### How routing works

The skills split the decision on **one axis — the data regime** (not the question):

```
Is it an EVM token with a known/derivable address?
 ├─ yes → token-research skill   (canonical on-chain numbers)
 └─ no  → cpd-crypto-analysis    (sourced trust narrative)
```

The CLI's documented refusals (non-EVM, pre-TGE) are the **hand-off signal** to the web engine, not a failure. A complete report often uses **both**: the CLI for the numbers, CPD for the sourced trust layer — they share one source of truth ([`docs/risk_revenue_estimation.md`](docs/risk_revenue_estimation.md)), so where they diverge, that divergence is itself a finding.

### What to ask

| You say… | Skill + mode | Runs |
|---|---|---|
| "Is MORPHO worth a look?" | `token-research` · quick | `score` + `risk`, reads the headline pillars |
| "Full dossier on ONDO" | `token-research` · deep | `deep-report` (all collectors + narrative) |
| "Compare PENDLE vs MORPHO vs ONDO" | `token-research` · compare | `score`/`compare` per token, diffed in a table |
| "Is HBAR / this pre-TGE program trustworthy?" | `cpd-crypto-analysis` | evidence-first CPD scorecard |

Full rationale and the routing contract: **[docs/skills-architecture.md](docs/skills-architecture.md)**.

---

## Use it on the CLI

```
token-research <command> <query> [options]
```

`<query>` is a ticker (`ETH`), project name (`Ondo`), or token address (`0x…`). Always pass `--address` and `--chain` when you know them — it pins the exact contract and skips resolver guesswork (squatter-token defense). Default output is JSON; `deep-report` also takes `--format markdown`.

### Commands at a glance

**Identity & market**
`resolve` · `identity` · `price` · `pools` · `liquidity` · `supply` · `holders`

**Flows & entities**
`flows` (Dune-keyed) · `labels` · `relations`

**Locks & supply pressure**
`locks` · `staking` · `unlocks` (`--with-premium` for Tokenomist)

**Fundamentals & scoring**
`yields` · `compare` · `risk` · `prelaunch` · `score` (6-pillar) · `fairlaunch` · `deep-report`

**Trader layer**
`trend` · `signal` · `screen` · `chain-report`

**Portfolio research (paper-mode, opt-in `[ccxt]`)**
`momentum-screen` · `momentum-rank` · `momentum-backtest` · `price-history` · `portfolio-build` · `portfolio-rebalance`

➡ **Authoritative surface — every command, flag, and output field: [docs/cli-reference.md](docs/cli-reference.md).**

### The four scoring systems

They answer different questions — don't read one as another. See [docs/scoring-overview.md](docs/scoring-overview.md).

| Command | Question |
|---|---|
| `score` | Structural quality (6-pillar composite, 0–100) |
| `risk` | Trust / rug risk (CPD 2.0 tier + %) |
| `signal` | Entry / exit timing (composite −10…+10) |
| `fairlaunch` | Launch fairness (15-point rubric) |

### Output contract

Every command returns the same shape: `metrics`, `sources`, `warnings`, `coverage_gaps`, `resolved_identity`, `generated_at`. Three rules when reading it: **trace don't trust** (every metric carries its source — RPC > explorer > vendor), **coverage gaps are signal** (they penalize the composite — surface them), **pillars over composite** (report the weak pillar, not just the number).

---

## Worked examples

Two real dossiers, kept as reference specimens in [`examples/`](examples/):

- [examples/morpho-deep-analysis.md](examples/morpho-deep-analysis.md) — mature DeFi lending token (the run that drove 7 scoring-model fixes)
- [examples/ondo-analysis.md](examples/ondo-analysis.md) — RWA token (Treasuries + Global Markets)

Older one-off write-ups and the pre-TGE / non-EVM case studies are kept under [`archived/`](archived/).

---

## Documentation

- **[docs/getting-started.md](docs/getting-started.md)** — install, `.env`, which keys actually matter, first run.
- **[docs/walkthrough.md](docs/walkthrough.md)** — research a token end-to-end and read the output.
- **[docs/scoring-overview.md](docs/scoring-overview.md)** — the four scoring systems and when to use each.
- **[docs/skills-architecture.md](docs/skills-architecture.md)** — the two engines, skill modes, and the routing rule.
- **[docs/cli-reference.md](docs/cli-reference.md)** — the authoritative command surface.
- **[docs/README.md](docs/README.md)** — full documentation index.

---

## Repository layout

```
src/token_research/   the CLI engine (providers, commands, scoring)
.claude/skills/       the two LLM skills (token-research + cpd-crypto-analysis)
docs/                 maintained knowledge base (see docs/README.md)
examples/             two canonical worked dossiers
archived/             produced artifacts + regenerable runtime data, kept for reference
tests/                pytest suite (offline by default)
```

The CLI writes its runtime data (raw API caches, generated reports, trend snapshots, price cache) to `.token-research/` by default — gitignored and regenerated on each run. Override with `TOKEN_RESEARCH_DATA_DIR`.
