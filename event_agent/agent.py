from __future__ import annotations

from datetime import date
from pathlib import Path

from event_agent.fetchers.prices import fetch_latest_prices
from event_agent.fetchers.tpex import fetch_tpex_exright_events, fetch_tpex_par_value_events
from event_agent.fetchers.twse import fetch_twse_exright_events
from event_agent.models import CorporateEvent
from event_agent.report import write_reports
from event_agent.scoring import score_events


def collect_events() -> list[CorporateEvent]:
    events: list[CorporateEvent] = []
    errors: list[str] = []
    for name, fetcher in (
        ("TWSE ex-right", fetch_twse_exright_events),
        ("TPEx ex-right", fetch_tpex_exright_events),
        ("TPEx par-value", fetch_tpex_par_value_events),
    ):
        try:
            events.extend(fetcher())
        except Exception as exc:  # noqa: BLE001 - 聚合多來源，單一路徑失敗不應整批中斷
            errors.append(f"{name}: {exc}")
    if not events and errors:
        raise RuntimeError("所有事件來源皆失敗：\n" + "\n".join(errors))
    return _dedupe(events)


def _dedupe(events: list[CorporateEvent]) -> list[CorporateEvent]:
    seen: set[tuple] = set()
    unique: list[CorporateEvent] = []
    for event in events:
        key = (
            event.stock_id,
            event.event_type,
            event.event_date.isoformat(),
            round(event.cash_dividend or 0, 6),
            round(event.stock_dividend_ratio or 0, 8),
            round(event.split_ratio or 0, 6),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def run_monitor(
    *,
    output_dir: str | Path = "output",
    as_of: date | None = None,
    include_prices: bool = True,
) -> dict:
    as_of = as_of or date.today()
    events = collect_events()
    prices: dict[str, float] = {}
    if include_prices:
        try:
            prices = fetch_latest_prices()
        except Exception:  # noqa: BLE001
            prices = {}
    scored = score_events(events, prices=prices, as_of=as_of)
    paths = write_reports(scored, output_dir, as_of=as_of)
    return {
        "as_of": as_of.isoformat(),
        "raw_count": len(events),
        "scored_count": len(scored),
        "watch_count": sum(1 for e in scored if e.watch),
        "paths": {k: str(v) for k, v in paths.items()},
        "top": [e.to_dict() for e in scored[:15]],
    }
