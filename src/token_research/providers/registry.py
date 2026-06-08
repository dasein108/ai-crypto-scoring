from __future__ import annotations

from token_research.config import AppConfig
from token_research.models import SourceRef, dataclass_list
from token_research.providers.base import ProviderDescriptor


def _descriptor_to_source(item: ProviderDescriptor) -> SourceRef:
    return SourceRef(
        name=item.name,
        kind=item.category,
        access=item.access,
        docs_url=item.docs_url,
        enabled=item.enabled,
        notes=item.notes,
    )


def build_provider_registry(config: AppConfig, include_premium: bool = False) -> list[ProviderDescriptor]:
    descriptors = [
        ProviderDescriptor("rpc", "canonical", "local", "https://eips.ethereum.org/", bool(config.eth_rpc_url or config.base_rpc_url), "On-chain contract calls: totalSupply, decimals, balanceOf."),
        ProviderDescriptor("dexscreener", "market", "free", "https://docs.dexscreener.com/api/reference", True, "Pool discovery and liquidity snapshots."),
        ProviderDescriptor("defillama", "market", "free", "https://coins.llama.fi/", True, "Price by contract with confidence score."),
        ProviderDescriptor("blockscout", "explorer", "free_or_key", "https://docs.blockscout.com/devs/apis/rest", True, "Holders and explorer-backed token surfaces."),
        ProviderDescriptor("etherscan", "explorer", "key", "https://docs.etherscan.io/", bool(config.etherscan_api_key), "Etherscan-family explorer access."),
        ProviderDescriptor("dune", "sql", "key", "https://docs.dune.com/api-reference/executions/endpoint/execute-sql", bool(config.dune_api_key), "Historical SQL and labels."),
        ProviderDescriptor("footprint", "sql", "key", "https://docs.footprint.network/reference/post_native-async", bool(config.footprint_api_key), "Historical SQL execution."),
        ProviderDescriptor("flipside", "sql", "key", "https://docs.flipsidecrypto.com/api", bool(config.flipside_api_key), "Supplemental analytics layer."),
        ProviderDescriptor("mobula", "enrichment", "key", "https://docs.mobula.io/rest-api-reference/introduction", bool(config.mobula_api_key), "Holder and LP lock enrichment."),
    ]
    if include_premium:
        descriptors.extend(
            [
                ProviderDescriptor("arkham", "entity", "premium", "https://intel.arkm.com/api/docs", bool(config.arkham_api_key), "Entity intelligence."),
                ProviderDescriptor("tokenomist", "tokenomics", "premium", "https://docs.unlocks.app/api-documents/api-endpoints", bool(config.tokenomist_api_key), "Curated unlock schedules."),
                ProviderDescriptor("messari", "tokenomics", "premium", "https://docs.messari.io/user-guides/intel/token-unlocks", bool(config.messari_api_key), "Curated unlock schedules and categories."),
            ]
        )
    return descriptors


def build_source_refs(config: AppConfig, include_premium: bool = False) -> list[dict[str, object]]:
    return dataclass_list([_descriptor_to_source(item) for item in build_provider_registry(config, include_premium)])
