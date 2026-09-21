from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quant_workbench.ops.calendar import latest_completed_session
from quant_workbench.store import DatasetStore, StateStore

MARKETS = {"us": "yfinance", "cn": "baostock"}


def _manifest_checks(store: DatasetStore, verify_checksums: bool) -> tuple[list[str], int]:
    errors: list[str] = []
    checked = 0
    for manifest_path in sorted((store.root / "canonical").rglob("*.manifest.json")):
        checked += 1
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data_path = store.root / manifest["relative_path"]
            if not data_path.exists():
                errors.append(f"missing data file: {data_path}")
                continue
            if verify_checksums:
                digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
                if digest != manifest["sha256"]:
                    errors.append(f"checksum mismatch: {data_path}")
        except Exception as exc:
            errors.append(f"invalid manifest {manifest_path}: {type(exc).__name__}: {exc}")
    if checked == 0:
        errors.append("no canonical manifests found")
    return errors, checked


def read_build_info(store: DatasetStore) -> dict[str, Any]:
    """``<runtime>/build-info.json`` is written by ops/deploy_macos_runtime.sh."""
    path = store.root.parent / "build-info.json"
    if not path.is_file():
        return {"build_sha": None, "deployed_at": None, "build_info_path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "build_sha": None,
            "deployed_at": None,
            "build_info_path": str(path),
            "build_info_error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "build_sha": payload.get("build_sha"),
        "deployed_at": payload.get("deployed_at"),
        "build_info_path": str(path),
    }


MONTHLY_SNAPSHOT_GRACE_DAYS = 5
CONSENSUS_GRACE_DAYS = 4


def _snapshot_freshness(
    state: StateStore, current: datetime, errors: list[str], warnings: list[str]
) -> dict[str, Any]:
    """Point-in-time snapshots cannot be back-filled, so a missed capture is a permanent
    hole: never captured -> warning, overdue beyond the grace period -> error."""
    from quant_workbench.jobs.snapshots import (
        CONSENSUS_DATASET,
        INDUSTRY_DATASET,
        SOURCE,
        UNIVERSE_DATASET,
    )

    report: dict[str, Any] = {}
    for market in MARKETS:
        expected = latest_completed_session(market, current, availability_delay=timedelta(hours=3))
        month = expected.isoformat()[:7]
        for name, source, dataset in (
            ("universe", "universe_file", UNIVERSE_DATASET),
            ("industry", SOURCE[market], INDUSTRY_DATASET),
        ):
            watermark = state.get_watermark(source, dataset, market)
            report[f"{name}_{market}"] = {"expected_month": month, "watermark": watermark}
            if watermark is None:
                warnings.append(f"{market} {name} snapshot never captured")
            elif watermark[:7] < month:
                message = f"{market} {name} snapshot stale: {watermark[:7]} < {month}"
                if expected.day > MONTHLY_SNAPSHOT_GRACE_DAYS:
                    errors.append(message)
                else:
                    warnings.append(message)
    expected = latest_completed_session("us", current, availability_delay=timedelta(hours=3))
    watermark = state.get_watermark("yfinance", CONSENSUS_DATASET, "us")
    report["consensus_us"] = {"expected_session": expected.isoformat(), "watermark": watermark}
    if watermark is None:
        warnings.append("us consensus snapshot never captured")
    elif watermark[:10] < (expected - timedelta(days=CONSENSUS_GRACE_DAYS)).isoformat():
        errors.append(f"us consensus snapshot stale: {watermark[:10]} < {expected}")
    elif watermark[:10] < expected.isoformat():
        warnings.append(f"us consensus snapshot behind: {watermark[:10]} < {expected}")
    return report


def build_health_report(
    store: DatasetStore,
    state: StateStore,
    now: datetime | None = None,
    minimum_symbol_coverage: float = 0.98,
    verify_checksums: bool = False,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    errors: list[str] = []
    warnings: list[str] = []
    database_integrity = state.connection.execute("PRAGMA integrity_check").fetchone()[0]
    if database_integrity != "ok":
        errors.append(f"sqlite integrity: {database_integrity}")

    market_health: dict[str, Any] = {}
    for market, source in MARKETS.items():
        # Leave time for the upstream source and the 30-minute pipeline cadence.
        expected = latest_completed_session(market, current, availability_delay=timedelta(hours=3))
        watermark = state.get_watermark(source, "daily_bars", market)
        total_symbols = state.connection.execute(
            """
            SELECT COUNT(*) FROM watermarks
            WHERE source=? AND dataset='daily_bars' AND market=? AND symbol<>''
            """,
            (source, market),
        ).fetchone()[0]
        current_symbols = state.connection.execute(
            """
            SELECT COUNT(*) FROM watermarks
            WHERE source=? AND dataset='daily_bars' AND market=? AND symbol<>'' AND value>=?
            """,
            (source, market, expected.isoformat()),
        ).fetchone()[0]
        coverage = current_symbols / total_symbols if total_symbols else 0.0
        market_health[market] = {
            "source": source,
            "expected_session": expected.isoformat(),
            "watermark": watermark,
            "symbols": total_symbols,
            "current_symbols": current_symbols,
            "coverage": coverage,
        }
        if watermark is None or watermark < expected.isoformat():
            errors.append(f"{market} daily bars stale: {watermark} < {expected}")
        if coverage < minimum_symbol_coverage:
            errors.append(
                f"{market} symbol coverage {coverage:.2%} < {minimum_symbol_coverage:.2%}"
            )
        elif coverage < 1.0:
            warnings.append(f"{market} symbol coverage is {coverage:.2%}")

    option_expected = latest_completed_session("us", current, availability_delay=timedelta(hours=3))
    option_watermark = state.get_watermark("yfinance", "option_chain", "us")
    option_date = option_watermark[:10] if option_watermark else None
    options_health = {
        "expected_session": option_expected.isoformat(),
        "watermark": option_watermark,
    }
    if option_date is None or option_date < option_expected.isoformat():
        errors.append(f"options snapshot stale: {option_date} < {option_expected}")

    snapshot_health = _snapshot_freshness(state, current, errors, warnings)

    sources = state.status(limit=0)["sources"]
    for source in sources:
        if source["circuit_open_until"]:
            try:
                if datetime.fromisoformat(source["circuit_open_until"]) > current:
                    errors.append(f"source circuit open: {source['source']}")
            except ValueError:
                errors.append(f"invalid circuit timestamp: {source['source']}")

    manifest_errors, manifests_checked = _manifest_checks(store, verify_checksums)
    errors.extend(manifest_errors)
    build_info = read_build_info(store)
    status = "error" if errors else "degraded" if warnings else "ok"
    return {
        "status": status,
        "checked_at": current.astimezone(timezone.utc).isoformat(),
        "build_sha": build_info["build_sha"],
        "deployed_at": build_info["deployed_at"],
        "errors": errors,
        "warnings": warnings,
        "sqlite_integrity": database_integrity,
        "markets": market_health,
        "options": options_health,
        "snapshots": snapshot_health,
        "sources": sources,
        "manifests_checked": manifests_checked,
        "checksums_verified": verify_checksums,
        "inventory": store.inventory(),
    }


def write_health_report(store: DatasetStore, report: dict[str, Any]) -> Path:
    current = datetime.fromisoformat(report["checked_at"])
    store.write_json("health/latest.json", report)
    return store.write_json(
        Path("health/history") / current.date().isoformat() / f"{current:%H%M%S}.json",
        report,
    )
