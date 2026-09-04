from datetime import date

from event_agent.dates import parse_roc_date, to_float
from event_agent.models import CorporateEvent
from event_agent.scoring import is_likely_etf_or_bond, score_event, score_events


def test_parse_roc_date_variants():
    assert parse_roc_date("1150907") == date(2026, 9, 7)
    assert parse_roc_date("115/09/07") == date(2026, 9, 7)
    assert parse_roc_date("115年09月07日") == date(2026, 9, 7)
    assert parse_roc_date("2026-09-07") == date(2026, 9, 7)
    assert parse_roc_date("") is None


def test_to_float():
    assert to_float("0.08000000") == 0.08
    assert to_float("-") is None
    assert to_float("") is None


def test_etf_filter():
    assert is_likely_etf_or_bond("00713")
    assert is_likely_etf_or_bond("00400A")
    assert not is_likely_etf_or_bond("5314")
    assert not is_likely_etf_or_bond("2330")


def test_par_value_scores_higher_than_plain_cash():
    as_of = date(2026, 9, 1)
    split = CorporateEvent(
        stock_id="5314",
        stock_name="世紀",
        market="TPEx",
        event_type="par_value_change",
        event_date=date(2026, 9, 10),
        source="test",
        split_ratio=20.0,
        suspend_date=date(2026, 9, 2),
        resume_date=date(2026, 9, 10),
    )
    cash = CorporateEvent(
        stock_id="2330",
        stock_name="台積電",
        market="TWSE",
        event_type="cash_dividend",
        event_date=date(2026, 9, 10),
        source="test",
        cash_dividend=4.0,
        price=1000.0,
        cash_yield=0.004,
    )
    score_event(split, as_of=as_of)
    score_event(cash, as_of=as_of)
    assert split.score > cash.score
    assert split.watch is True


def test_high_stock_dividend_is_watch():
    event = CorporateEvent(
        stock_id="1234",
        stock_name="測試",
        market="TWSE",
        event_type="stock_dividend",
        event_date=date(2026, 9, 8),
        source="test",
        stock_dividend_ratio=0.55,
    )
    scored = score_events([event], as_of=date(2026, 9, 4))
    assert len(scored) == 1
    assert scored[0].grade in {"A", "B"}
    assert scored[0].watch is True
