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


@dataclass(slots=True)
class PeakExitTrade:
    """T-5 進場：期間有高點就出場，否則強制抱到 T+5。"""

    strategy: str
    stock_id: str
    stock_name: str
    event_date: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    exit_reason: str  # peak_high | first_high_close | forced_t5
    ret_pct: float
    peak_high: float | None = None
    peak_date: str | None = None
    t5_close: float | None = None
    ret_hold_t5_pct: float | None = None
    short_balance: float | None = None
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


def _trading_days_after_through(
    cache: MarketQuoteCache,
    after: date,
    through: date,
) -> list[date]:
    days: list[date] = []
    d = cache.shift_trading_days(after, 1)
    while d is not None and d <= through:
        days.append(d)
        d = cache.shift_trading_days(d, 1)
        if len(days) > 40:
            break
    return days


def resolve_peak_or_t5_exit(
    cache: MarketQuoteCache,
    stock_id: str,
    entry_day: date,
    entry_price: float,
    event_day: date,
    *,
    mode: str = "peak_high",
) -> dict[str, Any] | None:
    """T-5 進場後的出場規則。

    - peak_high：持有區間最高價 > 進場價時，以該最高價出場（樂觀上界）
    - first_high_close：首次出現高點 > 進場價當日，以收盤價出場（較可執行）
    兩者若整段都沒有高於進場價的高點，則 T+5 收盤強制出場。
    """
    t5 = cache.shift_trading_days(event_day, 5)
    if t5 is None:
        return None
    window = _trading_days_after_through(cache, entry_day, t5)
    if not window:
        return None

    peak_high: float | None = None
    peak_day: date | None = None
    first_high_day: date | None = None
    first_high_close: float | None = None

    for d in window:
        high = cache.price(stock_id, d, "high")
        close = cache.price(stock_id, d, "close")
        if high is None:
            continue
        if peak_high is None or high > peak_high:
            peak_high = high
            peak_day = d
        if first_high_day is None and high > entry_price and close is not None:
            first_high_day = d
            first_high_close = close

    t5_close = cache.price(stock_id, t5, "close")
    if t5_close is None:
        return None

    if mode == "first_high_close":
        if first_high_day is not None and first_high_close is not None:
            exit_day, exit_price, reason = first_high_day, first_high_close, "first_high_close"
        else:
            exit_day, exit_price, reason = t5, t5_close, "forced_t5"
    else:
        if peak_high is not None and peak_day is not None and peak_high > entry_price:
            exit_day, exit_price, reason = peak_day, peak_high, "peak_high"
        else:
            exit_day, exit_price, reason = t5, t5_close, "forced_t5"

    ret = _ret(exit_price, entry_price)
    if ret is None:
        return None
    return {
        "exit_date": exit_day.isoformat(),
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "ret_pct": float(ret),
        "peak_high": peak_high,
        "peak_date": peak_day.isoformat() if peak_day else None,
        "t5_close": t5_close,
        "ret_hold_t5_pct": _ret(t5_close, entry_price),
        "entry_date": entry_day.isoformat(),
    }


def _summarize_peak(rows: list[PeakExitTrade]) -> dict[str, Any]:
    vals = [r.ret_pct for r in rows]
    if not vals:
        return {"sample_size": 0}
    wins = [v for v in vals if v > 0]
    peak_exits = sum(1 for r in rows if r.exit_reason != "forced_t5")
    forced = sum(1 for r in rows if r.exit_reason == "forced_t5")
    hold_vals = [r.ret_hold_t5_pct for r in rows if r.ret_hold_t5_pct is not None]
    return {
        "sample_size": len(vals),
        "win_rate": len(wins) / len(vals),
        "avg_return_pct": mean(vals),
        "median_return_pct": sorted(vals)[len(vals) // 2],
        "avg_win_pct": mean(wins) if wins else 0.0,
        "avg_loss_pct": mean([v for v in vals if v <= 0]) if any(v <= 0 for v in vals) else 0.0,
        "best_pct": max(vals),
        "worst_pct": min(vals),
        "peak_exit_rate": peak_exits / len(vals),
        "forced_t5_rate": forced / len(vals),
        "avg_hold_t5_return_pct": mean(hold_vals) if hold_vals else None,
    }


def backtest_t5_peak_or_hold(
    cache: MarketQuoteCache,
    events: list[dict[str, Any]],
    *,
    strategy: str,
    as_of: date,
    mode: str = "peak_high",
    entry_offset: int = -5,
    min_short_balance: float | None = None,
    min_short_util: float = 0.05,
) -> list[PeakExitTrade]:
    rows: list[PeakExitTrade] = []
    for ev in events:
        event_day = date.fromisoformat(ev["event_date"])
        t5 = cache.shift_trading_days(event_day, 5)
        if t5 is None or t5 > as_of:
            continue
        sid = ev["stock_id"]
        entry_day = cache.shift_trading_days(event_day, entry_offset)
        if entry_day is None:
            continue
        entry = cache.price(sid, entry_day, "close")
        if entry is None or entry <= 0:
            continue

        bal = None
        if min_short_balance is not None:
            margin = cache.margin_on(entry_day).get(sid) or {}
            bal = margin.get("short_balance")
            util = margin.get("short_util")
            if bal is None:
                continue
            if bal < min_short_balance and (util is None or util < min_short_util):
                continue

        resolved = resolve_peak_or_t5_exit(
            cache, sid, entry_day, float(entry), event_day, mode=mode
        )
        if resolved is None:
            continue
        rows.append(
            PeakExitTrade(
                strategy=strategy,
                stock_id=sid,
                stock_name=ev.get("stock_name") or "",
                event_date=ev["event_date"],
                entry_date=resolved["entry_date"],
                entry_price=float(entry),
                exit_date=resolved["exit_date"],
                exit_price=resolved["exit_price"],
                exit_reason=resolved["exit_reason"],
                ret_pct=resolved["ret_pct"],
                peak_high=resolved["peak_high"],
                peak_date=resolved["peak_date"],
                t5_close=resolved["t5_close"],
                ret_hold_t5_pct=resolved["ret_hold_t5_pct"],
                short_balance=bal,
                note=f"T{entry_offset}→高點或T+5；mode={mode}",
            )
        )
    return rows


def backtest_ex_dividend(
    cache: MarketQuoteCache,
    events: list[dict[str, Any]],
    *,
    as_of: date,
) -> list[TradeRow]:
    rows: list[TradeRow] = []
    for ev in events:
        event_day = date.fromisoformat(ev["event_date"])
        if event_day > as_of:
            continue
        sid = ev["stock_id"]
        ref = ev.get("ref_price") or ev.get("reference_price")
        trade_day = cache.find_trading_day(event_day, 1)
        if trade_day is None:
            continue
        open_px = cache.price(sid, trade_day, "open")
        entry = ref if ref and ref > 0 else open_px
        if entry is None or entry <= 0:
            continue
        exits = _fill_exits(cache, sid, trade_day, entry, event_day)
        pre = ev.get("pre_close") or ev.get("pre_close_price")
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
    min_short_balance: float = 200.0,
    min_short_util: float = 0.05,
) -> list[TradeRow]:
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
    picks["cross_strategy"] = sorted(
        cross.values(),
        key=lambda x: (len(x["strategies_hit"]), x["best_win_rate"], x["best_avg_d5_return_pct"]),
        reverse=True,
    )[:30]
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
                sum(1 for r in rows if r.recovered_pre_close)
                / sum(1 for r in rows if r.recovered_pre_close is not None)
                if any(r.recovered_pre_close is not None for r in rows)
                else None
            ),
        }
        for name, rows in by_strategy.items()
    }

    picks = pick_high_winrate_stocks(by_strategy, min_trades=2, min_win_rate=0.6)

    peak_by: dict[str, list[PeakExitTrade]] = {}
    first_by: dict[str, list[PeakExitTrade]] = {}
    specs = [
        ("ex_dividend_reclaim", exdiv, None),
        ("short_cover_proxy", exdiv, 200.0),
        ("par_value_split", splits, None),
        ("cb_listing", cbs, None),
    ]
    for name, events, min_short in specs:
        peak_by[name] = backtest_t5_peak_or_hold(
            cache,
            events,
            strategy=name,
            as_of=end,
            mode="peak_high",
            min_short_balance=min_short,
        )
        first_by[name] = backtest_t5_peak_or_hold(
            cache,
            events,
            strategy=name,
            as_of=end,
            mode="first_high_close",
            min_short_balance=min_short,
        )

    peak_summary = {name: _summarize_peak(rows) for name, rows in peak_by.items()}
    first_summary = {name: _summarize_peak(rows) for name, rows in first_by.items()}

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
                "除權息樣本以證交所上市為主",
                "基準策略：固定持有至 D0/D+5/D+10",
                "新規則：一律 T-5 收盤進場；期間若有高於進場價的高點可出場，否則 T+5 收盤強制出場",
                "peak_high：摸到期間最高價出場（樂觀上界）",
                "first_high_close：首次出現高點當日收盤出場（較可執行）",
                "融券回補仍過濾進場日融券水位",
            ],
            "event_counts": {
                "ex_dividend_events": len(exdiv),
                "split_events": len(splits),
                "cb_listing_events": len(cbs),
            },
        },
        "summary": summary,
        "t5_peak_exit": {
            "peak_high": {
                "summary": peak_summary,
                "rows": {k: [asdict(r) for r in v] for k, v in peak_by.items()},
            },
            "first_high_close": {
                "summary": first_summary,
                "rows": {k: [asdict(r) for r in v] for k, v in first_by.items()},
            },
        },
        "high_winrate_picks": picks,
        "single_event_stars": single_event_stars[:40],
        "rows": {name: [asdict(r) for r in rows] for name, rows in by_strategy.items()},
    }


def render_ytd_markdown(result: dict[str, Any]) -> str:
    meta = result["meta"]
    name_map = {
        "ex_dividend_reclaim": "除權息回補",
        "short_cover_proxy": "融券回補（代理）",
        "par_value_split": "面額變更／分割",
        "cb_listing": "可轉債掛牌",
    }
    lines = [
        f"# 2026 事件驅動策略回測（{meta['start']} ~ {meta['end']}）",
        "",
        "> 本報告僅供研究，不構成投資建議。",
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
        "## 基準策略績效（固定持有）",
        "",
    ]
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
                f"中位 {block['median_return_pct']:.2f}%"
            )
        lines.append("")

    t5 = result.get("t5_peak_exit") or {}
    lines += [
        "## T-5 進場＋有高點出場／否則抱到 T+5",
        "",
        "### A. 樂觀版：摸到期間最高價出場（peak_high）",
        "",
    ]
    for key, title in name_map.items():
        block = ((t5.get("peak_high") or {}).get("summary") or {}).get(key) or {}
        lines.append(f"#### {title}")
        if not block.get("sample_size"):
            lines.append("- 無有效樣本")
            lines.append("")
            continue
        lines.append(
            f"- 樣本 {block['sample_size']}，勝率 {block['win_rate']:.1%}，"
            f"平均報酬 {block['avg_return_pct']:.2f}%，中位 {block['median_return_pct']:.2f}%"
        )
        lines.append(
            f"- 高點出場占比 {block['peak_exit_rate']:.1%}，"
            f"強制 T+5 占比 {block['forced_t5_rate']:.1%}"
        )
        if block.get("avg_hold_t5_return_pct") is not None:
            lines.append(f"- 對照：若一律抱到 T+5，平均 {block['avg_hold_t5_return_pct']:.2f}%")
        lines.append("")

    lines += ["### B. 可執行版：首次出現高點當日收盤出場（first_high_close）", ""]
    for key, title in name_map.items():
        block = ((t5.get("first_high_close") or {}).get("summary") or {}).get(key) or {}
        lines.append(f"#### {title}")
        if not block.get("sample_size"):
            lines.append("- 無有效樣本")
            lines.append("")
            continue
        lines.append(
            f"- 樣本 {block['sample_size']}，勝率 {block['win_rate']:.1%}，"
            f"平均報酬 {block['avg_return_pct']:.2f}%，中位 {block['median_return_pct']:.2f}%"
        )
        lines.append(
            f"- 高點出場占比 {block['peak_exit_rate']:.1%}，"
            f"強制 T+5 占比 {block['forced_t5_rate']:.1%}"
        )
        if block.get("avg_hold_t5_return_pct") is not None:
            lines.append(f"- 對照：若一律抱到 T+5，平均 {block['avg_hold_t5_return_pct']:.2f}%")
        lines.append("")

    lines += ["## 高勝率標的（基準策略 D+5）", ""]
    picks = result.get("high_winrate_picks") or {}
    for key, title in name_map.items():
        items = picks.get(key) or []
        lines.append(f"### {title}")
        if not items:
            lines.append("- （無）")
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

    lines.append("")
    return "\n".join(lines)
