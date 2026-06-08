# token-research — Documentation

Knowledge base for the `token-research` CLI. Docs are tagged by status so you know what's normative:

- **Live** — describes the shipped tool; authoritative.
- **Design** — design memo for a planned/forward feature.
- **Historical** — point-in-time plan, now shipped; kept for provenance.
- **Example** — a worked analysis output, not maintained docs.

## Start here

| Doc | Status | What it is |
|---|---|---|
| [getting-started.md](getting-started.md) | Live | Install, `.env`, which keys matter, first run |
| [walkthrough.md](walkthrough.md) | Live | Research a token end-to-end and read the output |
| [scoring-overview.md](scoring-overview.md) | Live | The four scoring systems and when to use each |
| [cli-reference.md](cli-reference.md) | Live | Authoritative command surface, flags, output schema |

## Methods & reference

| Doc | Status | What it is |
|---|---|---|
| [risk_revenue_estimation.md](risk_revenue_estimation.md) | Live | CPD 2.0 protocol behind `risk` / `prelaunch` |
| [trend_signals.md](trend_signals.md) | Live | Trader signal framework behind `signal` / `trend` |
| [apis/providers.md](apis/providers.md) | Live | All data providers, status tied to code, endpoints |
| [domains/](domains/) | Design→Live | Per-domain method memos (holders, liquidity, locks, relations, fair-value, literature) |
| [architecture/](architecture/) | Live | CLI architecture, evidence graph, project adapters |
| [skills-architecture.md](skills-architecture.md) | Live | The two research engines, skill modes/purposes, routing rule |

## Worked examples

Two canonical specimens live in [`../examples/`](../examples/) (real reports, not tutorials):

| Doc | Token case |
|---|---|
| [../examples/morpho-deep-analysis.md](../examples/morpho-deep-analysis.md) | Mature DeFi lending token |
| [../examples/ondo-analysis.md](../examples/ondo-analysis.md) | RWA token |

The rest are archived under [`../archived/docs/`](../archived/docs/):

| Doc | Token case |
|---|---|
| [../archived/docs/katana_analysis.md](../archived/docs/katana_analysis.md) | Pre-TGE program (`prelaunch`) |
| [../archived/docs/hbar_analysis.md](../archived/docs/hbar_analysis.md) | Non-EVM L1 limits |
| [../archived/docs/analyses/](../archived/docs/analyses/) | Older one-off signal/CPD write-ups (dated) |

## Strategy playbooks (Live)

Operator-facing — theory + runbook + risks for an actual book run.

- [strategies/bullish-hedged-book.md](strategies/bullish-hedged-book.md) — self-funded long/short book (gross 100%, net β = 0.4) on Bybit USDT perps
- [strategies/long-only-book.md](strategies/long-only-book.md) — unhedged sibling: same engine, no BTC short
- [strategies/portfolio-universe-curation.md](strategies/portfolio-universe-curation.md) — rationale + audit for the curated 16-name universe

## Task / design docs

| Doc | Status |
|---|---|
| [tasks/00-roadmap.md](tasks/00-roadmap.md) | Historical (phases shipped) |
| [tasks/01-command-surface.md](tasks/01-command-surface.md) | Historical (shipped → see cli-reference) |
| [tasks/02-deep-report-pipeline.md](tasks/02-deep-report-pipeline.md) | Historical (shipped) |
| [tasks/03-momentum-portfolio.md](tasks/03-momentum-portfolio.md) | Design (shipped, design record) |
| [tasks/04-dex-cex-arb.md](tasks/04-dex-cex-arb.md) | Design (planned — no arb commands yet) |
| [tasks/05-ccxt-price-history.md](tasks/05-ccxt-price-history.md) | Design (shipped, design record) |
| [tasks/06-bullish-hedged-portfolio.md](tasks/06-bullish-hedged-portfolio.md) | Design (shipped, design record) |
| [tasks/07-systematic-universe-filter.md](tasks/07-systematic-universe-filter.md) | Design (planned — universe still hand-curated) |

## Provenance

- [raw/deep-research-project-analysis.md](raw/deep-research-project-analysis.md) — the founding deep-research memo this knowledge base grew from. Raw, unmaintained.
- `Revenue_vs_Risk_(CPD 2.0).xlsx` — source spreadsheet for the CPD model.

## Design rules (still normative)

- Canonical numeric truth comes from on-chain computation first (RPC > explorer > vendor).
- Curated schedules and investor categories are **claims** until validated by on-chain flows.
- Every output metric keeps provenance: source, endpoint/query ID, timestamp, confidence.
- Entity attribution is probabilistic — store evidence and confidence, never hard-coded certainty.
- EVM-first; non-EVM chains only through explicit adapters.
