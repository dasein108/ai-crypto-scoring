from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    pass


def build_url(base_url: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return base_url
    query = urlencode({key: value for key, value in params.items() if value is not None})
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{query}"


def get_json(url: str, timeout_seconds: float, headers: dict[str, str] | None = None) -> dict[str, Any] | list[Any]:
    default_headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    request = Request(url, headers={**default_headers, **(headers or {})})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ProviderError(f"Network error for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderError(f"Timed out fetching {url}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Invalid JSON from {url}") from exc
