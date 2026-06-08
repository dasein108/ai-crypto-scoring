# CPD Source Playbook

Use this source mapping during data collection.

## Canonical Sources by Criterion

- Project state (mainnet/testnet/devnet) and latest launch announcements
  - Official blog/announcements/docs, plus one independent confirmation source
- Chain health and TVL
  - DefiLlama, L2Beat, official chain docs
- Protocol version TVL and presence
  - DefiLlama protocol pages, official protocol docs/repos
- Dapp implementation provenance
  - Official site/docs, repo ownership, deployment metadata
- Audits and security reviews
  - Auditor report pages (Trail of Bits, OpenZeppelin, ChainSecurity, CertiK), protocol docs
- Incident verification
  - rekt.news, Immunefi, DefiLlama hacks, official post-mortems
- On-chain deployment date
  - Block explorer verified contracts and deployment txn timestamps
- Code maturity
  - GitHub/GitLab commit history, license, contributor count/activity
- Historical presence
  - Wayback Machine snapshots

## Evidence Logging Rules

For every claim, log:
- `claim`
- `value`
- `source_name`
- `url`
- `checked_at` (YYYY-MM-DD)
- `note` (one short sentence)

If evidence conflicts, record both and pick the score using the more conservative interpretation.

## Minimum Evidence Threshold

Before finalizing a report, ensure:
- `critical_facts.network_status` is filled with date and source URL from a source checked recently.
- Every scored criterion has at least one source.
- Critical criteria (audits, incidents, deployment date) have two independent sources when possible.
- Unknown data is explicitly marked as not found and scored conservatively.

## Contradiction Rule

If sources conflict on project state (e.g., testnet vs mainnet), compare dates and prefer the newest official source. Add a risk flag documenting the conflict.
