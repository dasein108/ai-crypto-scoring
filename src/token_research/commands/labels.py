from __future__ import annotations

from dataclasses import asdict

from token_research.chains import get_blockscout_base_url
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout, dune
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "labels": [],
        "merge_strategy": ["dune", "arkham", "mobula", "manual_registry"],
    }

    if not identity.token_address or config.offline:
        if config.offline:
            coverage_gaps.append(CoverageGap(metric=CoverageMetric.LABELS, reason="Offline mode enabled.", suggested_source="Disable offline mode."))
        else:
            coverage_gaps.append(CoverageGap(metric=CoverageMetric.LABELS, reason="No token address available.", suggested_source="Resolve token address first."))
        return _build_result(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    # Collect top holder addresses from Blockscout to look up in Dune labels
    holder_addresses: list[str] = []
    explorer_base = get_blockscout_base_url(identity.chain)
    if explorer_base:
        try:
            holders = blockscout.get_token_holders(identity.chain, identity.token_address, config, page=1, offset=20)
            holder_addresses = [str(h.get("address", "")) for h in holders if h.get("address")]
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.BLOCKSCOUT_UNAVAILABLE, message=str(exc), severity="info"))

    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()

    def _push(address: str, row: dict[str, object]) -> None:
        key = (
            address.lower(),
            str(row.get("source") or ""),
            str(row.get("label") or ""),
        )
        if key in seen:
            return
        seen.add(key)
        row = dict(row)
        row["address"] = address
        merged.append(row)

    # Free path: Blockscout address tags for each top holder.
    if holder_addresses and explorer_base:
        for addr in holder_addresses:
            try:
                info = blockscout.get_address_info(identity.chain, addr, config)
            except ProviderError as exc:
                warnings.append(WarningItem(code=WarningCode.BLOCKSCOUT_UNAVAILABLE, message=str(exc), severity="info"))
                continue
            for row in blockscout.extract_address_labels(info):
                _push(addr, row)

    # Paid path: Dune labels (if key configured).
    if holder_addresses and config.dune_api_key:
        try:
            rows = dune.query_address_labels(holder_addresses, config)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                addr = str(row.get("address") or "")
                if not addr:
                    continue
                normalized = {
                    "source": "dune_labels",
                    "label": str(row.get("label") or row.get("name") or ""),
                    "type": str(row.get("type") or row.get("category") or "labeled"),
                }
                if normalized["label"]:
                    _push(addr, normalized)
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DUNE_UNAVAILABLE, message=str(exc), severity="warning"))
            coverage_gaps.append(CoverageGap(metric=CoverageMetric.LABELS, reason="Dune label query failed.", suggested_source="Dune"))
    elif not config.dune_api_key:
        coverage_gaps.append(
            CoverageGap(
                metric="labels.dune",
                reason="Dune API key not configured — Blockscout tags only.",
                suggested_source="Set TOKEN_RESEARCH_DUNE_API_KEY for richer labels.",
            )
        )

    if not holder_addresses:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.LABELS,
                reason="No holder addresses available for label lookup.",
                suggested_source="Blockscout or other holder source.",
            )
        )

    metrics["labels"] = merged
    if not merged:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.LABELS, reason="No labels returned from any source.", suggested_source="Blockscout tags or Dune labels"))

    return _build_result(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)


def _build_result(
    query: str,
    chain: str | None,
    address: str | None,
    include_premium: bool,
    identity: object,
    metrics: dict[str, object],
    warnings: list[WarningItem],
    coverage_gaps: list[CoverageGap],
    config: AppConfig,
) -> CommandResult:
    return CommandResult(
        command="labels",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
