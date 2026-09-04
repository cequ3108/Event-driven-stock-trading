from __future__ import annotations

from event_agent.dates import to_float
from event_agent.http_util import http_get_json

TWSE_PRICE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_PRICE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def fetch_latest_prices() -> dict[str, float]:
    """回傳 {stock_id: latest_close}，合併上市 + 上櫃。"""
    prices: dict[str, float] = {}

    for row in http_get_json(TWSE_PRICE_URL) or []:
        stock_id = str(row.get("Code") or "").strip()
        close = to_float(row.get("ClosingPrice"))
        if stock_id and close and close > 0:
            prices[stock_id] = close

    for row in http_get_json(TPEX_PRICE_URL) or []:
        stock_id = str(row.get("SecuritiesCompanyCode") or "").strip()
        close = to_float(row.get("Close"))
        if stock_id and close and close > 0:
            prices[stock_id] = close

    return prices
