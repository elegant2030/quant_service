from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_workbench.jobs.ingestion import ingest_incremental_bars, snapshot_options
from quant_workbench.jobs.snapshots import (
    CONSENSUS_DATASET,
    INDUSTRY_DATASET,
    SOURCE,
    UNIVERSE_DATASET,
    snapshot_consensus,
    snapshot_industry,
    snapshot_universe,
)
from quant_workbench.ops.calendar import latest_completed_session
from quant_workbench.store import DatasetStore, StateStore


def run_due_jobs(
    store: DatasetStore,
    state: StateStore,
    universe_directory: Path,
    option_symbols: list[str],
    now: datetime | None = None,
    snapshots: bool = True,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    results: dict[str, Any] = {}
    errors: list[str] = []

    for market in ("cn", "us"):
        target = latest_completed_session(market, current)
        try:
            lease, write_result, details = ingest_incremental_bars(
                store,
                state,
                market,
                universe_directory / f"universe_{market}.csv",
                target,
            )
            results[f"daily_bars_{market}"] = {
                "target_session": target.isoformat(),
                "acquired": lease.acquired,
                "reason": lease.reason,
                "details": details,
                "rows": write_result.row_count if write_result else 0,
            }
        except Exception as exc:
            message = f"daily_bars_{market}: {type(exc).__name__}: {exc}"
            errors.append(message)
            results[f"daily_bars_{market}"] = {"error": message}

    option_target = latest_completed_session("us", current)
    normalized_option_symbols = sorted(
        {symbol.strip().upper() for symbol in option_symbols if symbol.strip()}
    )
    pending_option_symbols = []
    for symbol in normalized_option_symbols:
        watermark = state.get_watermark(
            "yfinance", "option_chain", "us", symbol=symbol
        )
        if not watermark or watermark[:10] < option_target.isoformat():
            pending_option_symbols.append(symbol)
    if not pending_option_symbols:
        results["options"] = {
            "target_session": option_target.isoformat(),
            "acquired": False,
            "reason": "already_current",
            "rows": 0,
            "symbols": normalized_option_symbols,
        }
    else:
        try:
            lease, write_result, failures = snapshot_options(
                store, state, pending_option_symbols, option_target
            )
            results["options"] = {
                "target_session": option_target.isoformat(),
                "acquired": lease.acquired,
                "reason": lease.reason,
                "rows": write_result.row_count if write_result else 0,
                "requested_symbols": normalized_option_symbols,
                "pending_symbols": pending_option_symbols,
                "warnings": failures,
            }
        except Exception as exc:
            message = f"options: {type(exc).__name__}: {exc}"
            errors.append(message)
            results["options"] = {"error": message}

    if snapshots:
        for market in ("cn", "us"):
            session = latest_completed_session(market, current)
            month = session.isoformat()[:7]
            universe_path = universe_directory / f"universe_{market}.csv"
            monthly = (
                ("universe", "universe_file", UNIVERSE_DATASET),
                ("industry", SOURCE[market], INDUSTRY_DATASET),
            )
            for name, source, dataset in monthly:
                label = f"snapshot_{name}_{market}"
                watermark = state.get_watermark(source, dataset, market)
                if watermark and watermark[:7] >= month:
                    results[label] = {
                        "month": month,
                        "acquired": False,
                        "reason": "already_current",
                    }
                    continue
                try:
                    if name == "universe":
                        lease, written, details = snapshot_universe(
                            store, state, market, universe_path, session
                        )
                    else:
                        lease, written, details = snapshot_industry(store, state, market, session)
                    results[label] = {
                        "month": month,
                        "acquired": lease.acquired,
                        "reason": lease.reason,
                        "rows": written.row_count if written else 0,
                        "details": details,
                    }
                except Exception as exc:
                    message = f"{label}: {type(exc).__name__}: {exc}"
                    errors.append(message)
                    results[label] = {"error": message}

        consensus_session = latest_completed_session("us", current)
        consensus_watermark = state.get_watermark("yfinance", CONSENSUS_DATASET, "us")
        if consensus_watermark and consensus_watermark[:10] >= consensus_session.isoformat():
            results["snapshot_consensus_us"] = {
                "session": consensus_session.isoformat(),
                "acquired": False,
                "reason": "already_current",
            }
        else:
            try:
                lease, written, details = snapshot_consensus(
                    store, state, "us", universe_directory / "universe_us.csv", consensus_session
                )
                results["snapshot_consensus_us"] = {
                    "session": consensus_session.isoformat(),
                    "acquired": lease.acquired,
                    "reason": lease.reason,
                    "rows": written.row_count if written else 0,
                    "details": details,
                }
            except Exception as exc:
                message = f"snapshot_consensus_us: {type(exc).__name__}: {exc}"
                errors.append(message)
                results["snapshot_consensus_us"] = {"error": message}

    return {
        "status": "error" if errors else "ok",
        "run_at": current.astimezone(timezone.utc).isoformat(),
        "results": results,
        "errors": errors,
    }
