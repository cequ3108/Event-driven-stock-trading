from __future__ import annotations


def classify_ex_event(
    ex_label: str | None,
    stock_ratio: float | None,
    cash: float | None,
    sub_ratio: float | None,
) -> str:
    has_stock = bool(stock_ratio and stock_ratio > 0)
    has_cash = bool(cash and cash > 0)
    has_sub = bool(sub_ratio and sub_ratio > 0)
    if has_sub and not has_stock and not has_cash:
        return "rights_issue"
    if has_stock and has_cash:
        return "stock_and_cash_dividend"
    if has_stock:
        return "stock_dividend"
    if has_cash:
        return "cash_dividend"
    label = (ex_label or "").strip()
    if "權" in label and "息" in label:
        return "stock_and_cash_dividend"
    if "權" in label:
        return "stock_dividend"
    return "cash_dividend"
