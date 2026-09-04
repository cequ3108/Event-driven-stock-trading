from __future__ import annotations

from datetime import date, datetime
from typing import Any

from event_agent.http_util import http_get_json

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"


def _parse_iso(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def fetch_finmind_dataset(
    dataset: str,
    *,
    data_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    token: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if token:
        params["token"] = token
    payload = http_get_json(FINMIND_DATA_URL, params=params) or {}
    if payload.get("status") not in (None, 200, "200"):
        # FinMind 成功時 status 多半是 200
        if not payload.get("data"):
            raise RuntimeError(f"FinMind error: {payload.get('msg') or payload}")
    return list(payload.get("data") or [])


def fetch_historical_par_value_changes(start_date: str = "2019-01-01") -> list[dict[str, Any]]:
    rows = fetch_finmind_dataset("TaiwanStockParValueChange", start_date=start_date)
    for row in rows:
        row["parsed_date"] = _parse_iso(row["date"])
    return rows


def fetch_stock_prices(stock_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return fetch_finmind_dataset(
        "TaiwanStockPrice",
        data_id=stock_id,
        start_date=start_date,
        end_date=end_date,
    )
