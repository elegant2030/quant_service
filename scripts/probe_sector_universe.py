"""Probe at least 200 symbols per market with sector-balanced universes and resumable cache."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import sleep
from typing import Any, Sequence

from quant_workbench.core.models import AssetClass, Bar, Currency, Instrument
from quant_workbench.data.baostock_provider import BaoStockProvider
from quant_workbench.data.universe import UniverseBuildResult, UniverseMember
from quant_workbench.data.yfinance_provider import YFinanceProvider


def summarize(bars: Sequence[Bar], expected_start: datetime) -> dict[str, Any]:
    return {
        "rows": len(bars),
        "first": bars[0].timestamp.date().isoformat() if bars else None,
        "last": bars[-1].timestamp.date().isoformat() if bars else None,
        "stale": bool(bars and bars[-1].timestamp < datetime.now() - timedelta(days=7)),
        "short_history": bool(bars and bars[0].timestamp > expected_start + timedelta(days=14)),
        "zero_volume_rows": sum(bar.volume == 0 for bar in bars),
        "invalid_ohlc_rows": sum(
            bar.low > min(bar.open, bar.close)
            or bar.high < max(bar.open, bar.close)
            or min(bar.open, bar.high, bar.low, bar.close) <= 0
            for bar in bars
        ),
    }


def write_universe(path: Path, universe: UniverseBuildResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["symbol", "name", "exchange", "sector", "market_cap"]
        )
        writer.writeheader()
        for member in universe.members:
            writer.writerow(
                {
                    "symbol": member.symbol,
                    "name": member.name,
                    "exchange": member.exchange.value,
                    "sector": member.sector,
                    "market_cap": str(member.market_cap) if member.market_cap is not None else "",
                }
            )


def write_bars(path: Path, bars: Sequence[Bar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "previous_close"])
        for bar in bars:
            writer.writerow(
                [
                    bar.timestamp.isoformat(),
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.previous_close or "",
                ]
            )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"symbols": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def instrument(member: UniverseMember) -> Instrument:
    currency = Currency.USD if member.exchange.value in {"NASDAQ", "NYSE", "AMEX"} else Currency.CNY
    lot = Decimal("1") if currency == Currency.USD else Decimal("100")
    return Instrument(member.symbol, member.exchange, AssetClass.EQUITY, currency, lot_size=lot)


def finalize(state: dict[str, Any], universe: UniverseBuildResult) -> None:
    active_symbols = {member.symbol for member in universe.members}
    state["symbols"] = {
        symbol: value for symbol, value in state["symbols"].items() if symbol in active_symbols
    }
    values = state["symbols"].values()
    successes = [value for value in values if value.get("status") == "ok"]
    state["summary"] = {
        "universe_size": len(universe.members),
        "requested_size": universe.requested_size,
        "sector_count": len(universe.sector_counts),
        "sector_counts": universe.sector_counts,
        "successful_symbols": len(successes),
        "failed_symbols": len(values) - len(successes),
        "coverage_rate": len(successes) / len(universe.members) if universe.members else 0,
        "symbols_with_invalid_ohlc": sum(value.get("invalid_ohlc_rows", 0) > 0 for value in successes),
        "symbols_with_zero_volume": sum(value.get("zero_volume_rows", 0) > 0 for value in successes),
        "universe_errors": list(universe.errors),
    }


def probe_us(target: int, start: datetime, end: datetime, root: Path) -> dict[str, Any]:
    provider = YFinanceProvider()
    universe = provider.build_universe(target)
    write_universe(root / "universe_us.csv", universe)
    state_path = root / "report_us.json"
    state = load_state(state_path)
    todo = [member for member in universe.members if state["symbols"].get(member.symbol, {}).get("status") != "ok"]
    bars_by_symbol, errors = provider.bars_many([instrument(member) for member in todo], start, end)
    error_map = {item.split(":", 1)[0]: item for item in errors}
    for member in todo:
        bars = bars_by_symbol.get(member.symbol, [])
        if bars:
            write_bars(root / "bars" / "us" / f"{member.symbol.replace('/', '_')}.csv", bars)
            state["symbols"][member.symbol] = {
                "status": "ok",
                "sector": member.sector,
                **summarize(bars, start),
            }
        else:
            state["symbols"][member.symbol] = {
                "status": "error",
                "sector": member.sector,
                "error": error_map.get(member.symbol, "empty response"),
            }
    finalize(state, universe)
    save_state(state_path, state)
    return state["summary"]


def probe_cn(target: int, start: datetime, end: datetime, root: Path) -> dict[str, Any]:
    provider = BaoStockProvider()
    universe = provider.build_universe(target)
    write_universe(root / "universe_cn.csv", universe)
    state_path = root / "report_cn.json"
    state = load_state(state_path)
    for index, member in enumerate(universe.members, 1):
        if state["symbols"].get(member.symbol, {}).get("status") == "ok":
            continue
        try:
            bars = provider.bars(instrument(member), start, end)
            if not bars:
                raise RuntimeError("empty response")
            write_bars(root / "bars" / "cn" / f"{member.symbol}.csv", bars)
            state["symbols"][member.symbol] = {
                "status": "ok",
                "sector": member.sector,
                **summarize(bars, start),
            }
        except Exception as exc:
            state["symbols"][member.symbol] = {
                "status": "error",
                "sector": member.sector,
                "error": f"{type(exc).__name__}: {exc}",
            }
        save_state(state_path, state)
        if index % 25 == 0:
            print(f"A股进度: {index}/{len(universe.members)}", flush=True)
        sleep(0.25)
    finalize(state, universe)
    save_state(state_path, state)
    provider.close()
    return state["summary"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["us", "cn", "all"], default="all")
    parser.add_argument("--target", type=int, default=220)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--output", type=Path, default=Path("data/cache/sector_probe"))
    args = parser.parse_args()
    if args.target < 200:
        parser.error("--target must be at least 200")
    end = datetime.now()
    start = end - timedelta(days=args.days)
    report: dict[str, Any] = {"generated_at": end.isoformat(), "days": args.days}
    if args.market in {"us", "all"}:
        report["us"] = probe_us(args.target, start, end, args.output)
        print(json.dumps({"us": report["us"]}, ensure_ascii=False, indent=2), flush=True)
    if args.market in {"cn", "all"}:
        report["cn"] = probe_cn(args.target, start, end, args.output)
        print(json.dumps({"cn": report["cn"]}, ensure_ascii=False, indent=2), flush=True)
    (args.output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
