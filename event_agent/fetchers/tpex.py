from __future__ import annotations

from event_agent.classify import classify_ex_event
from event_agent.dates import parse_roc_date, to_float
from event_agent.http_util import http_get_json
from event_agent.models import CorporateEvent

TPEX_EXRIGHT_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
TPEX_PAR_VALUE_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/pvChgAnn"


def fetch_tpex_exright_events() -> list[CorporateEvent]:
    rows = http_get_json(TPEX_EXRIGHT_URL) or []
    events: list[CorporateEvent] = []
    for row in rows:
        stock_id = str(row.get("SecuritiesCompanyCode") or "").strip()
        if not stock_id:
            continue
        event_date = parse_roc_date(row.get("ExRrightsExDividendDate"))
        if event_date is None:
            continue
        stock_ratio = to_float(row.get("StockDividendRatio"), 0.0) or 0.0
        cash = to_float(row.get("CashDividend"), 0.0) or 0.0
        sub_ratio = to_float(row.get("SubscriptionRatioToNewSharesIssued"), 0.0) or 0.0
        sub_price = to_float(row.get("SubscriptionPricePerShare"))
        ex_label = str(row.get("ExRrightsExDividend") or "").strip() or None
        events.append(
            CorporateEvent(
                stock_id=stock_id,
                stock_name=str(row.get("CompanyName") or "").strip(),
                market="TPEx",
                event_type=classify_ex_event(ex_label, stock_ratio, cash, sub_ratio),
                event_date=event_date,
                source="TPEx:tpex_exright_prepost",
                cash_dividend=cash or None,
                stock_dividend_ratio=stock_ratio or None,
                subscription_ratio=sub_ratio or None,
                subscription_price=sub_price,
                ex_type_label=ex_label,
            )
        )
    return events


def fetch_tpex_par_value_events() -> list[CorporateEvent]:
    """上櫃變更股票面額預告表（即將停止/恢復買賣）。"""
    payload = http_get_json(TPEX_PAR_VALUE_URL) or {}
    tables = payload.get("tables") or []
    events: list[CorporateEvent] = []
    for table in tables:
        fields = table.get("fields") or []
        for row in table.get("data") or []:
            mapped = dict(zip(fields, row))
            stock_id = str(mapped.get("證券代號") or "").strip()
            if not stock_id:
                continue
            resume = parse_roc_date(mapped.get("恢復買賣日期"))
            suspend = parse_roc_date(mapped.get("停止買賣日期"))
            event_date = resume or suspend
            if event_date is None:
                continue
            split_ratio = to_float(mapped.get("變更股票面額換股率"))
            events.append(
                CorporateEvent(
                    stock_id=stock_id,
                    stock_name=str(mapped.get("證券名稱") or "").strip(),
                    market="TPEx",
                    event_type="par_value_change",
                    event_date=event_date,
                    source="TPEx:pvChgAnn",
                    split_ratio=split_ratio,
                    par_value_from=to_float(mapped.get("變更前股票面額")),
                    par_value_to=to_float(mapped.get("變更後股票面額")),
                    suspend_date=suspend,
                    resume_date=resume,
                    ex_type_label="面額變更",
                )
            )
    return events
