from __future__ import annotations

import re
from datetime import date, datetime


_ROC_DATE_RE = re.compile(r"^(\d{2,3})[/\-]?(\d{2})[/\-]?(\d{2})$")
_ROC_CN_RE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def parse_roc_date(value: str | None) -> date | None:
    """將民國日期字串轉成西元 date。支援 1150907、115/09/07、115年09月07日。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "—", "N/A", "null"}:
        return None

    match = _ROC_CN_RE.search(text)
    if match:
        year = int(match.group(1)) + 1911
        return date(year, int(match.group(2)), int(match.group(3)))

    compact = text.replace(".", "").replace(" ", "")
    match = _ROC_DATE_RE.match(compact)
    if match:
        year = int(match.group(1)) + 1911
        return date(year, int(match.group(2)), int(match.group(3)))

    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def to_float(value: object, default: float | None = None) -> float | None:
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "—", "N/A", "null", ""}:
        return default
    try:
        return float(text)
    except ValueError:
        return default
