from __future__ import annotations

from event_agent.backtest_ytd import TradeRow, _stock_stats, _summarize, pick_high_winrate_stocks


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
