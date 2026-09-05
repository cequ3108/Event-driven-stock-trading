from __future__ import annotations

from datetime import date, timedelta

from event_agent.backtest_ytd import (
    TradeRow,
    _stock_stats,
    _summarize,
    pick_high_winrate_stocks,
    resolve_peak_or_t5_exit,
)


def _row(**kwargs) -> TradeRow:
    base = dict(
        strategy="ex_dividend_reclaim",
        stock_id="2330",
        stock_name="台積電",
        event_date="2026-01-01",
        entry_date="2026-01-01",
        entry_price=100.0,
    )
    base.update(kwargs)
    return TradeRow(**base)


def test_summarize_win_rate():
    rows = [
        _row(ret_d5_pct=2.0),
        _row(ret_d5_pct=-1.0),
        _row(ret_d5_pct=3.0),
        _row(ret_d5_pct=None),
    ]
    s = _summarize(rows, "d5")
    assert s["sample_size"] == 3
    assert abs(s["win_rate"] - 2 / 3) < 1e-9


def test_stock_stats_requires_min_trades():
    rows = [
        _row(stock_id="1111", ret_d5_pct=1.0),
        _row(stock_id="2222", ret_d5_pct=1.0),
        _row(stock_id="2222", ret_d5_pct=2.0),
    ]
    ranked = _stock_stats(rows, min_trades=2)
    assert len(ranked) == 1
    assert ranked[0]["stock_id"] == "2222"
    assert ranked[0]["win_rate"] == 1.0


def test_pick_high_winrate_filters():
    by_strategy = {
        "ex_dividend_reclaim": [
            _row(stock_id="1111", stock_name="甲", ret_d5_pct=1.0),
            _row(stock_id="1111", stock_name="甲", ret_d5_pct=2.0),
            _row(stock_id="3333", stock_name="乙", ret_d5_pct=-1.0),
            _row(stock_id="3333", stock_name="乙", ret_d5_pct=1.0),
        ]
    }
    picks = pick_high_winrate_stocks(by_strategy, min_trades=2, min_win_rate=0.6)
    ids = {x["stock_id"] for x in picks["ex_dividend_reclaim"]}
    assert "1111" in ids
    assert "3333" not in ids


class _FakeCache:
    """簡易假行情：weekday 皆視為交易日。"""

    def __init__(self, bars: dict[date, dict[str, float]]):
        self.bars = bars

    def shift_trading_days(self, start: date, n: int) -> date | None:
        if n == 0:
            return start if start.weekday() < 5 else None
        step = 1 if n > 0 else -1
        left = abs(n)
        d = start
        while left > 0:
            d = d + timedelta(days=step)
            if d.weekday() < 5:
                left -= 1
            if abs((d - start).days) > 60:
                return None
        return d

    def price(self, stock_id: str, d: date, field: str = "close") -> float | None:
        bar = self.bars.get(d)
        if not bar:
            return None
        return bar.get(field)


def test_peak_high_exit_when_high_exists():
    # entry Fri 6/5 close=100; event Fri 6/12; T+5 = Fri 6/19
    entry = date(2026, 6, 5)
    event = date(2026, 6, 12)
    bars = {
        date(2026, 6, 8): {"high": 101.0, "close": 100.5},  # first high
        date(2026, 6, 9): {"high": 108.0, "close": 106.0},  # peak
        date(2026, 6, 10): {"high": 104.0, "close": 103.0},
        date(2026, 6, 11): {"high": 102.0, "close": 101.0},
        date(2026, 6, 12): {"high": 101.0, "close": 100.0},
        date(2026, 6, 15): {"high": 99.0, "close": 98.0},
        date(2026, 6, 16): {"high": 98.0, "close": 97.0},
        date(2026, 6, 17): {"high": 97.0, "close": 96.0},
        date(2026, 6, 18): {"high": 96.0, "close": 95.0},
        date(2026, 6, 19): {"high": 95.0, "close": 94.0},
    }
    cache = _FakeCache(bars)
    peak = resolve_peak_or_t5_exit(cache, "2330", entry, 100.0, event, mode="peak_high")
    assert peak is not None
    assert peak["exit_reason"] == "peak_high"
    assert peak["exit_price"] == 108.0
    assert abs(peak["ret_pct"] - 8.0) < 1e-9

    first = resolve_peak_or_t5_exit(cache, "2330", entry, 100.0, event, mode="first_high_close")
    assert first is not None
    assert first["exit_reason"] == "first_high_close"
    assert first["exit_price"] == 100.5


def test_forced_t5_when_no_high():
    entry = date(2026, 6, 5)
    event = date(2026, 6, 12)
    bars = {
        date(2026, 6, 8): {"high": 99.0, "close": 98.0},
        date(2026, 6, 9): {"high": 98.0, "close": 97.0},
        date(2026, 6, 10): {"high": 97.0, "close": 96.0},
        date(2026, 6, 11): {"high": 96.0, "close": 95.0},
        date(2026, 6, 12): {"high": 95.0, "close": 94.0},
        date(2026, 6, 15): {"high": 94.0, "close": 93.0},
        date(2026, 6, 16): {"high": 93.0, "close": 92.0},
        date(2026, 6, 17): {"high": 92.0, "close": 91.0},
        date(2026, 6, 18): {"high": 91.0, "close": 90.0},
        date(2026, 6, 19): {"high": 90.0, "close": 89.0},
    }
    cache = _FakeCache(bars)
    out = resolve_peak_or_t5_exit(cache, "2330", entry, 100.0, event, mode="peak_high")
    assert out is not None
    assert out["exit_reason"] == "forced_t5"
    assert out["exit_price"] == 89.0
    assert out["ret_pct"] < 0
