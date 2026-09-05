from __future__ import annotations

from datetime import date
from pathlib import Path

from event_agent.fetchers.margin_cb import (
    fetch_convertible_bond_board_events,
    fetch_convertible_bond_listing_events,
    fetch_short_balances,
    fetch_tpex_short_cover_events,
    fetch_twse_short_cover_events,
)
from event_agent.fetchers.prices import fetch_latest_prices
from event_agent.fetchers.tpex import fetch_tpex_exright_events, fetch_tpex_par_value_events
from event_agent.fetchers.twse import fetch_twse_exright_events
from event_agent.models import CorporateEvent
from event_agent.report import write_reports
from event_agent.scoring import score_events


def collect_events(*, as_of: date | None = None) -> list[CorporateEvent]:
    as_of = as_of or date.today()
    events: list[CorporateEvent] = []
    errors: list[str] = []

    balances: dict[str, dict[str, float]] = {}
    try:
        balances = fetch_short_balances()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"short balances: {exc}")

    for name, fetcher in (
        ("TWSE ex-right", fetch_twse_exright_events),
        ("TPEx ex-right", fetch_tpex_exright_events),
        ("TPEx par-value", fetch_tpex_par_value_events),
        (
            "TWSE short-cover",
            lambda: fetch_twse_short_cover_events(as_of=as_of, balances=balances),
        ),
        (
            "TPEx short-cover",
            lambda: fetch_tpex_short_cover_events(as_of=as_of, balances=balances),
        ),
        (
            "CB listing",
            lambda: fetch_convertible_bond_listing_events(as_of=as_of),
        ),
        (
            "CB board/material",
            lambda: fetch_convertible_bond_board_events(as_of=as_of),
        ),
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
            event.cb_code or "",
            round(event.issue_amount or 0, 2),
            event.reason or "",
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
    events = collect_events(as_of=as_of)
    prices: dict[str, float] = {}
    if include_prices:
        try:
            prices = fetch_latest_prices()
        except Exception:  # noqa: BLE001
            prices = {}
    scored = score_events(events, prices=prices, as_of=as_of)
    paths = write_reports(scored, output_dir, as_of=as_of)

    by_type: dict[str, int] = {}
    for event in scored:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1

    return {
        "as_of": as_of.isoformat(),
        "raw_count": len(events),
        "scored_count": len(scored),
        "watch_count": sum(1 for e in scored if e.watch),
        "by_type": by_type,
        "paths": {k: str(v) for k, v in paths.items()},
        "top": [e.to_dict() for e in scored[:20]],
        "top_short_cover": [
            e.to_dict()
            for e in scored
            if e.event_type == "forced_short_cover" and e.watch
        ][:10],
        "top_cb": [
            e.to_dict()
            for e in scored
            if e.event_type in {"convertible_bond_board", "convertible_bond_listing"}
            and e.watch
        ][:10],
    }
