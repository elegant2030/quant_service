from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_workbench.jobs.ingestion import ingest_incremental_bars, snapshot_options
from quant_workbench.ops.calendar import latest_completed_session
from quant_workbench.store import DatasetStore, StateStore


def run_due_jobs(
    store: DatasetStore,
    state: StateStore,
    universe_directory: Path,
    option_symbols: list[str],
    now: datetime | None = None,
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
    option_watermark = state.get_watermark("yfinance", "option_chain", "us")
    if option_watermark and option_watermark[:10] >= option_target.isoformat():
        results["options"] = {
            "target_session": option_target.isoformat(),
            "acquired": False,
            "reason": "already_current",
            "rows": 0,
        }
    else:
        try:
            lease, write_result, failures = snapshot_options(
                store, state, option_symbols, option_target
            )
            results["options"] = {
                "target_session": option_target.isoformat(),
                "acquired": lease.acquired,
                "reason": lease.reason,
                "rows": write_result.row_count if write_result else 0,
                "warnings": failures,
            }
        except Exception as exc:
            message = f"options: {type(exc).__name__}: {exc}"
            errors.append(message)
            results["options"] = {"error": message}

    return {
        "status": "error" if errors else "ok",
        "run_at": current.astimezone(timezone.utc).isoformat(),
        "results": results,
        "errors": errors,
    }
