from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class CorporateEvent:
    """正規化後的公司行動事件。"""

    stock_id: str
    stock_name: str
    market: str  # TWSE / TPEx
    event_type: str  # cash_dividend / stock_dividend / rights_issue / par_value_change
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
    ex_type_label: str | None = None  # 原始「權/息/權息」標籤
    price: float | None = None
    cash_yield: float | None = None
    score: float = 0.0
    grade: str = "C"
    reasons: list[str] = field(default_factory=list)
    watch: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("event_date", "suspend_date", "resume_date"):
            value = payload.get(key)
            if isinstance(value, date):
                payload[key] = value.isoformat()
        return payload
