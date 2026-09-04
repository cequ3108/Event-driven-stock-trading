from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; EventDrivenStockAgent/0.1; "
        "+https://github.com/cequ3108/Event-driven-stock-trading)"
    ),
    "Accept": "application/json,text/plain,*/*",
}


def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    sleep: float = 0.4,
) -> Any:
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"

    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(sleep * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} retries: {url}") from last_error
