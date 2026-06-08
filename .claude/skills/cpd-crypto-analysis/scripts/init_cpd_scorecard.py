#!/usr/bin/env python3
"""Initialize a CPD scorecard JSON file."""

import argparse
import json
from datetime import date
from pathlib import Path


SCORECARD_TEMPLATE = {
    "project": "",
    "analysis_date": "",
    "scope": {
        "chain": "",
        "protocol_version": "",
        "dapp": "",
    },
    "scores": {
        "cpd": {
            "chain_score": None,
            "protocol_score": None,
            "dapp_score": None,
        },
        "stage_score": None,
        "age_score": None,
        "code_score": None,
        "incidents_score": None,
    },
    "critical_facts": {
        "network_status": {
            "value": "",
            "as_of": "",
            "source_url": "",
            "source_type": "official",
            "note": "",
        },
        "release_scope": {
            "value": "",
            "as_of": "",
            "source_url": "",
            "source_type": "official",
            "note": "",
        },
    },
    "adp": {
        "enabled": False,
        "yes_count": None,
    },
    "evidence": [],
    "notes": "",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create CPD scorecard JSON template")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--out", required=True, help="Output JSON file")
    parser.add_argument("--enable-adp", action="store_true", help="Enable ADP fields")
    args = parser.parse_args()

    payload = dict(SCORECARD_TEMPLATE)
    payload["project"] = args.project
    payload["analysis_date"] = date.today().isoformat()
    payload["adp"] = {
        "enabled": args.enable_adp,
        "yes_count": None if args.enable_adp else 0,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote scorecard template: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
