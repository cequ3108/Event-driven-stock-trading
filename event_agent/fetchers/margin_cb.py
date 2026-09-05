from __future__ import annotations

from datetime import date, timedelta

from event_agent.dates import parse_roc_date, parse_ymd_date, to_float
from event_agent.http_util import http_get_json
from event_agent.models import CorporateEvent
from event_agent.scoring import is_likely_etf_or_bond

TWSE_SHORT_COVER_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BFI84U"
TPEX_SHORT_COVER_URL = "https://www.tpex.org.tw/openapi/v1/tpex_margin_trading_term"
TWSE_MARGIN_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
TPEX_MARGIN_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
CB_ISSUE_URL = "https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data"
TWSE_MATERIAL_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
TPEX_MATERIAL_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"


def fetch_short_balances() -> dict[str, dict[str, float]]:
    """回傳 {stock_id: {short_balance, short_limit, short_utilization}}。"""
    out: dict[str, dict[str, float]] = {}

    for row in http_get_json(TWSE_MARGIN_URL) or []:
        stock_id = str(row.get("股票代號") or "").strip()
        if not stock_id:
            continue
        balance = to_float(row.get("融券今日餘額"), 0.0) or 0.0
        limit = to_float(row.get("融券限額"), 0.0) or 0.0
        out[stock_id] = {
            "short_balance": balance,
            "short_limit": limit,
            "short_utilization": (balance / limit) if limit > 0 else 0.0,
        }

    for row in http_get_json(TPEX_MARGIN_URL) or []:
        stock_id = str(row.get("SecuritiesCompanyCode") or "").strip()
        if not stock_id:
            continue
        balance = to_float(row.get("ShortSaleBalance"), 0.0) or 0.0
        limit = to_float(row.get("ShortSaleQuota"), 0.0) or 0.0
        util = to_float(row.get("ShortSaleUtilizationRate"))
        if util is not None and util > 1:
            util /= 100.0
        out[stock_id] = {
            "short_balance": balance,
            "short_limit": limit,
            "short_utilization": util
            if util is not None
            else ((balance / limit) if limit > 0 else 0.0),
        }
    return out


def _attach_short_interest(
    event: CorporateEvent, balances: dict[str, dict[str, float]]
) -> None:
    stats = balances.get(event.stock_id)
    if not stats:
        return
    event.short_balance = stats.get("short_balance")
    event.short_limit = stats.get("short_limit")
    event.short_utilization = stats.get("short_utilization")


def fetch_twse_short_cover_events(
    *,
    as_of: date | None = None,
    balances: dict[str, dict[str, float]] | None = None,
) -> list[CorporateEvent]:
    """集中市場停資停券預告：停券起日＝最後融券回補日。"""
    as_of = as_of or date.today()
    balances = balances if balances is not None else fetch_short_balances()
    events: list[CorporateEvent] = []
    for row in http_get_json(TWSE_SHORT_COVER_URL) or []:
        stock_id = str(row.get("Code") or "").strip()
        if not stock_id:
            continue
        cover_date = parse_roc_date(row.get("StartDate"))
        if cover_date is None or cover_date < as_of:
            continue
        reason = str(row.get("Reason") or "").strip() or None
        event = CorporateEvent(
            stock_id=stock_id,
            stock_name=str(row.get("Name") or "").strip(),
            market="TWSE",
            event_type="forced_short_cover",
            event_date=cover_date,
            source="TWSE:BFI84U",
            reason=reason,
            cover_end_date=parse_roc_date(row.get("EndDate")),
            ex_type_label=reason,
        )
        _attach_short_interest(event, balances)
        events.append(event)
    return events


def fetch_tpex_short_cover_events(
    *,
    as_of: date | None = None,
    balances: dict[str, dict[str, float]] | None = None,
) -> list[CorporateEvent]:
    """上櫃暫停融券賣出預告。"""
    as_of = as_of or date.today()
    balances = balances if balances is not None else fetch_short_balances()
    events: list[CorporateEvent] = []
    for row in http_get_json(TPEX_SHORT_COVER_URL) or []:
        stock_id = str(row.get("SecuritiesCompanyCode") or "").strip()
        if not stock_id:
            continue
        cover_date = parse_roc_date(row.get("ShortSaleSuspensionStartDate"))
        if cover_date is None or cover_date < as_of:
            continue
        reason = str(row.get("Reason") or "").strip() or None
        event = CorporateEvent(
            stock_id=stock_id,
            stock_name=str(row.get("CompanyName") or "").strip(),
            market="TPEx",
            event_type="forced_short_cover",
            event_date=cover_date,
            source="TPEx:tpex_margin_trading_term",
            reason=reason,
            cover_end_date=parse_roc_date(row.get("ShortSaleSuspensionEndDate")),
            ex_type_label=reason,
        )
        _attach_short_interest(event, balances)
        events.append(event)
    return events


def _stock_id_from_cb_code(bond_code: str) -> str | None:
    code = bond_code.strip()
    if len(code) >= 5 and code[:4].isdigit():
        return code[:4]
    return None


def fetch_convertible_bond_listing_events(
    *,
    as_of: date | None = None,
    lookback_days: int = 7,
    lookahead_days: int = 60,
) -> list[CorporateEvent]:
    """轉（交）換債發行資料：關注即將／剛掛牌可轉債對應之現股。"""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=lookback_days)
    end = as_of + timedelta(days=lookahead_days)
    events: list[CorporateEvent] = []
    for row in http_get_json(CB_ISSUE_URL) or []:
        bond_code = str(row.get("BondCode") or "").strip()
        stock_id = _stock_id_from_cb_code(bond_code)
        if not stock_id or is_likely_etf_or_bond(stock_id):
            continue
        issue_date = parse_ymd_date(row.get("IssueDate"))
        listing_date = parse_ymd_date(row.get("ListingDate"))
        focus = listing_date or issue_date
        if focus is None or focus < start or focus > end:
            continue
        amount = to_float(row.get("IssueAmount"))
        conv = to_float(row.get("Conversion/ExchangePriceAtIssuance"))
        events.append(
            CorporateEvent(
                stock_id=stock_id,
                stock_name=str(row.get("IssuerName") or "").strip(),
                market="TWSE",
                event_type="convertible_bond_listing",
                event_date=focus,
                source="TPEx:bond_ISSBD5_data",
                cb_code=bond_code,
                cb_name=str(row.get("ShortName") or "").strip() or None,
                issue_amount=amount,
                conversion_price=conv if conv and conv > 0 else None,
                listing_date=listing_date,
                detail=f"發行日 {issue_date.isoformat()}" if issue_date else None,
            )
        )
    return events


def _is_cb_issue_subject(subject: str) -> bool:
    text = subject.replace("\r", "").replace("\n", "")
    if "轉換公司債" not in text and "可轉換公司債" not in text:
        return False
    negative = ("贖回", "賣回", "終止櫃檯", "注意交易", "達公布注意", "更名", "行使賣回權")
    if any(token in text for token in negative):
        return False
    positive = (
        "董事會決議",
        "決議發行",
        "辦理發行",
        "完成訂價",
        "收足債款",
        "收足價款",
        "申報生效",
        "核准",
        "延長募集",
    )
    return any(token in text for token in positive)


def _parse_mops_date(value: object) -> date | None:
    text = str(value or "").strip().replace("/", "")
    return parse_roc_date(text) if text else None


def fetch_convertible_bond_board_events(
    *,
    as_of: date | None = None,
    lookback_days: int = 21,
) -> list[CorporateEvent]:
    """重大訊息中的可轉債董事會決議／發行進度（早期訊號）。"""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=lookback_days)
    events: list[CorporateEvent] = []

    for row in http_get_json(TWSE_MATERIAL_URL) or []:
        stock_id = str(row.get("公司代號") or "").strip()
        subject = str(row.get("主旨 ") or row.get("主旨") or "")
        if not stock_id or not _is_cb_issue_subject(subject):
            continue
        event_date = _parse_mops_date(row.get("事實發生日")) or _parse_mops_date(
            row.get("發言日期")
        )
        if event_date is None or event_date < start or event_date > as_of:
            continue
        events.append(
            CorporateEvent(
                stock_id=stock_id,
                stock_name=str(row.get("公司名稱") or "").strip(),
                market="TWSE",
                event_type="convertible_bond_board",
                event_date=event_date,
                source="TWSE:t187ap04_L",
                detail=subject.replace("\r", "").replace("\n", ""),
                reason="重大訊息-可轉債",
            )
        )

    for row in http_get_json(TPEX_MATERIAL_URL) or []:
        stock_id = str(row.get("SecuritiesCompanyCode") or "").strip()
        subject = str(row.get("主旨") or "")
        if not stock_id or not _is_cb_issue_subject(subject):
            continue
        event_date = _parse_mops_date(row.get("事實發生日")) or _parse_mops_date(
            row.get("發言日期")
        )
        if event_date is None or event_date < start or event_date > as_of:
            continue
        events.append(
            CorporateEvent(
                stock_id=stock_id,
                stock_name=str(row.get("CompanyName") or "").strip(),
                market="TPEx",
                event_type="convertible_bond_board",
                event_date=event_date,
                source="TPEx:mopsfin_t187ap04_O",
                detail=subject.replace("\r", "").replace("\n", ""),
                reason="重大訊息-可轉債",
            )
        )
    return events
