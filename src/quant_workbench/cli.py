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
from quant_workbench.jobs.ingestion import (
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
        history_path = write_health_report(store, report)
        report["history_path"] = str(history_path)
        if not args.no_alerts:
            report["alerts"] = notify_health_report(store, state, report)
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
