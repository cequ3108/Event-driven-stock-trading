from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from event_agent.dates import parse_roc_date, parse_ymd_date, to_float
from event_agent.http_util import http_get_json

CACHE_DIR = Path("output/cache")


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def _load_cache(name: str) -> Any | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cache(name: str, payload: Any) -> None:
    path = _cache_path(name)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def fetch_twse_exdiv_results(start: date, end: date) -> list[dict[str, Any]]:
    """證交所除權除息計算結果（可查歷史區間）。"""
    cache = f"twse_twt49u_{_yyyymmdd(start)}_{_yyyymmdd(end)}.json"
    cached = _load_cache(cache)
    if cached is not None:
        return cached

    url = "https://www.twse.com.tw/exchangeReport/TWT49U"
    payload = http_get_json(
        url,
        params={
            "response": "json",
            "startDate": _yyyymmdd(start),
            "endDate": _yyyymmdd(end),
        },
    )
    fields = payload.get("fields") or []
    out: list[dict[str, Any]] = []
    for row in payload.get("data") or []:
        item = {fields[i]: row[i] for i in range(min(len(fields), len(row)))}
        stock_id = str(item.get("股票代號") or "").strip()
        if len(stock_id) != 4 or not stock_id.isdigit():
            continue
        event_date = parse_roc_date(str(item.get("資料日期") or ""))
        if event_date is None:
            continue
        out.append(
            {
                "market": "TWSE",
                "stock_id": stock_id,
                "stock_name": str(item.get("股票名稱") or "").strip(),
                "event_date": event_date.isoformat(),
                "ex_rights": str(item.get("權/息") or item.get("除權息") or "").strip(),
                "ref_price": to_float(
                    item.get("除權息參考價") or item.get("參考價") or item.get("開盤競價基準")
                ),
                "pre_close": to_float(item.get("除權息前收盤價")),
                "dividend_value": to_float(item.get("權值+息值")),
                "raw": item,
            }
        )
    _save_cache(cache, out)
    return out


def fetch_twse_daily_quotes(d: date) -> dict[str, dict[str, float | None]]:
    cache = f"twse_mi_index_{_yyyymmdd(d)}.json"
    cached = _load_cache(cache)
    if cached is not None:
        return cached

    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    payload = http_get_json(
        url,
        params={"response": "json", "date": _yyyymmdd(d), "type": "ALLBUT0999"},
    )
    out: dict[str, dict[str, float | None]] = {}
    if str(payload.get("stat") or "") not in {"OK", "很抱歉，沒有符合條件的資料!"}:
        # still try parse
        pass
    tables = payload.get("tables") or []
    for table in tables:
        title = str(table.get("title") or "")
        if "每日收盤行情" not in title:
            continue
        fields = table.get("fields") or []
        try:
            id_i = fields.index("證券代號")
            open_i = fields.index("開盤價")
            high_i = fields.index("最高價")
            low_i = fields.index("最低價")
            close_i = fields.index("收盤價")
        except ValueError:
            continue
        for row in table.get("data") or []:
            sid = str(row[id_i]).strip()
            if len(sid) != 4 or not sid.isdigit():
                continue
            out[sid] = {
                "open": to_float(row[open_i]),
                "high": to_float(row[high_i]),
                "low": to_float(row[low_i]),
                "close": to_float(row[close_i]),
            }
        break
    _save_cache(cache, out)
    time.sleep(0.12)
    return out


def fetch_tpex_daily_quotes(d: date) -> dict[str, dict[str, float | None]]:
    cache = f"tpex_daily_{_yyyymmdd(d)}.json"
    cached = _load_cache(cache)
    if cached is not None:
        return cached

    url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
    payload = http_get_json(
        url,
        params={"date": d.strftime("%Y/%m/%d"), "id": "", "response": "json"},
    )
    out: dict[str, dict[str, float | None]] = {}
    tables = payload.get("tables") or []
    if not tables:
        _save_cache(cache, out)
        time.sleep(0.12)
        return out

    rows = tables[0].get("data") or []
    # 找出標題列
    header_idx = None
    for i, row in enumerate(rows[:5]):
        if row and "證券代號" in str(row[0]):
            header_idx = i
            break
    if header_idx is None:
        _save_cache(cache, out)
        time.sleep(0.12)
        return out

    fields = [str(x) for x in rows[header_idx]]
    try:
        id_i = fields.index("證券代號")
        open_i = next(i for i, f in enumerate(fields) if "開盤" in f)
        high_i = next(i for i, f in enumerate(fields) if "最高" in f)
        low_i = next(i for i, f in enumerate(fields) if "最低" in f)
        close_i = next(i for i, f in enumerate(fields) if "收盤" in f)
    except (ValueError, StopIteration):
        _save_cache(cache, out)
        time.sleep(0.12)
        return out

    for row in rows[header_idx + 1 :]:
        if not row or len(row) <= max(id_i, close_i):
            continue
        sid = str(row[id_i]).strip()
        if len(sid) != 4 or not sid.isdigit():
            continue
        out[sid] = {
            "open": to_float(row[open_i]),
            "high": to_float(row[high_i]),
            "low": to_float(row[low_i]),
            "close": to_float(row[close_i]),
        }
    _save_cache(cache, out)
    time.sleep(0.12)
    return out


def fetch_twse_margin_map(d: date) -> dict[str, dict[str, float | None]]:
    cache = f"twse_margin_{_yyyymmdd(d)}.json"
    cached = _load_cache(cache)
    if cached is not None:
        return cached

    url = "https://www.twse.com.tw/exchangeReport/MI_MARGN"
    payload = http_get_json(
        url,
        params={"response": "json", "date": _yyyymmdd(d), "selectType": "ALL"},
    )
    out: dict[str, dict[str, float | None]] = {}
    tables = payload.get("tables") or []
    if len(tables) < 2:
        _save_cache(cache, out)
        time.sleep(0.12)
        return out
    fields = tables[1].get("fields") or []
    try:
        id_i = fields.index("股票代號")
        bal_i = next(i for i, f in enumerate(fields) if "融券" in f and "今日餘額" in f)
        limit_i = next(i for i, f in enumerate(fields) if "融券" in f and "限額" in f)
    except (ValueError, StopIteration):
        _save_cache(cache, out)
        time.sleep(0.12)
        return out
    for row in tables[1].get("data") or []:
        sid = str(row[id_i]).strip()
        if len(sid) != 4 or not sid.isdigit():
            continue
        bal = to_float(row[bal_i])
        limit = to_float(row[limit_i])
        util = (bal / limit) if bal is not None and limit else None
        out[sid] = {"short_balance": bal, "short_limit": limit, "short_util": util}
    _save_cache(cache, out)
    time.sleep(0.12)
    return out


class MarketQuoteCache:
    """依日期快取上市／上櫃收盤截面，避免逐檔打 API。"""

    def __init__(self) -> None:
        self._quotes: dict[str, dict[str, dict[str, float | None]]] = {}
        self._margin: dict[str, dict[str, dict[str, float | None]]] = {}

    def quotes_on(self, d: date) -> dict[str, dict[str, float | None]]:
        key = d.isoformat()
        if key not in self._quotes:
            merged = dict(fetch_twse_daily_quotes(d))
            for sid, q in fetch_tpex_daily_quotes(d).items():
                merged.setdefault(sid, q)
            self._quotes[key] = merged
        return self._quotes[key]

    def price(self, stock_id: str, d: date, field: str = "close") -> float | None:
        q = self.quotes_on(d).get(stock_id)
        if not q:
            return None
        return q.get(field)  # type: ignore[return-value]

    def find_trading_day(self, start: date, direction: int = 1, max_days: int = 12) -> date | None:
        d = start
        for _ in range(max_days):
            if self.quotes_on(d):
                return d
            d = d + timedelta(days=direction)
        return None

    def shift_trading_days(self, start: date, n: int) -> date | None:
        if n == 0:
            return self.find_trading_day(start, 1)
        d = start
        stepped = 0
        direction = 1 if n > 0 else -1
        target = abs(n)
        # 先對齊到最近交易日
        aligned = self.find_trading_day(start, direction if n > 0 else -1)
        if aligned is None:
            return None
        d = aligned
        if n == 0:
            return d
        while stepped < target:
            d = d + timedelta(days=direction)
            if self.quotes_on(d):
                stepped += 1
            if abs((d - start).days) > 45:
                return None
        return d

    def margin_on(self, d: date) -> dict[str, dict[str, float | None]]:
        key = d.isoformat()
        if key not in self._margin:
            self._margin[key] = fetch_twse_margin_map(d)
        return self._margin[key]


def fetch_finmind_splits_ytd(start: date, end: date) -> list[dict[str, Any]]:
    cache = f"finmind_splits_{start.isoformat()}_{end.isoformat()}.json"
    cached = _load_cache(cache)
    if cached is not None:
        return cached
    url = "https://api.finmindtrade.com/api/v4/data"
    payload = http_get_json(
        url,
        params={
            "dataset": "TaiwanStockSplitPrice",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    rows = payload.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        stock_id = str(row.get("stock_id") or "").strip()
        if len(stock_id) != 4:
            continue
        d = parse_ymd_date(str(row.get("date") or ""))
        if d is None or d < start or d > end:
            continue
        out.append(
            {
                "stock_id": stock_id,
                "event_date": d.isoformat(),
                "type": str(row.get("type") or ""),
                "before_price": to_float(row.get("before_price")),
                "after_price": to_float(row.get("after_price")),
                "max_price": to_float(row.get("max_price")),
                "min_price": to_float(row.get("min_price")),
                "raw": row,
            }
        )
    _save_cache(cache, out)
    return out


def _stock_id_from_cb_code(bond_code: str) -> str | None:
    code = bond_code.strip()
    if len(code) >= 5 and code[:4].isdigit():
        return code[:4]
    return None


def fetch_cb_listings_ytd(start: date, end: date) -> list[dict[str, Any]]:
    """TPEx 公開 API：轉（交）換公司債發行資料，篩選年度內掛牌／發行。"""
    cache = f"tpex_cb_listings_{start.isoformat()}_{end.isoformat()}.json"
    cached = _load_cache(cache)
    if cached is not None:
        return cached
    url = "https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data"
    rows = http_get_json(url) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bond_code = str(row.get("BondCode") or "").strip()
        stock_id = _stock_id_from_cb_code(bond_code)
        if not stock_id:
            continue
        listing = parse_ymd_date(str(row.get("ListingDate") or ""))
        issue = parse_ymd_date(str(row.get("IssueDate") or ""))
        # 優先用掛牌日；沒掛牌日則用發行日（部分私募／未上市櫃）
        event_date = listing or issue
        if event_date is None or event_date < start or event_date > end:
            continue
        if not listing:
            # 無掛牌日多為未上櫃私募債，現股事件訊號較弱，略過
            continue
        out.append(
            {
                "bond_code": bond_code,
                "bond_name": str(row.get("ShortName") or "").strip(),
                "stock_id": stock_id,
                "stock_name": str(row.get("IssuerName") or "").strip(),
                "listing_date": listing.isoformat() if listing else None,
                "issue_date": issue.isoformat() if issue else None,
                "event_date": event_date.isoformat(),
                "issue_amount": to_float(row.get("IssueAmount")),
                "conversion_price": to_float(row.get("Conversion/ExchangePriceAtIssuance")),
                "raw": row,
            }
        )
    _save_cache(cache, out)
    return out


def fetch_all_twse_exdiv_ytd(start: date, end: date) -> list[dict[str, Any]]:
    """按季抓取區間內上市除權息結果（單次區間過長 API 可能截斷）。"""
    chunks: list[tuple[date, date]] = []
    cursor = date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
    while cursor <= end:
        q_end_month = cursor.month + 2
        if q_end_month > 12:
            q_end = date(cursor.year, 12, 31)
        else:
            # 季末
            if q_end_month in {3, 12}:
                q_end = date(cursor.year, q_end_month, 31)
            elif q_end_month == 6:
                q_end = date(cursor.year, 6, 30)
            else:
                q_end = date(cursor.year, 9, 30)
        chunk_start = max(cursor, start)
        chunk_end = min(q_end, end)
        chunks.append((chunk_start, chunk_end))
        # next quarter
        if cursor.month >= 10:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 3, 1)

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for a, b in chunks:
        for row in fetch_twse_exdiv_results(a, b):
            key = (row["stock_id"], row["event_date"])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out
