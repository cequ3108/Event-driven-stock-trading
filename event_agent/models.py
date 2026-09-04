from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class CorporateEvent:
    """正規化後的公司行動／籌碼事件。"""

    stock_id: str
    stock_name: str
    market: str  # TWSE / TPEx
    event_type: str
    event_date: date
    source: str
    cash_dividend: float | None = None
    stock_dividend_ratio: float | None = None  # 無償配股率，例如 0.1 = 配 10%
    subscription_ratio: float | None = None
    subscription_price: float | None = None
    split_ratio: float | None = None  # 面額變更換股率，例如 20 = 1 拆 20
    par_value_from: float | None = None
    par_value_to: float | None = None
    suspend_date: date | None = None
    resume_date: date | None = None
    ex_type_label: str | None = None  # 原始「權/息/權息」或停券原因等標籤
    price: float | None = None
    cash_yield: float | None = None
    # 強制融券回補／軋空相關
    reason: str | None = None
    cover_end_date: date | None = None  # 停券迄日
    short_balance: float | None = None  # 融券餘額（張）
    short_limit: float | None = None  # 融券限額（張）
    short_utilization: float | None = None  # 融券使用率 0~1
    # 可轉債相關
    cb_code: str | None = None
    cb_name: str | None = None
    issue_amount: float | None = None  # 發行金額（元）
    conversion_price: float | None = None
    listing_date: date | None = None
    detail: str | None = None
    score: float = 0.0
    grade: str = "C"
    reasons: list[str] = field(default_factory=list)
    watch: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "event_date",
            "suspend_date",
            "resume_date",
            "cover_end_date",
            "listing_date",
        ):
            value = payload.get(key)
            if isinstance(value, date):
                payload[key] = value.isoformat()
        return payload
