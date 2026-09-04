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
}


def render_markdown_report(
    events: list[CorporateEvent],
    *,
    as_of: date | None = None,
    title: str = "台股事件驅動監控日報",
) -> str:
    as_of = as_of or date.today()
    watch = [e for e in events if e.watch]
    lines = [
        f"# {title}",
        "",
        f"- 產生日期：`{as_of.isoformat()}`",
        f"- 事件總數：`{len(events)}`",
        f"- 建議關注（A/B）：`{len(watch)}`",
        "",
        "> 評分只代表『提前關注優先序』，不是上漲機率保證。投資有風險，請自行判斷。",
        "",
        "## 高關注清單",
        "",
    ]
    if not watch:
        lines.append("_目前沒有達到 A/B 級的股票事件。_")
    else:
        lines.extend(
            [
                "| 等級 | 分數 | 代號 | 名稱 | 市場 | 事件 | 日期 | 關鍵數字 | 理由摘要 |",
                "| --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
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

    lines.extend(["", "## 完整事件表（依分數排序）", ""])
    if not events:
        lines.append("_無事件。_")
    else:
        lines.extend(
            [
                "| 等級 | 分數 | 代號 | 名稱 | 事件 | 日期 | 現金股利 | 配股率 | 換股率 | 現價 | 殖利率 |",
                "| --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for event in events:
            lines.append(
                "| {grade} | {score:.1f} | {sid} | {name} | {etype} | {edate} | {cash} | {sdr} | {split} | {price} | {yld} |".format(
                    grade=event.grade,
                    score=event.score,
                    sid=event.stock_id,
                    name=event.stock_name,
                    etype=EVENT_TYPE_ZH.get(event.event_type, event.event_type),
                    edate=event.event_date.isoformat(),
                    cash=_fmt(event.cash_dividend),
                    sdr=_pct(event.stock_dividend_ratio),
                    split=_fmt(event.split_ratio),
                    price=_fmt(event.price),
                    yld=_pct(event.cash_yield),
                )
            )

    lines.extend(
        [
            "",
            "## 使用建議",
            "",
            "1. **面額變更／高配股**：優先看停止買賣前是否已過熱、恢復日籌碼與題材是否仍在。",
            "2. **純除息**：不要只因為『會回補』就買；先看殖利率、除息前漲幅與大盤風險。",
            "3. **5314 類型**：分割／除權只是催化，背後通常還有題材與動能；本 agent 負責提醒，不下單。",
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
        "events": [e.to_dict() for e in events],
    }
    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": md_path, "json": json_path, "latest_markdown": latest_md, "latest_json": latest_json}


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


def _key_metrics(event: CorporateEvent) -> str:
    parts: list[str] = []
    if event.split_ratio:
        parts.append(f"換股 {event.split_ratio:g}")
    if event.stock_dividend_ratio:
        parts.append(f"配股 {event.stock_dividend_ratio:.2%}")
    if event.cash_yield is not None:
        parts.append(f"殖利率 {event.cash_yield:.2%}")
    elif event.cash_dividend:
        parts.append(f"現金 {event.cash_dividend:g}")
    return "，".join(parts) if parts else "-"
