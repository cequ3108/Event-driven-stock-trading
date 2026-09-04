from datetime import date

from event_agent.classify import classify_ex_event
from event_agent.report import render_markdown_report
from event_agent.models import CorporateEvent


def test_classify_ex_event():
    assert classify_ex_event("息", 0, 1.2, 0) == "cash_dividend"
    assert classify_ex_event("權", 0.1, 0, 0) == "stock_dividend"
    assert classify_ex_event("權息", 0.1, 0.5, 0) == "stock_and_cash_dividend"
    assert classify_ex_event("除權", 0, 0, 0.1) == "rights_issue"


def test_render_markdown_contains_watch_section():
    event = CorporateEvent(
        stock_id="5314",
        stock_name="世紀",
        market="TPEx",
        event_type="par_value_change",
        event_date=date(2026, 9, 10),
        source="test",
        split_ratio=20,
        score=88,
        grade="A",
        watch=True,
        reasons=["面額變更／分割：恢復交易日常見投機熱度"],
    )
    md = render_markdown_report([event], as_of=date(2026, 9, 4))
    assert "高關注清單" in md
    assert "5314" in md
    assert "面額變更" in md
