from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quant_workbench.ai.openai_client import OpenAIResearchClient
from quant_workbench.backtest.engine import BacktestEngine
from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument
from quant_workbench.derivatives.options import OptionType, black_scholes, implied_volatility
from quant_workbench.events.pit import ingest_events, run_due_events
from quant_workbench.fundamentals.pit import ingest_fundamentals, run_due_fundamentals
from quant_workbench.jobs.ingestion import (
    backfill_daily_bars,
    ingest_cached_bars,
    ingest_incremental_bars,
    snapshot_options,
)
from quant_workbench.jobs.orchestrator import run_due_jobs
from quant_workbench.ops.alert import (
    notify_health_report,
    notify_pipeline_report,
    send_test_message,
)
from quant_workbench.ops.backup import create_backup_snapshot, verify_backup_snapshot
from quant_workbench.ops.health import build_health_report, write_health_report
from quant_workbench.reports.market_brief import build_market_brief, run_due_market_briefs
from quant_workbench.store import DatasetStore, StateStore
from quant_workbench.strategy.momentum import CrossSectionalMomentum

DEFAULT_LAKE = Path("data/lake")


def _demo_bars() -> list[Bar]:
    instruments = [
        Instrument("AAA", Exchange.NASDAQ, AssetClass.EQUITY, Currency.USD),
        Instrument(
            "600000", Exchange.SSE, AssetClass.EQUITY, Currency.CNY, lot_size=Decimal("100")
        ),
    ]
    result: list[Bar] = []
    start = datetime(2025, 1, 2)
    for day in range(60):
        timestamp = start + timedelta(days=day)
        if timestamp.weekday() >= 5:
            continue
        for index, instrument in enumerate(instruments):
            base = Decimal("100") + Decimal(day) * (
                Decimal("0.35") if index == 0 else Decimal("0.18")
            )
            wave = Decimal((day % 7) - 3) * Decimal("0.12")
            close = base + wave
            result.append(
                Bar(
                    instrument,
                    timestamp,
                    base,
                    max(base, close) + Decimal("0.5"),
                    min(base, close) - Decimal("0.5"),
                    close,
                    Decimal("100000"),
                )
            )
    return result


def command_demo(_: argparse.Namespace) -> None:
    result = BacktestEngine(rebalance_every=5).run(
        _demo_bars(), CrossSectionalMomentum(lookback=10, top_n=1)
    )
    print(
        json.dumps(
            {"metrics": result.metrics, "fills": len(result.fills)}, ensure_ascii=False, indent=2
        )
    )


def command_option(args: argparse.Namespace) -> None:
    option_type = OptionType(args.type)
    analytics = black_scholes(
        args.spot, args.strike, args.years, args.rate, args.vol, option_type, args.dividend
    )
    result = (
        analytics.__dict__
        if hasattr(analytics, "__dict__")
        else {name: getattr(analytics, name) for name in analytics.__slots__}
    )
    if args.market_price:
        result["implied_volatility"] = implied_volatility(
            args.market_price,
            args.spot,
            args.strike,
            args.years,
            args.rate,
            option_type,
            args.dividend,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_analyze(args: argparse.Namespace) -> None:
    text = open(args.file, encoding="utf-8").read()
    result = OpenAIResearchClient(model=args.model).analyze(
        subject=args.subject,
        task=args.task,
        materials=[{"source": args.file, "content": text}],
    )
    output = {name: getattr(result, name) for name in result.__slots__}
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _open_stores(root: str) -> tuple[DatasetStore, StateStore]:
    dataset_store = DatasetStore(Path(root))
    state_store = StateStore(dataset_store.root / "state" / "control.db")
    return dataset_store, state_store


def command_ops_init(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        print(
            json.dumps(
                {"initialized": True, "inventory": store.inventory()}, ensure_ascii=False, indent=2
            )
        )
    finally:
        state.close()


def command_ingest_cache(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    market = args.market
    source = "yfinance" if market == "us" else "baostock"
    bars_directory = Path(
        args.bars_directory or f"data/cache/strategy_run_2026-09-13/bars/{market}"
    )
    universe_path = Path(args.universe or f"data/cache/sector_probe/universe_{market}.csv")
    try:
        lease, result = ingest_cached_bars(
            store,
            state,
            market,
            bars_directory,
            universe_path,
            source,
            date.fromisoformat(args.run_date) if args.run_date else date.today(),
        )
        output = {
            "job": "ingest_cached_bars",
            "market": market,
            "acquired": lease.acquired,
            "reason": lease.reason,
            "result": (
                {
                    "path": str(result.path),
                    "rows": result.row_count,
                    "sha256": result.sha256,
                }
                if result
                else None
            ),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        state.close()


def command_snapshot_options(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        lease, result, failures = snapshot_options(
            store,
            state,
            args.symbols,
            date.fromisoformat(args.run_date) if args.run_date else date.today(),
        )
        output = {
            "job": "snapshot_options",
            "symbols": args.symbols,
            "acquired": lease.acquired,
            "reason": lease.reason,
            "failures": failures,
            "result": (
                {
                    "path": str(result.path),
                    "rows": result.row_count,
                    "sha256": result.sha256,
                }
                if result
                else None
            ),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        state.close()


def command_ingest_daily(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    market = args.market
    universe_path = Path(args.universe or f"data/cache/sector_probe/universe_{market}.csv")
    try:
        lease, result, details = ingest_incremental_bars(
            store,
            state,
            market,
            universe_path,
            date.fromisoformat(args.as_of) if args.as_of else date.today(),
            args.minimum_coverage,
        )
        output = {
            "job": "ingest_incremental_bars",
            "market": market,
            "acquired": lease.acquired,
            "reason": lease.reason,
            "details": details,
            "result": (
                {
                    "path": str(result.path),
                    "rows": result.row_count,
                    "sha256": result.sha256,
                }
                if result
                else None
            ),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        state.close()


def command_backfill_daily(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    market = args.market
    universe_path = Path(args.universe or f"data/cache/sector_probe/universe_{market}.csv")
    try:
        lease, result, summary = backfill_daily_bars(
            store,
            state,
            market,
            universe_path,
            date.fromisoformat(args.start),
            date.fromisoformat(args.run_date) if args.run_date else date.today(),
            end=date.fromisoformat(args.end) if args.end else None,
            minimum_coverage=args.minimum_coverage,
            force=args.force,
        )
        output = {
            "job": "backfill_daily_bars",
            "market": market,
            "acquired": lease.acquired,
            "reason": lease.reason,
            "result": (
                {"path": str(result.path), "rows": result.row_count, "sha256": result.sha256}
                if result
                else None
            ),
            "summary": summary,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    finally:
        state.close()


def command_ingest_fundamentals(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        result = ingest_fundamentals(
            store,
            state,
            args.market,
            Path(args.universe or f"data/cache/sector_probe/universe_{args.market}.csv"),
            date.fromisoformat(args.run_date) if args.run_date else date.today(),
            symbols=args.symbols,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        state.close()


def command_fundamentals_due(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        result = run_due_fundamentals(
            store,
            state,
            Path(args.universe_directory),
            _parse_now(args.now),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.strict and result["status"] != "ok":
            raise SystemExit(2)
    finally:
        state.close()


def command_ingest_events(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        result = ingest_events(
            store,
            state,
            args.market,
            Path(args.universe or f"data/cache/sector_probe/universe_{args.market}.csv"),
            date.fromisoformat(args.run_date) if args.run_date else date.today(),
            symbols=args.symbols,
            limit=args.limit,
            lookback_days=args.lookback_days,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        state.close()


def command_events_due(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        result = run_due_events(
            store,
            state,
            Path(args.universe_directory),
            _parse_now(args.now),
            lookback_days=args.lookback_days,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.strict and result["status"] != "ok":
            raise SystemExit(2)
    finally:
        state.close()


def command_ops_status(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        print(
            json.dumps(
                {"inventory": store.inventory(), "state": state.status(args.limit)},
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        state.close()


def command_ops_backup(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        run_date = date.fromisoformat(args.run_date) if args.run_date else date.today()
        path, snapshot = create_backup_snapshot(store, state, run_date)
        print(
            json.dumps(
                {
                    "snapshot_manifest": str(path),
                    "datasets": len(snapshot["datasets"]),
                    "raw_files": len(snapshot["raw_files"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        state.close()


def command_ops_verify_backup(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        path = Path(args.manifest)
        if not path.is_absolute():
            path = store.root / path
        report = verify_backup_snapshot(store, path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict and report["status"] != "ok":
            raise SystemExit(2)
    finally:
        state.close()


def command_watchdog(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        report = build_health_report(
            store,
            state,
            minimum_symbol_coverage=args.minimum_coverage,
            verify_checksums=args.full,
        )
        if not args.no_alerts:
            report["alerts"] = notify_health_report(store, state, report)
        history_path = write_health_report(store, report)
        report["history_path"] = str(history_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict and report["status"] == "error":
            raise SystemExit(2)
    finally:
        state.close()


def command_run_due(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        report = run_due_jobs(
            store,
            state,
            Path(args.universe_directory),
            args.option_symbols,
            now,
        )
        if not args.no_alerts:
            report["alerts"] = notify_pipeline_report(store, report)
        store.write_json("health/last-pipeline-run.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.strict and report["status"] != "ok":
            raise SystemExit(2)
    finally:
        state.close()


def command_alert_test(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        result = send_test_message(store, args.text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["ok"]:
            raise SystemExit(2)
    finally:
        state.close()


def _parse_now(value: str | None) -> datetime:
    current = datetime.fromisoformat(value) if value else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current


def command_market_report(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        report, artifacts = build_market_brief(
            store,
            args.market,
            args.stage,
            _parse_now(args.now),
            fetch_live=not args.no_live,
            send=not args.no_send,
            generate_gpt=not args.no_gpt,
            gpt_model=args.gpt_model,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "market": report["market"],
                    "stage": report["stage"],
                    "sectors": len(report["sectors"]),
                    "stocks": len(report["stocks"]),
                    "data_mode": report["data_mode"],
                    "json_path": str(artifacts.json_path),
                    "markdown_path": str(artifacts.markdown_path),
                    "gpt_status": report["gpt_analysis"]["status"],
                    "gpt_json_path": str(artifacts.gpt_json_path),
                    "gpt_markdown_path": str(artifacts.gpt_markdown_path),
                    "sent": artifacts.sent,
                    "send_error": artifacts.send_error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if args.strict and (len(report["sectors"]) != 5 or len(report["stocks"]) != 15):
            raise SystemExit(2)
    finally:
        state.close()


def command_market_reports_due(args: argparse.Namespace) -> None:
    store, state = _open_stores(args.root)
    try:
        result = run_due_market_briefs(
            store,
            _parse_now(args.now),
            fetch_live=not args.no_live,
            send=not args.no_send,
            generate_gpt=not args.no_gpt,
            gpt_model=args.gpt_model,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.strict and result["status"] != "ok":
            raise SystemExit(2)
    finally:
        state.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-workbench")
    subparsers = parser.add_subparsers(required=True)
    demo = subparsers.add_parser("demo-backtest", help="运行内置双市场日频回测")
    demo.set_defaults(func=command_demo)

    option = subparsers.add_parser("option", help="计算欧式期权价格与 Greeks")
    option.add_argument("--type", choices=["call", "put"], required=True)
    option.add_argument("--spot", type=float, required=True)
    option.add_argument("--strike", type=float, required=True)
    option.add_argument("--years", type=float, required=True)
    option.add_argument("--rate", type=float, default=0.03)
    option.add_argument("--vol", type=float, default=0.25)
    option.add_argument("--dividend", type=float, default=0.0)
    option.add_argument("--market-price", type=float)
    option.set_defaults(func=command_option)

    analyze = subparsers.add_parser("analyze", help="调用 OpenAI 做有证据约束的材料分析")
    analyze.add_argument("--subject", required=True)
    analyze.add_argument("--task", default="财报解读与利好利空判断")
    analyze.add_argument("--file", required=True)
    analyze.add_argument("--model")
    analyze.set_defaults(func=command_analyze)

    ops_init = subparsers.add_parser("ops-init", help="初始化本地数据湖和作业状态库")
    ops_init.add_argument("--root", default=str(DEFAULT_LAKE))
    ops_init.set_defaults(func=command_ops_init)

    ingest_cache = subparsers.add_parser("ingest-cache", help="幂等迁移已验证的历史日线到 Parquet")
    ingest_cache.add_argument("--market", choices=["us", "cn"], required=True)
    ingest_cache.add_argument("--root", default=str(DEFAULT_LAKE))
    ingest_cache.add_argument("--bars-directory")
    ingest_cache.add_argument("--universe")
    ingest_cache.add_argument("--run-date")
    ingest_cache.set_defaults(func=command_ingest_cache)

    daily = subparsers.add_parser("ingest-daily", help="按交易日历增量更新日线")
    daily.add_argument("--market", choices=["us", "cn"], required=True)
    daily.add_argument("--root", default=str(DEFAULT_LAKE))
    daily.add_argument("--universe")
    daily.add_argument("--as-of", help="更新到该日期之前的最新交易日，默认今天")
    daily.add_argument("--minimum-coverage", type=float, default=0.98)
    daily.set_defaults(func=command_ingest_daily)

    backfill = subparsers.add_parser(
        "backfill-daily", help="重新拉取全量原始日线（含复权因子）并归档旧的复权价分区"
    )
    backfill.add_argument("--market", choices=["us", "cn"], required=True)
    backfill.add_argument("--root", default=str(DEFAULT_LAKE))
    backfill.add_argument("--universe")
    backfill.add_argument("--start", default="2023-08-15")
    backfill.add_argument("--end", help="默认为当前全局水位")
    backfill.add_argument("--run-date")
    backfill.add_argument("--minimum-coverage", type=float, default=0.98)
    backfill.add_argument("--force", action="store_true", help="即使同参数已成功过也重跑")
    backfill.set_defaults(func=command_backfill_daily)

    fundamentals = subparsers.add_parser(
        "ingest-fundamentals", help="采集带披露时间的美股/A股季度基本面"
    )
    fundamentals.add_argument("--market", choices=["us", "cn"], required=True)
    fundamentals.add_argument("--root", default=str(DEFAULT_LAKE))
    fundamentals.add_argument("--universe")
    fundamentals.add_argument("--symbols", nargs="+")
    fundamentals.add_argument("--limit", type=int)
    fundamentals.add_argument("--run-date")
    fundamentals.set_defaults(func=command_ingest_fundamentals)

    fundamentals_due = subparsers.add_parser(
        "fundamentals-due", help="按市场收盘时间刷新季度基本面"
    )
    fundamentals_due.add_argument("--root", default=str(DEFAULT_LAKE))
    fundamentals_due.add_argument(
        "--universe-directory", default="data/cache/sector_probe"
    )
    fundamentals_due.add_argument("--now", help="测试用ISO时间；无时区时按UTC")
    fundamentals_due.add_argument("--strict", action="store_true")
    fundamentals_due.set_defaults(func=command_fundamentals_due)

    events = subparsers.add_parser(
        "ingest-events", help="采集带发布时间和原文链接的美股新闻/A股公告"
    )
    events.add_argument("--market", choices=["us", "cn"], required=True)
    events.add_argument("--root", default=str(DEFAULT_LAKE))
    events.add_argument("--universe")
    events.add_argument("--symbols", nargs="+")
    events.add_argument("--limit", type=int)
    events.add_argument("--lookback-days", type=int, default=14)
    events.add_argument("--run-date")
    events.set_defaults(func=command_ingest_events)

    events_due = subparsers.add_parser("events-due", help="按市场收盘时间刷新消息事件")
    events_due.add_argument("--root", default=str(DEFAULT_LAKE))
    events_due.add_argument(
        "--universe-directory", default="data/cache/sector_probe"
    )
    events_due.add_argument("--lookback-days", type=int, default=14)
    events_due.add_argument("--now", help="测试用ISO时间；无时区时按UTC")
    events_due.add_argument("--strict", action="store_true")
    events_due.set_defaults(func=command_events_due)

    option_snapshot = subparsers.add_parser("snapshot-options", help="保存研究用美股期权链每日快照")
    option_snapshot.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    option_snapshot.add_argument("--root", default=str(DEFAULT_LAKE))
    option_snapshot.add_argument("--run-date")
    option_snapshot.set_defaults(func=command_snapshot_options)

    ops_status = subparsers.add_parser("ops-status", help="显示数据资产和最近作业状态")
    ops_status.add_argument("--root", default=str(DEFAULT_LAKE))
    ops_status.add_argument("--limit", type=int, default=20)
    ops_status.set_defaults(func=command_ops_status)

    ops_backup = subparsers.add_parser("ops-backup", help="创建 SQLite 与数据文件一致性清单")
    ops_backup.add_argument("--root", default=str(DEFAULT_LAKE))
    ops_backup.add_argument("--run-date")
    ops_backup.set_defaults(func=command_ops_backup)

    verify_backup = subparsers.add_parser("ops-verify-backup", help="校验备份数据库和数据清单")
    verify_backup.add_argument("--root", default=str(DEFAULT_LAKE))
    verify_backup.add_argument("--manifest", required=True)
    verify_backup.add_argument("--strict", action="store_true")
    verify_backup.set_defaults(func=command_ops_verify_backup)

    watchdog = subparsers.add_parser("watchdog", help="检查数据新鲜度、覆盖率和文件完整性")
    watchdog.add_argument("--root", default=str(DEFAULT_LAKE))
    watchdog.add_argument("--minimum-coverage", type=float, default=0.98)
    watchdog.add_argument("--full", action="store_true", help="同时验证全部 Parquet 校验和")
    watchdog.add_argument("--strict", action="store_true", help="发现错误时返回非零退出码")
    watchdog.add_argument("--no-alerts", action="store_true", help="不推送 Telegram 告警")
    watchdog.set_defaults(func=command_watchdog)

    alert_test = subparsers.add_parser("alert-test", help="向 Telegram 发送一条测试告警")
    alert_test.add_argument("--root", default=str(DEFAULT_LAKE))
    alert_test.add_argument("--text", default=None, help="自定义消息内容")
    alert_test.set_defaults(func=command_alert_test)

    market_report = subparsers.add_parser(
        "market-report", help="生成并推送指定市场的盘前、盘中或盘后热度报告"
    )
    market_report.add_argument("--root", default=str(DEFAULT_LAKE))
    market_report.add_argument("--market", choices=["us", "cn"], required=True)
    market_report.add_argument(
        "--stage", choices=["premarket", "midday", "postmarket"], required=True
    )
    market_report.add_argument("--now", help="测试用ISO时间；无时区时按UTC")
    market_report.add_argument("--no-live", action="store_true", help="只使用已完成日线")
    market_report.add_argument("--no-send", action="store_true", help="只保存本地，不推送")
    market_report.add_argument("--no-gpt", action="store_true", help="不调用 GPT 解释层")
    market_report.add_argument("--gpt-model", help="覆盖 OPENAI_REPORT_MODEL")
    market_report.add_argument("--strict", action="store_true")
    market_report.set_defaults(func=command_market_report)

    reports_due = subparsers.add_parser(
        "market-reports-due", help="补跑当天已到时点且尚未生成的三段市场报告"
    )
    reports_due.add_argument("--root", default=str(DEFAULT_LAKE))
    reports_due.add_argument("--now", help="测试用ISO时间；无时区时按UTC")
    reports_due.add_argument("--no-live", action="store_true", help="只使用已完成日线")
    reports_due.add_argument("--no-send", action="store_true", help="只保存本地，不推送")
    reports_due.add_argument("--no-gpt", action="store_true", help="不调用 GPT 解释层")
    reports_due.add_argument("--gpt-model", help="覆盖 OPENAI_REPORT_MODEL")
    reports_due.add_argument("--strict", action="store_true")
    reports_due.set_defaults(func=command_market_reports_due)

    run_due = subparsers.add_parser("run-due", help="按各市场已完成交易日运行到期作业")
    run_due.add_argument("--root", default=str(DEFAULT_LAKE))
    run_due.add_argument(
        "--universe-directory", default="data/cache/sector_probe", help="股票池CSV目录"
    )
    run_due.add_argument("--option-symbols", nargs="+", default=["SPY", "QQQ"])
    run_due.add_argument("--now", help="测试用ISO时间；无时区时按UTC")
    run_due.add_argument("--strict", action="store_true")
    run_due.add_argument("--no-alerts", action="store_true", help="不推送 Telegram 告警")
    run_due.set_defaults(func=command_run_due)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
