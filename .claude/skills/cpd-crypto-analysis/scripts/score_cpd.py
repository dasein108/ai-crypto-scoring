#!/usr/bin/env python3
"""Validate and score CPD scorecard JSON."""

import argparse
import json
from datetime import date, datetime
from pathlib import Path


RANGES = {
    "chain_score": (1, 3),
    "protocol_score": (0, 2),
    "dapp_score": (-1, 1),
    "stage_score": (-1, 3),
    "age_score": (-1, 3),
    "code_score": (-1, 3),
    "incidents_score": (-1, 3),
}


def tier_from_percent(percent: float) -> str:
    if percent >= 75:
        return "Tier-1"
    if percent >= 50:
        return "Tier-2"
    if percent >= 25:
        return "Tier-3"
    return "Below Tier-3"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Failed to read scorecard JSON: {exc}")


def require_number(name: str, value):
    if value is None or not isinstance(value, (int, float)):
        raise SystemExit(f"Missing or invalid numeric value for '{name}'")


def check_range(name: str, value):
    lo, hi = RANGES[name]
    if value < lo or value > hi:
        raise SystemExit(f"Value out of range for '{name}': {value} (allowed: {lo}..{hi})")


def parse_iso_date(name: str, value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        raise SystemExit(f"Invalid date for '{name}': expected YYYY-MM-DD")


def validate_critical_facts(
    payload: dict,
    analysis_date: date,
    max_critical_age_days: int,
    allow_unknown_network_status: bool,
):
    critical = payload.get("critical_facts", {})
    network_status = critical.get("network_status", {})
    value = str(network_status.get("value", "")).strip().lower()
    as_of = str(network_status.get("as_of", "")).strip()
    source_url = str(network_status.get("source_url", "")).strip()

    allowed_values = {"mainnet", "testnet", "devnet", "unknown"}
    if value not in allowed_values:
        raise SystemExit(
            "Missing/invalid critical fact 'critical_facts.network_status.value' "
            "(allowed: mainnet, testnet, devnet, unknown)"
        )
    if value == "unknown" and not allow_unknown_network_status:
        raise SystemExit(
            "Critical fact 'network_status' cannot be 'unknown' without "
            "--allow-unknown-network-status"
        )
    if not as_of:
        raise SystemExit("Missing critical fact date: 'critical_facts.network_status.as_of'")
    if not source_url:
        raise SystemExit("Missing critical fact source: 'critical_facts.network_status.source_url'")

    fact_date = parse_iso_date("critical_facts.network_status.as_of", as_of)
    age_days = (analysis_date - fact_date).days
    if age_days < 0:
        raise SystemExit(
            "Invalid critical fact date: 'critical_facts.network_status.as_of' is in the future"
        )
    if age_days > max_critical_age_days:
        raise SystemExit(
            f"Critical fact 'network_status' is stale ({age_days} days old; "
            f"max allowed: {max_critical_age_days})"
        )


def build_output(
    payload: dict,
    max_critical_age_days: int,
    allow_unknown_network_status: bool,
) -> dict:
    analysis_date_raw = payload.get("analysis_date", "")
    if str(analysis_date_raw).strip():
        analysis_date = parse_iso_date("analysis_date", analysis_date_raw)
    else:
        analysis_date = date.today()

    validate_critical_facts(
        payload,
        analysis_date=analysis_date,
        max_critical_age_days=max_critical_age_days,
        allow_unknown_network_status=allow_unknown_network_status,
    )

    scores = payload.get("scores", {})
    cpd = scores.get("cpd", {})

    chain = cpd.get("chain_score")
    protocol = cpd.get("protocol_score")
    dapp = cpd.get("dapp_score")
    stage = scores.get("stage_score")
    age = scores.get("age_score")
    code = scores.get("code_score")
    incidents = scores.get("incidents_score")

    values = {
        "chain_score": chain,
        "protocol_score": protocol,
        "dapp_score": dapp,
        "stage_score": stage,
        "age_score": age,
        "code_score": code,
        "incidents_score": incidents,
    }

    for k, v in values.items():
        require_number(k, v)
        check_range(k, v)

    block1 = chain + protocol + dapp
    block2 = stage
    block3 = age
    block4 = code
    block5 = incidents

    total = block1 + block2 + block3 + block4 + block5
    percent = (total / 18.0) * 100.0
    tier = tier_from_percent(percent)

    adp = payload.get("adp", {})
    adp_enabled = bool(adp.get("enabled", False))
    adp_score = None
    if adp_enabled:
        yes_count = adp.get("yes_count")
        require_number("adp.yes_count", yes_count)
        if yes_count < 0 or yes_count > 10:
            raise SystemExit("Value out of range for 'adp.yes_count': allowed 0..10")
        adp_score = (yes_count / 10.0) * 100.0

    return {
        "project": payload.get("project", ""),
        "analysis_date": analysis_date.isoformat(),
        "totals": {
            "block1_cpd": block1,
            "block2_stage": block2,
            "block3_age": block3,
            "block4_code": block4,
            "block5_incidents": block5,
            "total": total,
            "max": 18,
            "percent": round(percent, 2),
            "tier": tier,
        },
        "adp": {
            "enabled": adp_enabled,
            "score": None if adp_score is None else round(adp_score, 2),
        },
    }


def to_markdown(result: dict) -> str:
    totals = result["totals"]
    lines = [
        f"# CPD Score Summary - {result.get('project') or 'Unknown Project'}",
        "",
        f"- Analysis date: {result.get('analysis_date') or 'N/A'}",
        f"- Block 1 (CPD): {totals['block1_cpd']} / 6",
        f"- Block 2 (Stage): {totals['block2_stage']} / 3",
        f"- Block 3 (Age): {totals['block3_age']} / 3",
        f"- Block 4 (Code): {totals['block4_code']} / 3",
        f"- Block 5 (Incidents): {totals['block5_incidents']} / 3",
        f"- Total: {totals['total']} / {totals['max']}",
        f"- Percent: {totals['percent']}%",
        f"- Tier: {totals['tier']}",
    ]

    adp = result.get("adp", {})
    if adp.get("enabled"):
        lines.append(f"- ADP score: {adp.get('score')}%")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Score CPD scorecard JSON")
    parser.add_argument("--scorecard", required=True, help="Path to scorecard JSON")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format",
    )
    parser.add_argument("--out", help="Output file path (optional)")
    parser.add_argument(
        "--max-critical-age-days",
        type=int,
        default=45,
        help="Maximum allowed age for critical project-state facts",
    )
    parser.add_argument(
        "--allow-unknown-network-status",
        action="store_true",
        help="Allow scoring even when network status is unknown",
    )
    args = parser.parse_args()

    payload = read_json(Path(args.scorecard))
    result = build_output(
        payload,
        max_critical_age_days=args.max_critical_age_days,
        allow_unknown_network_status=args.allow_unknown_network_status,
    )

    if args.format == "json":
        rendered = json.dumps(result, indent=2) + "\n"
    else:
        rendered = to_markdown(result)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote report: {out_path}")
    else:
        print(rendered, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
