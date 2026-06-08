---
name: cpd-crypto-analysis
description: Perform deep due diligence and risk scoring for any crypto project using the CPD framework (Chain, Protocol, Dapp), sometimes referred to as CDP in user requests, plus stage, age, code quality, and incident history. Use when the user asks to evaluate crypto project reliability, compare projects by security/risk posture, produce an evidence-backed CPD report, or calculate CPD percentage/tier from sourced findings.
allowed-tools: Bash, Read, Write, WebFetch, WebSearch
---

# CPD Crypto Analysis

## Overview

Run evidence-first CPD analysis. Produce reproducible scorecard. Use bundled scripts to generate research prompt, scaffold report, compute scores deterministically.

All paths below are project-root-relative. Skill assets live at `.claude/skills/cpd-crypto-analysis/`.

## Workflow

1. Collect target metadata.
2. Run project-state freshness gate (mainnet/testnet/devnet).
3. Generate project-specific research brief.
4. Gather verifiable evidence from primary or canonical sources.
5. Score each CPD block from evidence only.
6. Compute totals. Produce final recommendation.

## Step 1: Collect Target Metadata

Capture before researching:
- Project name and ticker
- Chain and deployment environment (mainnet/testnet)
- Exact protocol/version under review
- Exact dapp/interface under review (official UI, fork, aggregator route)

If user does not specify version or interface, mark incomplete. State assumptions in report.

## Step 2: Project-State Freshness Gate (Mandatory)

Verify current network state from at least:
- one official source (project docs/blog/announcements),
- one independent source (major media, explorer, analytics platform).

Use WebFetch / WebSearch tools. Capture in scorecard:
- `critical_facts.network_status.value` — `mainnet` | `testnet` | `devnet` | `unknown`
- `critical_facts.network_status.as_of` — YYYY-MM-DD
- `critical_facts.network_status.source_url`

Do not score until filled. If sources disagree, use more recent. Flag risk.

## Step 3: Generate Research Brief

Generate reusable deep-research prompt:

```bash
python3 .claude/skills/cpd-crypto-analysis/scripts/build_cpd_prompt.py \
  --project "Aave" > /tmp/aave_cpd_prompt.md
```

Stablecoin mode (adds ADP anti-depeg block):

```bash
python3 .claude/skills/cpd-crypto-analysis/scripts/build_cpd_prompt.py \
  --project "USDC" --include-adp > /tmp/usdc_cpd_prompt.md
```

Script loads template at `.claude/skills/cpd-crypto-analysis/references/cpd_prompt_template.md`.

## Step 4: Collect Evidence

Read `.claude/skills/cpd-crypto-analysis/references/source_playbook.md` to map each criterion to canonical sources. Record per finding:
- Source name
- URL
- Access/check date
- One-line evidence note

Prioritize direct sources (official docs, explorers, audit reports, repos). Cross-check secondary sources against ≥1 primary.

## Step 5: Fill the Scorecard

Initialize structured scorecard JSON:

```bash
python3 .claude/skills/cpd-crypto-analysis/scripts/init_cpd_scorecard.py \
  --project "Aave" --out /tmp/aave_scorecard.json
```

Stablecoin variant:

```bash
python3 .claude/skills/cpd-crypto-analysis/scripts/init_cpd_scorecard.py \
  --project "USDC" --out /tmp/usdc_scorecard.json --enable-adp
```

Edit JSON with evidence-backed values. Allowed ranges:
- `cpd.chain_score`: 1..3
- `cpd.protocol_score`: 0..2
- `cpd.dapp_score`: -1..1
- `stage_score`: -1..3
- `age_score`: -1..3
- `code_score`: -1..3
- `incidents_score`: -1..3

## Step 6: Compute Final Score and Tier

```bash
python3 .claude/skills/cpd-crypto-analysis/scripts/score_cpd.py \
  --scorecard /tmp/aave_scorecard.json \
  --format markdown \
  --out /tmp/aave_report.md
```

Scorer enforces critical-fact freshness. Default: `network_status` must be present and ≤45 days from `analysis_date`.

Output:
- Block subtotals
- Total out of 18
- Percentage
- Final tier classification
- Optional ADP score when present

## Reporting Standard

Per criterion include:
1. Assigned score with concise rationale
2. Evidence references (URL + checked date)
3. Critical risk flags when applicable

Conclude with:
- Final CPD percentage and tier
- Top risk drivers
- Recommendation: proceed / proceed with caution / avoid

Final report layout: `.claude/skills/cpd-crypto-analysis/assets/cpd_report_template.md`.

## Resources

- `references/cpd_prompt_template.md` — base CPD prompt template
- `references/source_playbook.md` — source map and evidence checklist
- `assets/cpd_report_template.md` — final report layout
- `scripts/build_cpd_prompt.py` — prompt generator
- `scripts/init_cpd_scorecard.py` — scorecard initializer
- `scripts/score_cpd.py` — score calculator and report generator
