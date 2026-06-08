from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    cache_dir: Path
    raw_dir: Path
    reports_dir: Path
    offline: bool
    request_timeout_seconds: float
    eth_rpc_url: str | None
    base_rpc_url: str | None
    arb_rpc_url: str | None
    op_rpc_url: str | None
    bsc_rpc_url: str | None
    blockscout_api_key: str | None
    etherscan_api_key: str | None
    dune_api_key: str | None
    footprint_api_key: str | None
    flipside_api_key: str | None
    mobula_api_key: str | None
    arkham_api_key: str | None
    tokenomist_api_key: str | None
    messari_api_key: str | None

    @classmethod
    def from_env(cls) -> "AppConfig":
        data_dir = Path(os.getenv("TOKEN_RESEARCH_DATA_DIR", ".token-research")).resolve()
        cache_dir = data_dir / "cache"
        raw_dir = data_dir / "raw"
        reports_dir = data_dir / "reports"

        offline_raw = os.getenv("TOKEN_RESEARCH_OFFLINE", "")
        truthy = {"1", "true", "yes", "on"}
        falsy = {"", "0", "false", "no", "off"}
        offline_lower = offline_raw.strip().lower()
        if offline_lower not in truthy and offline_lower not in falsy:
            raise ValueError(
                f"TOKEN_RESEARCH_OFFLINE must be one of {sorted(truthy | falsy - {''})}; got {offline_raw!r}."
            )
        offline = offline_lower in truthy

        timeout_raw = os.getenv("TOKEN_RESEARCH_REQUEST_TIMEOUT_SECONDS", "4.0")
        try:
            request_timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise ValueError(
                f"TOKEN_RESEARCH_REQUEST_TIMEOUT_SECONDS must be a float; got {timeout_raw!r}."
            ) from exc
        if request_timeout_seconds <= 0:
            raise ValueError(
                f"TOKEN_RESEARCH_REQUEST_TIMEOUT_SECONDS must be > 0; got {request_timeout_seconds}."
            )

        return cls(
            data_dir=data_dir,
            cache_dir=cache_dir,
            raw_dir=raw_dir,
            reports_dir=reports_dir,
            offline=offline,
            request_timeout_seconds=request_timeout_seconds,
            eth_rpc_url=os.getenv("TOKEN_RESEARCH_ETH_RPC_URL"),
            base_rpc_url=os.getenv("TOKEN_RESEARCH_BASE_RPC_URL"),
            arb_rpc_url=os.getenv("TOKEN_RESEARCH_ARB_RPC_URL"),
            op_rpc_url=os.getenv("TOKEN_RESEARCH_OP_RPC_URL"),
            bsc_rpc_url=os.getenv("TOKEN_RESEARCH_BSC_RPC_URL"),
            blockscout_api_key=os.getenv("TOKEN_RESEARCH_BLOCKSCOUT_API_KEY"),
            etherscan_api_key=os.getenv("TOKEN_RESEARCH_ETHERSCAN_API_KEY"),
            dune_api_key=os.getenv("TOKEN_RESEARCH_DUNE_API_KEY"),
            footprint_api_key=os.getenv("TOKEN_RESEARCH_FOOTPRINT_API_KEY"),
            flipside_api_key=os.getenv("TOKEN_RESEARCH_FLIPSIDE_API_KEY"),
            mobula_api_key=os.getenv("TOKEN_RESEARCH_MOBULA_API_KEY"),
            arkham_api_key=os.getenv("TOKEN_RESEARCH_ARKHAM_API_KEY"),
            tokenomist_api_key=os.getenv("TOKEN_RESEARCH_TOKENOMIST_API_KEY"),
            messari_api_key=os.getenv("TOKEN_RESEARCH_MESSARI_API_KEY"),
        )

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.raw_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)
