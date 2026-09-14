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
    status = "error" if errors else "degraded" if warnings else "ok"
    return {
        "status": status,
        "checked_at": current.astimezone(timezone.utc).isoformat(),
        "errors": errors,
        "warnings": warnings,
        "sqlite_integrity": database_integrity,
        "markets": market_health,
        "options": options_health,
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
