from __future__ import annotations

from dataclasses import asdict
from typing import Any

from token_research import contract_registry
from token_research.commands import flows, holders, labels, locks, staking
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, dataclass_list
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _index_labels(label_rows: list[Any]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for row in label_rows:
        if not isinstance(row, dict):
            continue
        addr = str(row.get("address") or "").lower()
        if addr:
            index[addr] = row
    return index


def _classify_entity(
    address: str,
    balance: float | None,
    *,
    vesting_by_addr: dict[str, dict],
    timelock_by_addr: dict[str, dict],
    staking_by_addr: dict[str, dict],
    label_by_addr: dict[str, dict],
) -> dict:
    """Build an entity record for a single holder address."""
    lower = address.lower()
    entity = {
        "address": address,
        "balance": balance,
        "type": "wallet",
        "label": None,
        "confidence": 0.5,
        "evidence": [],
    }

    if contract_registry.is_burn_address(address):
        entity.update(
            {
                "type": "burn",
                "label": "burn_address",
                "confidence": 1.0,
                "evidence": [{"source": "contract_registry", "claim": "burn address"}],
            }
        )
        return entity

    if lower in vesting_by_addr:
        match = vesting_by_addr[lower]
        entity.update(
            {
                "type": "vesting",
                "label": match.get("type") or "vesting_wallet",
                "confidence": 0.9,
                "evidence": [
                    {
                        "source": "rpc",
                        "claim": f"on-chain {match.get('type') or 'vesting'} inspection",
                        "details": {
                            "start": match.get("start"),
                            "duration": match.get("duration"),
                            "beneficiary": match.get("beneficiary"),
                            "release_time": match.get("release_time"),
                        },
                    }
                ],
            }
        )
        return entity

    if lower in staking_by_addr:
        match = staking_by_addr[lower]
        entity.update(
            {
                "type": "staking",
                "label": match.get("type") or "staking_contract",
                "confidence": 0.9,
                "evidence": [
                    {
                        "source": "rpc",
                        "claim": f"{match.get('type') or 'staking'} inspection",
                        "details": {
                            "total_assets": match.get("total_assets"),
                            "total_share_supply": match.get("total_share_supply"),
                        },
                    }
                ],
            }
        )
        return entity

    if lower in timelock_by_addr:
        match = timelock_by_addr[lower]
        label = match.get("label")
        entity_type = "locker" if match.get("type") == "registered_locker" else "contract"
        entity.update(
            {
                "type": entity_type,
                "label": label or "contract_holder",
                "confidence": 0.7 if entity_type == "locker" else 0.55,
                "evidence": [
                    {
                        "source": "rpc+registry" if entity_type == "locker" else "rpc",
                        "claim": "registered locker contract" if entity_type == "locker" else "unlabeled contract holder",
                    }
                ],
            }
        )

    if lower in label_by_addr:
        label_row = label_by_addr[lower]
        label_text = str(
            label_row.get("label")
            or label_row.get("name")
            or label_row.get("tag")
            or ""
        )
        label_type = str(label_row.get("type") or label_row.get("category") or "labeled").lower()
        if entity["type"] == "wallet":
            entity["type"] = label_type or "labeled"
        entity["label"] = label_text or entity["label"]
        entity["confidence"] = max(float(entity["confidence"]), 0.7)
        entity["evidence"].append({"source": "dune_labels", "claim": label_text or "labeled"})

    return entity


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "entities": [],
        "clusters": [],
        "relations": [],
        "confidence_policy": "evidence_first",
    }

    if not identity.token_address:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.RELATIONS,
                reason="No token address available.",
                suggested_source="Resolve token address first.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    # Gather source data. Each dependency self-handles offline / missing RPC /
    # missing explorer cases; we simply degrade when their metrics are empty.
    holders_result = holders.run(query, chain, address, config, include_premium).to_dict()
    labels_result = labels.run(query, chain, address, config, include_premium).to_dict()
    locks_result = locks.run(query, chain, address, config, include_premium).to_dict()
    staking_result = staking.run(query, chain, address, config, include_premium).to_dict()
    flows_result = flows.run(query, chain, address, config, include_premium).to_dict()

    top_holders_list = (holders_result.get("metrics") or {}).get("top_holders") or []
    label_rows = (labels_result.get("metrics") or {}).get("labels") or []
    vesting_contracts = (locks_result.get("metrics") or {}).get("vesting_contracts") or []
    timelock_balances = (locks_result.get("metrics") or {}).get("timelock_balances") or []
    staking_contracts = (staking_result.get("metrics") or {}).get("staking_contracts") or []

    vesting_by_addr = {
        str(v.get("contract") or "").lower(): v for v in vesting_contracts if isinstance(v, dict)
    }
    timelock_by_addr = {
        str(t.get("address") or "").lower(): t for t in timelock_balances if isinstance(t, dict)
    }
    staking_by_addr = {
        str(s.get("contract") or "").lower(): s for s in staking_contracts if isinstance(s, dict)
    }
    label_by_addr = _index_labels(label_rows)

    if not top_holders_list:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.ENTITIES,
                reason="No top holders available to seed the relations graph.",
                suggested_source="Run holders command with a supported chain explorer.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    # Compute share of supply when a total is derivable (sum of top-holder values
    # as denominator is conservative; true total_supply is only used if holders
    # already computed it).
    total_balance = 0.0
    for h in top_holders_list:
        v = _safe_float(h.get("value"))
        if v:
            total_balance += v

    entities: list[dict] = []
    for holder in top_holders_list[:25]:
        addr = str(holder.get("address") or "")
        if not addr:
            continue
        balance = _safe_float(holder.get("value"))
        entity = _classify_entity(
            addr,
            balance,
            vesting_by_addr=vesting_by_addr,
            timelock_by_addr=timelock_by_addr,
            staking_by_addr=staking_by_addr,
            label_by_addr=label_by_addr,
        )
        if balance is not None and total_balance > 0:
            entity["top_holder_share"] = round(balance / total_balance, 4)
        entities.append(entity)

    # Cluster by entity type.
    clusters: dict[str, dict] = {}
    for entity in entities:
        group = str(entity["type"])
        if group not in clusters:
            clusters[group] = {
                "name": group,
                "members": [],
                "total_balance": 0.0,
                "evidence_count": 0,
            }
        clusters[group]["members"].append(entity["address"])
        if entity["balance"]:
            clusters[group]["total_balance"] += float(entity["balance"])
        clusters[group]["evidence_count"] += len(entity["evidence"])
    for cluster in clusters.values():
        cluster["total_balance"] = round(cluster["total_balance"], 6)
        if total_balance > 0:
            cluster["share_of_top_holders"] = round(cluster["total_balance"] / total_balance, 4)

    # Relations: token ← holder edges, plus co-cluster edges for non-wallet groups.
    token_addr = identity.token_address
    relations: list[dict] = []
    for entity in entities:
        relations.append(
            {
                "from": entity["address"],
                "to": token_addr,
                "type": f"holds_as_{entity['type']}",
                "confidence": entity["confidence"],
                "evidence": [ev.get("claim") for ev in entity["evidence"]],
            }
        )
    for cluster_name, cluster in clusters.items():
        if cluster_name in {"wallet", "burn"}:
            continue
        members = cluster["members"]
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                relations.append(
                    {
                        "from": members[i],
                        "to": members[j],
                        "type": f"co_{cluster_name}",
                        "confidence": 0.5,
                        "evidence": [f"both classified as {cluster_name}"],
                    }
                )

    # Flow-derived edges: for each category in the flows netflows, add a
    # directional edge from the token to a synthetic category node tagged with
    # net movement and evidence sourced to the flows command. This lets the
    # graph describe "where value is moving" in addition to "who holds it".
    netflows = (flows_result.get("metrics") or {}).get("netflows") or []
    for row in netflows:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "unknown")
        if category == "unknown":
            continue
        net_raw = row.get("net_raw")
        try:
            net_val = float(net_raw) if net_raw is not None else 0.0
        except (TypeError, ValueError):
            net_val = 0.0
        if net_val == 0:
            continue
        direction = "net_inflow_from" if net_val > 0 else "net_outflow_to"
        synthetic_node = f"cluster:{category}"
        relations.append(
            {
                "from": token_addr if net_val < 0 else synthetic_node,
                "to": synthetic_node if net_val < 0 else token_addr,
                "type": f"{direction}_{category}",
                "confidence": 0.7,
                "evidence": [
                    f"flows: net {category} net_raw={net_val:.0f} tx_count={row.get('tx_count')}"
                ],
            }
        )

    metrics["entities"] = entities
    metrics["clusters"] = sorted(
        clusters.values(),
        key=lambda c: c.get("total_balance", 0.0),
        reverse=True,
    )
    metrics["relations"] = relations

    # Propagate any upstream coverage gaps so they surface in deep-report.
    for source_result in (labels_result, locks_result, staking_result, flows_result):
        for gap in source_result.get("coverage_gaps", []) or []:
            if isinstance(gap, dict):
                coverage_gaps.append(
                    CoverageGap(
                        metric=f"relations.{source_result.get('command')}.{gap.get('metric')}",
                        reason=str(gap.get("reason") or ""),
                        suggested_source=str(gap.get("suggested_source") or ""),
                    )
                )

    return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)


def _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config):
    return CommandResult(
        command="relations",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
