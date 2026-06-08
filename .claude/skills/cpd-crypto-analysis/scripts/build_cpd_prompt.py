#!/usr/bin/env python3
"""Generate a project-specific CPD deep research prompt."""

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CPD prompt for a project")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument(
        "--template",
        default=str(Path(__file__).resolve().parents[1] / "references" / "cpd_prompt_template.md"),
        help="Template path",
    )
    parser.add_argument(
        "--include-adp",
        action="store_true",
        help="Include ADP anti-depeg section (for stablecoins)",
    )
    args = parser.parse_args()

    template_path = Path(args.template)
    if not template_path.exists():
        raise SystemExit(f"Template not found: {template_path}")

    adp_section = ""
    if args.include_adp:
        adp_section = """
## Additional Block: ADP (Anti-Depeg, 0-100%)

If the target includes a stablecoin, answer Yes/No for:
1. No historical depeg event?
2. Non-custodial design?
3. No native-token collateral dependency?
4. No rehypothecation of collateral?
5. Tier-1 chain?
6. Older than 3 years?
7. No major negative security/regulatory news in last 3-12 months?
8. Market cap not materially declining in last 1-3 months?
9. Proof-of-reserves available?
10. No other significant depeg risk known?

ADP score = (Yes count / 10) * 100.
""".strip("\n")

    template = template_path.read_text(encoding="utf-8")
    output = template.replace("{{PROJECT_NAME}}", args.project)
    output = output.replace("{{ADP_SECTION}}", adp_section)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
