#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from event_agent.agent import run_monitor
from event_agent.backtest import evaluate_par_value_resume_edge, render_backtest_markdown
from event_agent.backtest_ytd import render_ytd_markdown, run_ytd_backtest


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="台股事件驅動監控 agent（除權息／面額變更提醒與評分）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    monitor = sub.add_parser("monitor", help="抓取未來事件並產生關注清單")
    monitor.add_argument("--output-dir", default="output")
    monitor.add_argument("--as-of", default=None, help="YYYY-MM-DD，預設今天")
    monitor.add_argument("--no-prices", action="store_true", help="不抓收盤價（較快）")
    monitor.add_argument("--json", action="store_true", help="stdout 輸出 JSON 摘要")

    backtest = sub.add_parser("backtest-par-value", help="回測歷史面額變更恢復日表現")
    backtest.add_argument("--start-date", default="2019-01-01")
    backtest.add_argument("--output-dir", default="output")
    backtest.add_argument("--json", action="store_true")

    ytd = sub.add_parser("backtest-2026", help="回測今年三種事件策略並挑高勝率標的")
    ytd.add_argument("--start-date", default=None, help="YYYY-MM-DD，預設今年元旦")
    ytd.add_argument("--end-date", default=None, help="YYYY-MM-DD，預設今天")
    ytd.add_argument("--output-dir", default="output")
    ytd.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "monitor":
        result = run_monitor(
            output_dir=args.output_dir,
            as_of=_parse_date(args.as_of),
            include_prices=not args.no_prices,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"監控完成：事件 {result['scored_count']} 筆，"
                f"關注 {result['watch_count']} 筆 → {result['paths']['latest_markdown']}"
            )
            if result["top"]:
                print("\nTop 關注：")
                for row in result["top"][:8]:
                    if not row.get("watch"):
                        continue
                    print(
                        f"  [{row['grade']}] {row['score']:.1f} "
                        f"{row['stock_id']} {row['stock_name']} "
                        f"{row['event_type']} @ {row['event_date']}"
                    )
        return 0

    if args.command == "backtest-par-value":
        result = evaluate_par_value_resume_edge(start_date=args.start_date)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / "backtest_par_value.md"
        json_path = out / "backtest_par_value.json"
        md_path.write_text(render_backtest_markdown(result), encoding="utf-8")
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps(result.get("summary"), ensure_ascii=False, indent=2))
        else:
            summary = result.get("summary") or {}
            print(
                f"回測完成：樣本 {summary.get('sample_size', 0)}，"
                f"收盤>參考價 {summary.get('pct_close_above_ref', 0):.0%} → {md_path}"
            )
        return 0

    if args.command == "backtest-2026":
        result = run_ytd_backtest(
            start=_parse_date(args.start_date),
            end=_parse_date(args.end_date),
        )
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = (args.end_date or date.today().isoformat()).replace("-", "")
        md_path = out / f"backtest_2026_{stamp}.md"
        json_path = out / f"backtest_2026_{stamp}.json"
        latest_md = out / "backtest_2026_latest.md"
        latest_json = out / "backtest_2026_latest.json"
        md = render_ytd_markdown(result)
        md_path.write_text(md, encoding="utf-8")
        latest_md.write_text(md, encoding="utf-8")
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        json_path.write_text(payload, encoding="utf-8")
        latest_json.write_text(payload, encoding="utf-8")
        if args.json:
            print(json.dumps(result.get("summary"), ensure_ascii=False, indent=2))
        else:
            print(f"2026 回測完成 → {md_path}")
            for key, title in (
                ("ex_dividend_reclaim", "除權息"),
                ("short_cover_proxy", "融券回補"),
                ("par_value_split", "面額變更"),
                ("cb_listing", "可轉債"),
            ):
                d5 = ((result.get("summary") or {}).get(key) or {}).get("d5") or {}
                if not d5.get("sample_size"):
                    print(f"  {title}：無樣本")
                    continue
                print(
                    f"  {title}：n={d5['sample_size']} 勝率={d5['win_rate']:.0%} "
                    f"平均D+5={d5['avg_return_pct']:.2f}%"
                )
            picks = (result.get("high_winrate_picks") or {}).get("cross_strategy") or []
            if picks:
                print("\n高勝率綜合觀察（前8）：")
                for it in picks[:8]:
                    print(
                        f"  {it['stock_id']} {it['stock_name']} "
                        f"策略命中={len(it['strategies_hit'])} "
                        f"勝率={it['best_win_rate']:.0%} "
                        f"D+5均={it['best_avg_d5_return_pct']:.2f}%"
                    )
        return 0

    parser.error(f"未知命令：{args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
