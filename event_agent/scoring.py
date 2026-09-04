from __future__ import annotations

from datetime import date

from event_agent.models import CorporateEvent

# ETF / 受益憑證等多半不是「回補行情」標的，預設降權或排除於高關注清單
_ETF_PREFIXES = ("00", "01")


def is_likely_etf_or_bond(stock_id: str) -> bool:
    code = stock_id.strip()
    if not code:
        return True
    if code[-1:].isalpha() and code[:-1].isdigit() and len(code) >= 5:
        # 例如 00710B、00400A
        return True
    if code.isdigit() and len(code) == 4:
        return False
    if code.startswith(_ETF_PREFIXES) and len(code) >= 4:
        return True
    return len(code) != 4


def enrich_with_price(event: CorporateEvent, prices: dict[str, float]) -> None:
    price = prices.get(event.stock_id)
    if price is None:
        return
    event.price = price
    if event.cash_dividend and price > 0:
        event.cash_yield = event.cash_dividend / price


def score_event(event: CorporateEvent, *, as_of: date | None = None) -> CorporateEvent:
    """以透明規則打分。分數只代表『值得提前關注的程度』，不是上漲保證。"""
    as_of = as_of or date.today()
    score = 0.0
    reasons: list[str] = []

    days = (event.event_date - as_of).days
    if days < 0:
        # 已過期事件：僅保留極低分，通常應在 filter 時剔除
        score -= 20
        reasons.append("事件日已過")
    elif days <= 3:
        score += 12
        reasons.append(f"即將發生（{days} 天內）")
    elif days <= 10:
        score += 8
        reasons.append(f"近 {days} 天內")
    elif days <= 30:
        score += 4
        reasons.append(f"{days} 天後")
    else:
        score += 1
        reasons.append(f"較遠（{days} 天後）")

    etype = event.event_type

    if etype == "par_value_change":
        score += 35
        reasons.append("面額變更／分割：恢復交易日常見投機熱度")
        ratio = event.split_ratio or 0.0
        if ratio >= 10:
            score += 15
            reasons.append(f"高換股率 {ratio:g}：股價視覺親民化效果強")
        elif ratio >= 4:
            score += 8
            reasons.append(f"換股率 {ratio:g}")
        elif ratio >= 2:
            score += 4
            reasons.append(f"換股率 {ratio:g}")
        if event.suspend_date and event.suspend_date >= as_of:
            score += 6
            reasons.append("尚可在停止買賣前布局")
        if event.resume_date and 0 <= (event.resume_date - as_of).days <= 5:
            score += 8
            reasons.append("恢復買賣臨近，關注開盤動能")

    elif etype in {"stock_dividend", "stock_and_cash_dividend"}:
        score += 22
        reasons.append("含股票股利／除權：歷史上較常出現回補題材")
        ratio = event.stock_dividend_ratio or 0.0
        if ratio >= 0.5:
            score += 28
            reasons.append(f"超高配股率 {ratio:.2%}（類似大幅『實質分割』）")
        elif ratio >= 0.2:
            score += 18
            reasons.append(f"高配股率 {ratio:.2%}")
        elif ratio >= 0.08:
            score += 10
            reasons.append(f"中高配股率 {ratio:.2%}")
        elif ratio > 0:
            score += 4
            reasons.append(f"配股率 {ratio:.2%}")
        if etype == "stock_and_cash_dividend":
            score += 4
            reasons.append("同時配息，現金殖利率可當次要濾網")
    elif etype == "cash_dividend":
        score += 6
        reasons.append("純除息：回補並非必然，需看殖利率與大盤")
        yld = event.cash_yield
        if yld is not None:
            if yld >= 0.08:
                score += 10
                reasons.append(f"現金殖利率偏高 {yld:.2%}")
            elif yld >= 0.05:
                score += 6
                reasons.append(f"現金殖利率 {yld:.2%}")
            elif yld >= 0.03:
                score += 2
                reasons.append(f"現金殖利率 {yld:.2%}")
            else:
                score -= 2
                reasons.append(f"現金殖利率偏低 {yld:.2%}")
        elif event.cash_dividend and event.cash_dividend >= 5:
            score += 4
            reasons.append(f"現金股利金額偏高 {event.cash_dividend:g} 元（未還原股價）")

    elif etype == "rights_issue":
        score += 5
        reasons.append("現金增資／認購：偏稀釋，通常不是單純做多事件")
        if event.subscription_price and event.price and event.subscription_price < event.price * 0.7:
            score += 4
            reasons.append("認購價相對市價有折價，需另評估棄權／認購策略")

    if is_likely_etf_or_bond(event.stock_id):
        score -= 25
        reasons.append("疑似 ETF／債券／特別股，事件驅動股票策略權重降低")

    if event.price and event.price >= 300 and etype != "par_value_change":
        score += 2
        reasons.append("高價股：若後續出現分割／高配股，題材更容易發酵")

    # 正規化到約 0–100
    score = max(0.0, min(100.0, score))
    if score >= 65:
        grade = "A"
    elif score >= 40:
        grade = "B"
    elif score >= 25:
        grade = "C"
    else:
        grade = "D"

    event.score = round(score, 1)
    event.grade = grade
    event.reasons = reasons
    event.watch = grade in {"A", "B"} and not is_likely_etf_or_bond(event.stock_id)
    return event


def score_events(
    events: list[CorporateEvent],
    *,
    prices: dict[str, float] | None = None,
    as_of: date | None = None,
) -> list[CorporateEvent]:
    as_of = as_of or date.today()
    prices = prices or {}
    scored: list[CorporateEvent] = []
    for event in events:
        if event.event_date < as_of and not (
            event.event_type == "par_value_change"
            and event.resume_date
            and event.resume_date >= as_of
        ):
            continue
        enrich_with_price(event, prices)
        scored.append(score_event(event, as_of=as_of))
    scored.sort(key=lambda e: (-e.score, e.event_date, e.stock_id))
    return scored
