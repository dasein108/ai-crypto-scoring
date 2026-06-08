# CPD Deep Research Prompt

You are a Web3 security analyst. Run deep research for project **{{PROJECT_NAME}}** using the CPD framework (Chain, Protocol, Dapp), and score all blocks below using verifiable sources only.

Before scoring, confirm the project's current network state (mainnet/testnet/devnet) using the latest official announcement/docs and at least one independent source. If sources conflict, prefer the newest official source and document the conflict.

## Block 1: CPD Structure (max 6)

1. Chain
- Identify the exact chain.
- Score: Tier-1=3, Tier-2=2, Tier-3=1.
- Justify with TVL, chain age, validator/decentralization profile, consensus/finality, and major chain-level incident history.

2. Protocol (exact version)
- Identify protocol and version.
- Score: Tier-1=2, Tier-2=1, Tier-3=0.
- Justify with version-specific TVL, audits, security history, and uptime/longevity.

3. Dapp implementation
- Identify exact UI/implementation (official/fork/third-party).
- Score: Tier-1=1, Tier-2=0, Tier-3=-1.
- Justify with ownership, launch date, and whether this implementation has separate audits.

## Block 2: Development Stage (max 3)

Stage scoring:
- MVP=-1
- Alpha=0
- Beta=1
- Release=2
- Release+Audit=3

Verify official release status, audit firms and dates, links to reports, and bounty program details.

## Block 3: Age (max 3)

Use first on-chain deployment/transaction date for the specific version/implementation.
- 0-1y=-1
- 1-3y=0
- 3-4y=1
- 4-5y=2
- >5y=3

Also capture first repo commit date and earliest Wayback snapshot for the project site.

## Block 4: Code (max 3)

- Proprietary new=-1
- Proprietary old=0
- Open source new=1
- Open source old=2
- Open source old + audit=3

Verify repo links, license, contributor/activity profile, contract verification status, and audit list with dates.

## Block 5: Incidents (max 3)

- 2+ verified hacks=-1
- 1-2 verified hacks=0
- 1 hack but remediated/compensated=1
- 0 hacks=2
- 0 hacks across protocol versions=3

Collect all verified incidents and post-mortems, plus remediation outcomes.

## Final Computation

Total max = 18.
Percent = (total / 18) * 100.
Final tier:
- >=75: Tier-1
- 50-75: Tier-2
- 25-50: Tier-3
- <25: below Tier-3

{{ADP_SECTION}}

## Output Format

For each criterion provide:
1. Score with rationale
2. Source(s) and checked date
3. Critical flags if any

End with a 3-5 sentence executive summary: final tier, main risks, recommendation.
