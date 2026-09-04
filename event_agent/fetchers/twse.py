from __future__ import annotations

from event_agent.classify import classify_ex_event
from event_agent.dates import parse_roc_date, to_float
from event_agent.http_util import http_get_json
from event_agent.models import CorporateEvent

TWSE_EXRIGHT_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"


def fetch_twse_exright_events() -> list[CorporateEvent]:
    rows = http_get_json(TWSE_EXRIGHT_URL) or []
    events: list[CorporateEvent] = []
    for row in rows:
        stock_id = str(row.get("Code") or "").strip()
        if not stock_id:
            continue
        event_date = parse_roc_date(row.get("Date"))
        if event_date is None:
            continue
        stock_ratio = to_float(row.get("StockDividendRatio"), 0.0) or 0.0
        cash = to_float(row.get("CashDividend"), 0.0) or 0.0
        sub_ratio = to_float(row.get("SubscriptionRatio"), 0.0) or 0.0
        sub_price = to_float(row.get("SubscriptionPricePerShare"))
        ex_label = str(row.get("Exdividend") or "").strip() or None
        events.append(
            CorporateEvent(
                stock_id=stock_id,
                stock_name=str(row.get("Name") or "").strip(),
                market="TWSE",
                event_type=classify_ex_event(ex_label, stock_ratio, cash, sub_ratio),
                event_date=event_date,
                source="TWSE:TWT48U_ALL",
                cash_dividend=cash or None,
                stock_dividend_ratio=stock_ratio or None,
                subscription_ratio=sub_ratio or None,
                subscription_price=sub_price,
                ex_type_label=ex_label,
            )
        )
    return events
