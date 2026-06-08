from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from token_research.cache import persist_raw_payload
from token_research.config import AppConfig
from token_research.providers.http import ProviderError


DUNE_API_BASE = "https://api.dune.com/api/v1"
DEFAULT_POLL_INTERVAL = 3.0
# Real Dune queries on erc20 Transfer tables regularly take 2–4 minutes to
# complete even with a 7-day window. Cap at ~6 minutes so we don't hang on
# pathological runs but still give queries a realistic chance to finish.
DEFAULT_MAX_POLLS = 120


def _headers(config: AppConfig) -> dict[str, str]:
    if not config.dune_api_key:
        raise ProviderError("Dune API key not configured (TOKEN_RESEARCH_DUNE_API_KEY).")
    return {
        "Content-Type": "application/json",
        "X-Dune-Api-Key": config.dune_api_key,
    }


def _post(url: str, body: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    data = json.dumps(body).encode()
    request = Request(url, data=data, headers=_headers(config))
    try:
        with urlopen(request, timeout=config.request_timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ProviderError(f"Dune HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ProviderError(f"Dune network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderError(f"Dune timeout for {url}") from exc
    if not isinstance(result, dict):
        raise ProviderError(f"Unexpected Dune response type from {url}")
    return result


def _get(url: str, config: AppConfig, timeout_override: float | None = None) -> dict[str, Any]:
    request = Request(url, headers=_headers(config))
    try:
        with urlopen(request, timeout=timeout_override or config.request_timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ProviderError(f"Dune HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ProviderError(f"Dune network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderError(f"Dune timeout for {url}") from exc
    if not isinstance(result, dict):
        raise ProviderError(f"Unexpected Dune response type from {url}")
    return result


def execute_sql(sql: str, config: AppConfig, performance: str = "medium") -> str:
    """Submit a SQL query for execution. Returns the execution_id."""
    result = _post(
        f"{DUNE_API_BASE}/sql/execute",
        {"sql": sql, "performance": performance},
        config,
    )
    execution_id = result.get("execution_id")
    if not execution_id:
        raise ProviderError(f"Dune execute response missing execution_id: {result}")
    return str(execution_id)


def get_execution_results(
    execution_id: str,
    config: AppConfig,
    limit: int = 1000,
    offset: int = 0,
) -> dict[str, Any]:
    """Fetch results for an execution. May not be finished yet — check is_execution_finished."""
    url = f"{DUNE_API_BASE}/execution/{execution_id}/results?limit={limit}&offset={offset}"
    # Use config timeout if it's larger than the 30s default for results
    timeout = max(30.0, config.request_timeout_seconds)
    return _get(url, config, timeout_override=timeout)


def run_sql_sync(
    sql: str,
    config: AppConfig,
    *,
    performance: str = "medium",
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_polls: int = DEFAULT_MAX_POLLS,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Execute SQL and poll until results are ready. Returns the rows list."""
    execution_id = execute_sql(sql, config, performance=performance)

    for _ in range(max_polls):
        result = get_execution_results(execution_id, config, limit=limit)
        if result.get("is_execution_finished"):
            error = result.get("error")
            if error:
                raise ProviderError(f"Dune query failed: {error.get('message', error)}")
            inner = result.get("result") or {}
            rows = inner.get("rows") or []
            return [row for row in rows if isinstance(row, dict)]
        time.sleep(poll_interval)

    raise ProviderError(f"Dune query {execution_id} did not complete within {max_polls * poll_interval}s.")


def query_token_flows(
    token_address: str,
    config: AppConfig,
    *,
    days: int = 7,  # Optimization: Reduced default window from 30 to 7 days
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Query net token flows by destination category.
    Optimizations:
    1. Reduced default time window from 30 days to 7 days to decrease data volume.
    2. Used a CTE (transfers) to filter by token and time before joining with labels.
    3. Added a safety LIMIT within the CTE to prevent runaway execution on hyper-active tokens.
    4. **Nonce comment** — Dune's execution cache hashes the SQL string, so identical
       queries return the same execution_id. When a prior execution gets stuck, all
       retries poll the dead one. Injecting a timestamp comment forces a fresh execution.
    """
    nonce = int(time.time())
    sql = f"""
    -- run_id: {nonce}
    WITH transfers AS (
        -- Pre-filter transfers to target token and time window to reduce join volume
        SELECT "to", "from", value, evt_tx_hash
        FROM erc20_ethereum.evt_Transfer
        WHERE contract_address = FROM_HEX('{token_address.lower().replace("0x", "")}')
            AND evt_block_time > NOW() - INTERVAL '{days}' DAY
        LIMIT 10000 -- Safety limit for high-volume tokens
    )
    SELECT
        CASE
            WHEN dl.category = 'CEX' THEN 'cex'
            WHEN dl.category = 'DEX' THEN 'dex'
            WHEN dl.category = 'Bridge' THEN 'bridge'
            WHEN dl.category IS NOT NULL THEN lower(dl.category)
            ELSE 'unknown'
        END AS destination_category,
        SUM(CASE WHEN t."to" = dl.address THEN CAST(t.value AS double) ELSE 0 END) AS inflow_raw,
        SUM(CASE WHEN t."from" = dl.address THEN CAST(t.value AS double) ELSE 0 END) AS outflow_raw,
        COUNT(DISTINCT t.evt_tx_hash) AS tx_count
    FROM transfers t
    LEFT JOIN labels.addresses dl
        ON (t."to" = dl.address OR t."from" = dl.address)
    GROUP BY 1
    ORDER BY inflow_raw DESC
    LIMIT {limit}
    """
    rows = run_sql_sync(sql, config)
    persist_raw_payload(config, "dune", "token-flows", token_address.lower(), {"rows": rows})
    return rows


def query_address_labels(
    addresses: list[str],
    config: AppConfig,
) -> list[dict[str, Any]]:
    """Look up Dune labels for a list of addresses. Uses a nonce comment to
    avoid Dune's execution-cache returning a stuck cached execution."""
    if not addresses:
        return []
    hex_list = ", ".join(f"FROM_HEX('{a.lower().replace('0x', '')}')" for a in addresses[:50])
    nonce = int(time.time())
    sql = f"""
    -- run_id: {nonce}
    SELECT
        LOWER(CONCAT('0x', TO_HEX(address))) AS address,
        name,
        category,
        source
    FROM labels.addresses
    WHERE address IN ({hex_list})
    ORDER BY address
    """
    rows = run_sql_sync(sql, config)
    persist_raw_payload(config, "dune", "labels", "batch", {"rows": rows})
    return rows
