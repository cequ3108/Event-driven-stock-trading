from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from event_agent.models import CorporateEvent

EVENT_TYPE_ZH = {
    "par_value_change": "面額變更／分割",
    "stock_dividend": "除權（股票股利）",
    "stock_and_cash_dividend": "除權息",
    "cash_dividend": "除息",
    "rights_issue": "現金增資／認購",
    "forced_short_cover": "強制融券回補／停券",
    "convertible_bond_board": "可轉債董事會／發行進度",
    "convertible_bond_listing": "可轉債掛牌／發行",
}


def render_markdown_report(
    events: list[CorporateEvent],
    *,
    as_of: date | None = None,
    title: str = "台股事件驅動監控日報",
) -> str:
    as_of = as_of or date.today()
    watch = [e for e in events if e.watch]
    short_cover = [e for e in events if e.event_type == "forced_short_cover"]
    short_watch = [e for e in short_cover if e.watch]
    cb_events = [
        e
        for e in events
        if e.event_type in {"convertible_bond_board", "convertible_bond_listing"}
    ]
    cb_watch = [e for e in cb_events if e.watch]

    lines = [
        f"# {title}",
        "",
        f"- 產生日期：`{as_of.isoformat()}`",
        f"- 事件總數：`{len(events)}`",
        f"- 建議關注（A/B）：`{len(watch)}`",
        f"- 強制融券回補事件：`{len(short_cover)}`（關注 {len(short_watch)}）",
        f"- 可轉債相關事件：`{len(cb_events)}`（關注 {len(cb_watch)}）",
        "",
        "> 評分只代表『提前關注優先序』，不是上漲機率保證。投資有風險，請自行判斷。",
        "",
        "## 高關注清單",
        "",
    ]
    if not watch:
        lines.append("_目前沒有達到 A/B 級的股票事件。_")
    else:
        lines.extend(_watch_table(watch))

    lines.extend(
        [
            "",
            "## 強制融券回補／軋空觀察",
            "",
            "> 停券起日通常是最後回補日。真正有軋空味的，多半還要看融券餘額與使用率，",
            "> 不是「有停券預告」就會大漲。",
            "",
        ]
    )
    if not short_cover:
        lines.append("_目前沒有未來強制融券回補事件。_")
    else:
        lines.extend(
            [
                "| 等級 | 分數 | 代號 | 名稱 | 最後回補日 | 原因 | 融券餘額(張) | 使用率 |",
                "| --- | ---: | --- | --- | --- | --- | ---: | ---: |",
            ]
        )
        for event in short_cover[:40]:
            lines.append(
                "| {grade} | {score:.1f} | {sid} | {name} | {edate} | {reason} | {bal} | {util} |".format(
                    grade=event.grade,
                    score=event.score,
                    sid=event.stock_id,
                    name=event.stock_name,
                    edate=event.event_date.isoformat(),
                    reason=event.reason or event.ex_type_label or "-",
                    bal=_fmt(event.short_balance),
                    util=_pct(event.short_utilization),
                )
            )

    lines.extend(
        [
            "",
            "## 可轉債觀察",
            "",
            "> 董事會決議／收足債款／掛牌日都可能引發題材；但也要注意可轉債本質偏稀釋，",
            "> 短線行情與中長期評價未必同一方向。",
            "",
        ]
    )
    if not cb_events:
        lines.append("_目前沒有近期可轉債董事會／掛牌事件。_")
    else:
        lines.extend(
            [
                "| 等級 | 分數 | 代號 | 名稱 | 事件 | 日期 | 可轉債 | 發行金額 | 轉換價 | 摘要 |",
                "| --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- |",
            ]
        )
        for event in cb_events[:40]:
            lines.append(
                "| {grade} | {score:.1f} | {sid} | {name} | {etype} | {edate} | {cb} | {amt} | {conv} | {detail} |".format(
                    grade=event.grade,
                    score=event.score,
                    sid=event.stock_id,
                    name=event.stock_name,
                    etype=EVENT_TYPE_ZH.get(event.event_type, event.event_type),
                    edate=event.event_date.isoformat(),
                    cb=event.cb_name or event.cb_code or "-",
                    amt=_amount(event.issue_amount),
                    conv=_fmt(event.conversion_price),
                    detail=_short_text(event.detail),
                )
            )

    lines.extend(["", "## 完整事件表（依分數排序）", ""])
    if not events:
        lines.append("_無事件。_")
    else:
        lines.extend(
            [
                "| 等級 | 分數 | 代號 | 名稱 | 事件 | 日期 | 關鍵數字 | 現價 |",
                "| --- | ---: | --- | --- | --- | --- | --- | ---: |",
            ]
        )
        for event in events:
            lines.append(
                "| {grade} | {score:.1f} | {sid} | {name} | {etype} | {edate} | {key} | {price} |".format(
                    grade=event.grade,
                    score=event.score,
                    sid=event.stock_id,
                    name=event.stock_name,
                    etype=EVENT_TYPE_ZH.get(event.event_type, event.event_type),
                    edate=event.event_date.isoformat(),
                    key=_key_metrics(event),
                    price=_fmt(event.price),
                )
            )

    lines.extend(
        [
            "",
            "## 使用建議",
            "",
            "1. **面額變更／高配股**：優先看停止買賣前是否已過熱、恢復日籌碼與題材是否仍在。",
            "2. **強制融券回補**：先看融券餘額／使用率；餘額很低時，停券預告的軋空意義通常不大。",
            "3. **可轉債**：董事會決議是早期訊號，掛牌日是題材高峰觀察點；留意發行規模與轉換價溢價。",
            "4. **純除息**：不要只因為『會回補』就買；先看殖利率、除息前漲幅與大盤風險。",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    events: list[CorporateEvent],
    output_dir: str | Path,
    *,
    as_of: date | None = None,
) -> dict[str, Path]:
    as_of = as_of or date.today()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"watchlist_{as_of.isoformat()}.md"
    json_path = out / f"watchlist_{as_of.isoformat()}.json"
    latest_md = out / "watchlist_latest.md"
    latest_json = out / "watchlist_latest.json"

    markdown = render_markdown_report(events, as_of=as_of)
    payload = {
        "as_of": as_of.isoformat(),
        "count": len(events),
        "watch_count": sum(1 for e in events if e.watch),
        "short_cover_count": sum(1 for e in events if e.event_type == "forced_short_cover"),
        "cb_count": sum(
            1
            for e in events
            if e.event_type in {"convertible_bond_board", "convertible_bond_listing"}
        ),
        "events": [e.to_dict() for e in events],
    }
    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "markdown": md_path,
        "json": json_path,
        "latest_markdown": latest_md,
        "latest_json": latest_json,
    }


def _watch_table(watch: list[CorporateEvent]) -> list[str]:
    lines = [
        "| 等級 | 分數 | 代號 | 名稱 | 市場 | 事件 | 日期 | 關鍵數字 | 理由摘要 |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for event in watch:
        lines.append(
            "| {grade} | {score:.1f} | {sid} | {name} | {market} | {etype} | {edate} | {key} | {why} |".format(
                grade=event.grade,
                score=event.score,
                sid=event.stock_id,
                name=event.stock_name,
                market=event.market,
                etype=EVENT_TYPE_ZH.get(event.event_type, event.event_type),
                edate=event.event_date.isoformat(),
                key=_key_metrics(event),
                why="；".join(event.reasons[:2]) or "-",
            )
        )
    return lines


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 100:
        return f"{value:.2f}"
    return f"{value:g}"


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2%}"


def _amount(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 1e8:
        return f"{value / 1e8:.1f}億"
    if value >= 1e4:
        return f"{value / 1e4:.0f}萬"
    return f"{value:g}"


def _short_text(value: str | None, limit: int = 28) -> str:
    if not value:
        return "-"
    text = value.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _key_metrics(event: CorporateEvent) -> str:
    parts: list[str] = []
    if event.event_type == "forced_short_cover":
        if event.short_balance is not None:
            parts.append(f"融券 {event.short_balance:,.0f} 張")
        if event.short_utilization is not None:
            parts.append(f"使用率 {event.short_utilization:.2%}")
        if event.reason:
            parts.append(event.reason)
    elif event.event_type in {"convertible_bond_board", "convertible_bond_listing"}:
        if event.cb_name or event.cb_code:
            parts.append(event.cb_name or event.cb_code or "")
        if event.issue_amount:
            parts.append(_amount(event.issue_amount))
        if event.conversion_price:
            parts.append(f"轉價 {event.conversion_price:g}")
    else:
        if event.split_ratio:
            parts.append(f"換股 {event.split_ratio:g}")
        if event.stock_dividend_ratio:
            parts.append(f"配股 {event.stock_dividend_ratio:.2%}")
        if event.cash_yield is not None:
            parts.append(f"殖利率 {event.cash_yield:.2%}")
        elif event.cash_dividend:
            parts.append(f"現金 {event.cash_dividend:g}")
    return "，".join(parts) if parts else "-"
