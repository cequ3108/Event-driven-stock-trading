from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from event_agent.fetchers.finmind import (
    fetch_historical_par_value_changes,
    fetch_stock_prices,
)


@dataclass(slots=True)
class ParValueTradeResult:
    stock_id: str
    stock_name: str
    resume_date: str
    ref_price: float
    day0_close: float
    day0_return_pct: float
    day0_high_return_pct: float
    max_5d_return_pct: float


def evaluate_par_value_resume_edge(
    *,
    start_date: str = "2019-01-01",
    sleep: float = 0.25,
) -> dict[str, Any]:
    """用 FinMind 歷史面額變更 + 真實日 K，評估恢復買賣日相對參考價的表現。

    注意：樣本小、且會做分割的公司常已是熱門股，存在選擇偏誤。
    """
    events = fetch_historical_par_value_changes(start_date=start_date)
    results: list[ParValueTradeResult] = []

    for event in events:
        stock_id = str(event["stock_id"])
        resume = str(event["date"])
        ref = float(event["after_ref_close"])
        start = (date.fromisoformat(resume) - timedelta(days=5)).isoformat()
        end = (date.fromisoformat(resume) + timedelta(days=20)).isoformat()
        time.sleep(sleep)
        prices = fetch_stock_prices(stock_id, start, end)
        after = [p for p in prices if p["date"] >= resume]
        if not after or ref <= 0:
            continue
        day0 = after[0]
        day0_close = float(day0["close"])
        day0_high = float(day0["max"])
        day0_ret = (day0_close - ref) / ref * 100
        day0_high_ret = (day0_high - ref) / ref * 100
        following = [p for p in prices if p["date"] > day0["date"]][:5]
        path = [day0_ret] + [(float(p["close"]) - ref) / ref * 100 for p in following]
        results.append(
            ParValueTradeResult(
                stock_id=stock_id,
                stock_name=str(event.get("stock_name") or ""),
                resume_date=resume,
                ref_price=ref,
                day0_close=day0_close,
                day0_return_pct=day0_ret,
                day0_high_return_pct=day0_high_ret,
                max_5d_return_pct=max(path),
            )
        )

    n = len(results)
    if n == 0:
        return {"sample_size": 0, "rows": [], "summary": {}}

    def rate(pred) -> float:
        return sum(1 for r in results if pred(r)) / n

    summary = {
        "sample_size": n,
        "avg_day0_close_vs_ref_pct": sum(r.day0_return_pct for r in results) / n,
        "pct_close_above_ref": rate(lambda r: r.day0_return_pct > 0),
        "pct_close_ge_5pct": rate(lambda r: r.day0_return_pct >= 5),
        "pct_close_ge_9pct": rate(lambda r: r.day0_return_pct >= 9),
        "pct_touch_limit_up_approx": rate(lambda r: r.day0_high_return_pct >= 9),
        "avg_max_5d_vs_ref_pct": sum(r.max_5d_return_pct for r in results) / n,
        "caveats": [
            "樣本數少，統計不穩。",
            "會辦理面額變更的公司常已具題材／動能，存在選擇偏誤。",
            "此回測假設在參考價附近成交；實際停止買賣前布局會有不同成本與風險。",
            "不代表未來仍有效，更不是投資建議。",
        ],
    }
    return {
        "sample_size": n,
        "summary": summary,
        "rows": [
            {
                "stock_id": r.stock_id,
                "stock_name": r.stock_name,
                "resume_date": r.resume_date,
                "ref_price": r.ref_price,
                "day0_close": r.day0_close,
                "day0_return_pct": round(r.day0_return_pct, 2),
                "day0_high_return_pct": round(r.day0_high_return_pct, 2),
                "max_5d_return_pct": round(r.max_5d_return_pct, 2),
            }
            for r in results
        ],
    }


def render_backtest_markdown(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        "# 面額變更恢復買賣日 — 簡易回測",
        "",
        f"- 樣本數：`{summary.get('sample_size', 0)}`",
        f"- 恢復日收盤 > 參考價：`{summary.get('pct_close_above_ref', 0):.0%}`",
        f"- 恢復日收盤 ≥ 5%：`{summary.get('pct_close_ge_5pct', 0):.0%}`",
        f"- 恢復日收盤 ≥ 9%（近漲停）：`{summary.get('pct_close_ge_9pct', 0):.0%}`",
        f"- 盤中高點 ≥ 9%：`{summary.get('pct_touch_limit_up_approx', 0):.0%}`",
        f"- 平均恢復日報酬：`{summary.get('avg_day0_close_vs_ref_pct', 0):.2f}%`",
        f"- 平均 5 日內最大相對參考價報酬：`{summary.get('avg_max_5d_vs_ref_pct', 0):.2f}%`",
        "",
        "## 注意事項",
        "",
    ]
    for caveat in summary.get("caveats", []):
        lines.append(f"- {caveat}")
    lines.extend(
        [
            "",
            "## 明細",
            "",
            "| 日期 | 代號 | 名稱 | 參考價 | 收盤 | 日報酬% | 盤中高% | 5日最大% |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in result.get("rows") or []:
        lines.append(
            f"| {row['resume_date']} | {row['stock_id']} | {row['stock_name']} | "
            f"{row['ref_price']} | {row['day0_close']} | {row['day0_return_pct']} | "
            f"{row['day0_high_return_pct']} | {row['max_5d_return_pct']} |"
        )
    lines.append("")
    return "\n".join(lines)
