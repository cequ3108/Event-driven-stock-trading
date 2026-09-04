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
        score -= 8
        reasons.append(f"事件日已過 {abs(days)} 天（仍可能有後續觀察價值）")
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
            reasons.append(
                f"現金股利金額偏高 {event.cash_dividend:g} 元（未還原股價）"
            )

    elif etype == "rights_issue":
        score += 5
        reasons.append("現金增資／認購：偏稀釋，通常不是單純做多事件")
        if (
            event.subscription_price
            and event.price
            and event.subscription_price < event.price * 0.7
        ):
            score += 4
            reasons.append("認購價相對市價有折價，需另評估棄權／認購策略")

    elif etype == "forced_short_cover":
        score += 28
        reasons.append("強制融券回補／停券：空方須在期限前還券，具備軋空題材條件")
        reason = event.reason or event.ex_type_label or ""
        if any(token in reason for token in ("除權", "減資", "現金增資", "合併", "分割", "面額")):
            score += 8
            reasons.append(f"停券原因「{reason}」較具結構性籌碼壓力")
        elif "除息" in reason:
            score += 3
            reasons.append(f"停券原因「{reason}」：回補力道通常弱於除權／減資")
        elif reason:
            score += 2
            reasons.append(f"停券原因：{reason}")

        bal = event.short_balance or 0.0
        util = event.short_utilization or 0.0
        if bal >= 5000:
            score += 18
            reasons.append(f"融券餘額偏高 {bal:,.0f} 張，軋空潛力較大")
        elif bal >= 1000:
            score += 12
            reasons.append(f"融券餘額 {bal:,.0f} 張")
        elif bal >= 200:
            score += 6
            reasons.append(f"融券餘額 {bal:,.0f} 張")
        elif bal > 0:
            score += 1
            reasons.append(f"融券餘額偏低 {bal:,.0f} 張，純事件軋空機率有限")
        else:
            score -= 8
            reasons.append("目前融券餘額接近 0，強制回補的實際買盤可能很弱")

        if util >= 0.2:
            score += 10
            reasons.append(f"融券使用率偏高 {util:.1%}")
        elif util >= 0.05:
            score += 4
            reasons.append(f"融券使用率 {util:.1%}")

    elif etype == "convertible_bond_board":
        score += 26
        reasons.append("董事會／發行進度出現可轉債訊息：常成為短線題材催化")
        detail = event.detail or ""
        if "董事會決議" in detail or "決議發行" in detail:
            score += 8
            reasons.append("屬董事會決議／發行決策階段，適合列入觀察清單")
        if any(token in detail for token in ("收足", "訂價", "申報生效")):
            score += 6
            reasons.append("已進入收足／訂價／生效階段，距掛牌更近")

    elif etype == "convertible_bond_listing":
        score += 24
        reasons.append("可轉債即將／剛掛牌：市場常炒『可轉債行情』敘事")
        if 0 <= days <= 5:
            score += 8
            reasons.append("掛牌／發行日臨近")
        amount = event.issue_amount or 0.0
        if amount >= 2_000_000_000:
            score += 10
            reasons.append(f"發行規模大（約 {amount / 1e8:.1f} 億）")
        elif amount >= 500_000_000:
            score += 6
            reasons.append(f"發行規模約 {amount / 1e8:.1f} 億")
        elif amount > 0:
            score += 2
            reasons.append(f"發行規模約 {amount / 1e8:.1f} 億")
        if event.conversion_price and event.price and event.price > 0:
            premium = event.conversion_price / event.price - 1
            if premium <= 0.05:
                score += 6
                reasons.append(f"轉換價接近市價（溢價 {premium:.1%}），股債連動較敏感")
            elif premium >= 0.35:
                score -= 2
                reasons.append(f"轉換價溢價偏高 {premium:.1%}，短線股價激勵可能有限")

    if is_likely_etf_or_bond(event.stock_id):
        score -= 25
        reasons.append("疑似 ETF／債券／特別股，事件驅動股票策略權重降低")

    if event.price and event.price >= 300 and etype != "par_value_change":
        score += 2
        reasons.append("高價股：若後續出現分割／高配股，題材更容易發酵")

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

    watch = grade in {"A", "B"} and not is_likely_etf_or_bond(event.stock_id)
    # 軋空觀察：沒有足夠融券餘額時，不進高關注，避免「有停券就關注」的噪音
    if watch and etype == "forced_short_cover":
        bal = event.short_balance or 0.0
        util = event.short_utilization or 0.0
        if bal < 50 and util < 0.05:
            watch = False
            reasons.append("融券餘額／使用率偏低，僅列入完整表供參考，不進高關注")
    event.watch = watch
    return event


def _keep_event(event: CorporateEvent, as_of: date) -> bool:
    if event.event_date >= as_of:
        return True
    if (
        event.event_type == "par_value_change"
        and event.resume_date
        and event.resume_date >= as_of
    ):
        return True
    if event.event_type == "convertible_bond_board" and (as_of - event.event_date).days <= 21:
        return True
    if event.event_type == "convertible_bond_listing" and (as_of - event.event_date).days <= 7:
        return True
    return False


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
        if not _keep_event(event, as_of):
            continue
        enrich_with_price(event, prices)
        scored.append(score_event(event, as_of=as_of))
    scored.sort(key=lambda e: (-e.score, e.event_date, e.stock_id))
    return scored
