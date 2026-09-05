from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from statistics import mean
from typing import Any

from event_agent.fetchers.history import (
    MarketQuoteCache,
    fetch_all_twse_exdiv_ytd,
    fetch_cb_listings_ytd,
    fetch_finmind_splits_ytd,
)


@dataclass(slots=True)
class TradeRow:
    strategy: str
    stock_id: str
    stock_name: str
    event_date: str
    entry_date: str
    entry_price: float
    exit_d0: float | None = None
    exit_d5: float | None = None
    exit_d10: float | None = None
    ret_d0_pct: float | None = None
    ret_d5_pct: float | None = None
    ret_d10_pct: float | None = None
    recovered_pre_close: bool | None = None
    short_balance: float | None = None
    short_util: float | None = None
    note: str = ""


def _ret(exit_px: float | None, entry: float) -> float | None:
    if exit_px is None or entry <= 0:
        return None
    return (exit_px - entry) / entry * 100.0


def _summarize(rows: list[TradeRow], horizon: str = "d5") -> dict[str, Any]:
    attr = f"ret_{horizon}_pct"
    vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
    if not vals:
        return {"sample_size": 0}
    wins = [v for v in vals if v > 0]
    return {
        "sample_size": len(vals),
        "win_rate": len(wins) / len(vals),
        "avg_return_pct": mean(vals),
        "median_return_pct": sorted(vals)[len(vals) // 2],
        "avg_win_pct": mean(wins) if wins else 0.0,
        "avg_loss_pct": mean([v for v in vals if v <= 0]) if any(v <= 0 for v in vals) else 0.0,
        "best_pct": max(vals),
        "worst_pct": min(vals),
    }


def _stock_stats(rows: list[TradeRow], *, min_trades: int = 2) -> list[dict[str, Any]]:
    by_stock: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        if r.ret_d5_pct is None:
            continue
        by_stock[r.stock_id].append(r)

    ranked: list[dict[str, Any]] = []
    for sid, items in by_stock.items():
        if len(items) < min_trades:
            continue
        rets = [x.ret_d5_pct for x in items if x.ret_d5_pct is not None]
        if not rets:
            continue
        wins = sum(1 for v in rets if v > 0)
        ranked.append(
            {
                "stock_id": sid,
                "stock_name": items[-1].stock_name,
                "trades": len(rets),
                "win_rate": wins / len(rets),
                "avg_d5_return_pct": mean(rets),
                "avg_d0_return_pct": mean(
                    [x.ret_d0_pct for x in items if x.ret_d0_pct is not None] or [0.0]
                ),
                "strategies": sorted({x.strategy for x in items}),
                "last_event_date": max(x.event_date for x in items),
            }
        )
    ranked.sort(key=lambda x: (x["win_rate"], x["avg_d5_return_pct"], x["trades"]), reverse=True)
    return ranked


def _fill_exits(
    cache: MarketQuoteCache,
    stock_id: str,
    entry_date: date,
    entry_price: float,
    event_day: date,
) -> dict[str, float | None]:
    d0 = cache.find_trading_day(event_day, 1)
    d5 = cache.shift_trading_days(event_day, 5) if d0 else None
    d10 = cache.shift_trading_days(event_day, 10) if d0 else None
    exit_d0 = cache.price(stock_id, d0) if d0 else None
    exit_d5 = cache.price(stock_id, d5) if d5 else None
    exit_d10 = cache.price(stock_id, d10) if d10 else None
    return {
        "exit_d0": exit_d0,
        "exit_d5": exit_d5,
        "exit_d10": exit_d10,
        "ret_d0_pct": _ret(exit_d0, entry_price),
        "ret_d5_pct": _ret(exit_d5, entry_price),
        "ret_d10_pct": _ret(exit_d10, entry_price),
        "entry_date": entry_date.isoformat(),
    }


def backtest_ex_dividend(
    cache: MarketQuoteCache,
    events: list[dict[str, Any]],
    *,
    as_of: date,
) -> list[TradeRow]:
    """除權息回補：以除權息參考價（或當日開盤）進場，持有至 D0 / D+5 / D+10。"""
    rows: list[TradeRow] = []
    for ev in events:
        event_day = date.fromisoformat(ev["event_date"])
        # 需至少有 +10 交易日空間才算完整樣本；否則仍算到 as_of 能取到的
        if event_day > as_of:
            continue
        sid = ev["stock_id"]
        ref = ev.get("ref_price")
        trade_day = cache.find_trading_day(event_day, 1)
        if trade_day is None:
            continue
        open_px = cache.price(sid, trade_day, "open")
        entry = ref if ref and ref > 0 else open_px
        if entry is None or entry <= 0:
            continue
        exits = _fill_exits(cache, sid, trade_day, entry, event_day)
        pre = ev.get("pre_close")
        recovered = None
        if pre and exits["exit_d5"] is not None:
            recovered = exits["exit_d5"] >= pre
        rows.append(
            TradeRow(
                strategy="ex_dividend_reclaim",
                stock_id=sid,
                stock_name=ev.get("stock_name") or "",
                event_date=ev["event_date"],
                entry_date=exits["entry_date"],
                entry_price=float(entry),
                exit_d0=exits["exit_d0"],
                exit_d5=exits["exit_d5"],
                exit_d10=exits["exit_d10"],
                ret_d0_pct=exits["ret_d0_pct"],
                ret_d5_pct=exits["ret_d5_pct"],
                ret_d10_pct=exits["ret_d10_pct"],
                recovered_pre_close=recovered,
                note=f"權息={ev.get('ex_rights') or '-'}",
            )
        )
    return rows


def backtest_short_cover(
    cache: MarketQuoteCache,
    events: list[dict[str, Any]],
    *,
    as_of: date,
    min_short_balance: float = 500.0,
    min_short_util: float = 0.05,
) -> list[TradeRow]:
    """強制融券回補代理策略：除權息日前有足夠融券餘額者，T-5 收盤買、事件日收盤賣。

    說明：停券／最後回補日多貼近除權息，公開歷史停券清單不易一次拉齊，
    故以「除權息 + 事前融券水位」作為可回測的代理事件。
    """
    rows: list[TradeRow] = []
    for ev in events:
        event_day = date.fromisoformat(ev["event_date"])
        if event_day > as_of:
            continue
        sid = ev["stock_id"]
        entry_day = cache.shift_trading_days(event_day, -5)
        if entry_day is None:
            continue
        entry = cache.price(sid, entry_day, "close")
        if entry is None or entry <= 0:
            continue
        # 用進場日融券水位過濾
        margin = cache.margin_on(entry_day).get(sid) or {}
        bal = margin.get("short_balance")
        util = margin.get("short_util")
        if bal is None:
            continue
        if bal < min_short_balance and (util is None or util < min_short_util):
            continue
        exits = _fill_exits(cache, sid, entry_day, entry, event_day)
        rows.append(
            TradeRow(
                strategy="short_cover_proxy",
                stock_id=sid,
                stock_name=ev.get("stock_name") or "",
                event_date=ev["event_date"],
                entry_date=exits["entry_date"],
                entry_price=float(entry),
                exit_d0=exits["exit_d0"],
                exit_d5=exits["exit_d5"],
                exit_d10=exits["exit_d10"],
                ret_d0_pct=exits["ret_d0_pct"],
                ret_d5_pct=exits["ret_d5_pct"],
                ret_d10_pct=exits["ret_d10_pct"],
                short_balance=bal,
                short_util=util,
                note="T-5→事件日；濾融券餘額",
            )
        )
    return rows


def backtest_par_value_split(
    cache: MarketQuoteCache,
    events: list[dict[str, Any]],
    *,
    as_of: date,
) -> list[TradeRow]:
    """面額變更／分割：以恢復日後參考價（after_price）進場。"""
    rows: list[TradeRow] = []
    for ev in events:
        event_day = date.fromisoformat(ev["event_date"])
        if event_day > as_of:
            continue
        sid = ev["stock_id"]
        trade_day = cache.find_trading_day(event_day, 1)
        if trade_day is None:
            continue
        entry = ev.get("after_price") or cache.price(sid, trade_day, "open")
        if entry is None or entry <= 0:
            continue
        exits = _fill_exits(cache, sid, trade_day, float(entry), event_day)
        rows.append(
            TradeRow(
                strategy="par_value_split",
                stock_id=sid,
                stock_name="",
                event_date=ev["event_date"],
                entry_date=exits["entry_date"],
                entry_price=float(entry),
                exit_d0=exits["exit_d0"],
                exit_d5=exits["exit_d5"],
                exit_d10=exits["exit_d10"],
                ret_d0_pct=exits["ret_d0_pct"],
                ret_d5_pct=exits["ret_d5_pct"],
                ret_d10_pct=exits["ret_d10_pct"],
                note=str(ev.get("type") or "面額變更"),
            )
        )
    return rows


def backtest_cb_listing(
    cache: MarketQuoteCache,
    events: list[dict[str, Any]],
    *,
    as_of: date,
) -> list[TradeRow]:
    """可轉債掛牌：掛牌前一交易日收盤買進現股，觀察掛牌日／+5／+10。"""
    rows: list[TradeRow] = []
    for ev in events:
        event_day = date.fromisoformat(ev["event_date"])
        if event_day > as_of:
            continue
        sid = ev["stock_id"]
        entry_day = cache.shift_trading_days(event_day, -1)
        if entry_day is None:
            continue
        entry = cache.price(sid, entry_day, "close")
        if entry is None or entry <= 0:
            continue
        exits = _fill_exits(cache, sid, entry_day, entry, event_day)
        rows.append(
            TradeRow(
                strategy="cb_listing",
                stock_id=sid,
                stock_name=ev.get("stock_name") or "",
                event_date=ev["event_date"],
                entry_date=exits["entry_date"],
                entry_price=float(entry),
                exit_d0=exits["exit_d0"],
                exit_d5=exits["exit_d5"],
                exit_d10=exits["exit_d10"],
                ret_d0_pct=exits["ret_d0_pct"],
                ret_d5_pct=exits["ret_d5_pct"],
                ret_d10_pct=exits["ret_d10_pct"],
                note=f"CB {ev.get('bond_code') or ''}",
            )
        )
    return rows


def pick_high_winrate_stocks(
    by_strategy: dict[str, list[TradeRow]],
    *,
    min_trades: int = 2,
    min_win_rate: float = 0.6,
    min_avg_return: float = 0.0,
) -> dict[str, list[dict[str, Any]]]:
    picks: dict[str, list[dict[str, Any]]] = {}
    for name, rows in by_strategy.items():
        ranked = _stock_stats(rows, min_trades=min_trades)
        picks[name] = [
            r
            for r in ranked
            if r["win_rate"] >= min_win_rate and r["avg_d5_return_pct"] >= min_avg_return
        ][:25]
    # 綜合：同一檔在多策略皆佳
    cross: dict[str, dict[str, Any]] = {}
    for name, items in picks.items():
        for item in items:
            sid = item["stock_id"]
            slot = cross.setdefault(
                sid,
                {
                    "stock_id": sid,
                    "stock_name": item["stock_name"],
                    "strategies_hit": [],
                    "best_win_rate": 0.0,
                    "best_avg_d5_return_pct": 0.0,
                    "total_trades": 0,
                },
            )
            slot["strategies_hit"].append(name)
            slot["best_win_rate"] = max(slot["best_win_rate"], item["win_rate"])
            slot["best_avg_d5_return_pct"] = max(
                slot["best_avg_d5_return_pct"], item["avg_d5_return_pct"]
            )
            slot["total_trades"] += item["trades"]
    cross_list = sorted(
        cross.values(),
        key=lambda x: (len(x["strategies_hit"]), x["best_win_rate"], x["best_avg_d5_return_pct"]),
        reverse=True,
    )
    picks["cross_strategy"] = cross_list[:30]
    return picks


def run_ytd_backtest(
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    end = end or date.today()
    start = start or date(end.year, 1, 1)
    cache = MarketQuoteCache()

    exdiv = fetch_all_twse_exdiv_ytd(start, end)
    splits = fetch_finmind_splits_ytd(start, end)
    cbs = fetch_cb_listings_ytd(start, end)

    ex_rows = backtest_ex_dividend(cache, exdiv, as_of=end)
    short_rows = backtest_short_cover(cache, exdiv, as_of=end)
    split_rows = backtest_par_value_split(cache, splits, as_of=end)
    cb_rows = backtest_cb_listing(cache, cbs, as_of=end)

    by_strategy = {
        "ex_dividend_reclaim": ex_rows,
        "short_cover_proxy": short_rows,
        "par_value_split": split_rows,
        "cb_listing": cb_rows,
    }

    summary = {
        name: {
            "d0": _summarize(rows, "d0"),
            "d5": _summarize(rows, "d5"),
            "d10": _summarize(rows, "d10"),
            "pct_recovered_pre_close": (
                sum(1 for r in rows if r.recovered_pre_close) / sum(1 for r in rows if r.recovered_pre_close is not None)
                if any(r.recovered_pre_close is not None for r in rows)
                else None
            ),
        }
        for name, rows in by_strategy.items()
    }

    picks = pick_high_winrate_stocks(by_strategy, min_trades=2, min_win_rate=0.6)

    # 單次事件但報酬突出者也列為觀察（樣本=1，標註）
    single_event_stars: list[dict[str, Any]] = []
    for name, rows in by_strategy.items():
        for r in rows:
            if r.ret_d5_pct is not None and r.ret_d5_pct >= 8:
                single_event_stars.append(
                    {
                        "strategy": name,
                        "stock_id": r.stock_id,
                        "stock_name": r.stock_name,
                        "event_date": r.event_date,
                        "ret_d5_pct": r.ret_d5_pct,
                        "ret_d0_pct": r.ret_d0_pct,
                        "note": "單次事件高報酬，樣本不足，僅供觀察",
                    }
                )
    single_event_stars.sort(key=lambda x: x["ret_d5_pct"], reverse=True)

    return {
        "meta": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "assumptions": [
                "除權息樣本以證交所 TWT49U 上市股票為主（上櫃歷史除權息 API 不穩，暫未納入）",
                "除權息策略：事件日參考價進場，觀察 D0/D+5/D+10 收盤",
                "融券回補：以除權息前融券水位作代理，T-5 收盤進、事件日後持有",
                "面額變更：FinMind TaiwanStockSplitPrice",
                "可轉債：TPEx bond_ISSBD5 有掛牌日者，掛牌前一日收盤買現股",
                "勝率以 D+5 報酬 > 0 計算；高勝率標的需至少 2 筆同策略事件",
            ],
            "event_counts": {
                "ex_dividend_events": len(exdiv),
                "split_events": len(splits),
                "cb_listing_events": len(cbs),
            },
        },
        "summary": summary,
        "high_winrate_picks": picks,
        "single_event_stars": single_event_stars[:40],
        "rows": {
            name: [asdict(r) for r in rows]
            for name, rows in by_strategy.items()
        },
    }


def render_ytd_markdown(result: dict[str, Any]) -> str:
    meta = result["meta"]
    lines = [
        f"# 2026 事件驅動策略回測（{meta['start']} ~ {meta['end']}）",
        "",
        "> 本報告僅供研究，不構成投資建議。樣本期短、有倖存者偏誤與流動性限制。",
        "",
        "## 方法假設",
        "",
    ]
    for a in meta["assumptions"]:
        lines.append(f"- {a}")
    lines += [
        "",
        "## 事件樣本數",
        "",
        f"- 除權息（上市）：{meta['event_counts']['ex_dividend_events']}",
        f"- 面額變更／分割：{meta['event_counts']['split_events']}",
        f"- 可轉債掛牌：{meta['event_counts']['cb_listing_events']}",
        "",
        "## 策略績效摘要（報酬單位：%）",
        "",
    ]

    name_map = {
        "ex_dividend_reclaim": "除權息回補",
        "short_cover_proxy": "融券回補（代理）",
        "par_value_split": "面額變更／分割",
        "cb_listing": "可轉債掛牌",
    }
    for key, title in name_map.items():
        s = result["summary"].get(key) or {}
        lines.append(f"### {title}")
        lines.append("")
        for hz in ("d0", "d5", "d10"):
            block = s.get(hz) or {}
            if not block.get("sample_size"):
                lines.append(f"- **{hz.upper()}**：無有效樣本")
                continue
            lines.append(
                f"- **{hz.upper()}**：樣本 {block['sample_size']}，"
                f"勝率 {block['win_rate']:.1%}，"
                f"平均 {block['avg_return_pct']:.2f}%，"
                f"中位 {block['median_return_pct']:.2f}%，"
                f"最佳 {block['best_pct']:.2f}%／最差 {block['worst_pct']:.2f}%"
            )
        recovered = s.get("pct_recovered_pre_close")
        if recovered is not None:
            lines.append(f"- D+5 收復除權息前收盤價比例：{recovered:.1%}")
        lines.append("")

    lines += ["## 未來再遇事件時，勝率較高標的（D+5，同策略≥2 次且勝率≥60%）", ""]
    picks = result.get("high_winrate_picks") or {}
    for key, title in name_map.items():
        items = picks.get(key) or []
        lines.append(f"### {title}")
        lines.append("")
        if not items:
            lines.append("- （無符合門檻者；樣本不足或勝率未達標）")
            lines.append("")
            continue
        lines.append("| 代號 | 名稱 | 次數 | 勝率 | 平均D+5% |")
        lines.append("| --- | --- | ---: | ---: | ---: |")
        for it in items[:15]:
            lines.append(
                f"| {it['stock_id']} | {it['stock_name']} | {it['trades']} | "
                f"{it['win_rate']:.0%} | {it['avg_d5_return_pct']:.2f} |"
            )
        lines.append("")

    cross = picks.get("cross_strategy") or []
    lines += ["### 跨策略綜合觀察", ""]
    if not cross:
        lines.append("- 無")
    else:
        lines.append("| 代號 | 名稱 | 命中策略數 | 最佳勝率 | 最佳平均D+5% | 總次數 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for it in cross[:20]:
            lines.append(
                f"| {it['stock_id']} | {it['stock_name']} | {len(it['strategies_hit'])} | "
                f"{it['best_win_rate']:.0%} | {it['best_avg_d5_return_pct']:.2f} | {it['total_trades']} |"
            )
    lines += ["", "## 單次事件高報酬觀察（D+5≥8%，樣本=1，勿過度解讀）", ""]
    stars = result.get("single_event_stars") or []
    if not stars:
        lines.append("- 無")
    else:
        lines.append("| 策略 | 代號 | 名稱 | 事件日 | D+5% | D0% |")
        lines.append("| --- | --- | --- | --- | ---: | ---: |")
        for it in stars[:25]:
            lines.append(
                f"| {name_map.get(it['strategy'], it['strategy'])} | {it['stock_id']} | "
                f"{it['stock_name']} | {it['event_date']} | {it['ret_d5_pct']:.2f} | "
                f"{(it.get('ret_d0_pct') or 0):.2f} |"
            )
    lines.append("")
    return "\n".join(lines)
