"""Snapshot jobs that build point-in-time history for data free sources only publish
as "current": universe membership, index constituents, industry classification and
analyst consensus. Each run stores one complete snapshot with
``effective_at = retrieved_at``; analyses must use the latest snapshot not later than
the decision time (see ``core/classification.py``).
"""

from __future__ import annotations

import csv
import time as time_module
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from quant_workbench.data.validation import (
    validate_classification_row,
    validate_consensus_row,
    validate_index_constituent_row,
    validate_snapshot_row,
)
from quant_workbench.ops.lock import ProcessLock
from quant_workbench.store.files import DatasetStore, WriteResult
from quant_workbench.store.state import JobLease, StateStore

SCHEMA_VERSION = 1
UNIVERSE_DATASET = "universe_snapshot"
INDEX_DATASET = "index_constituents"
INDUSTRY_DATASET = "industry_classification"
CONSENSUS_DATASET = "consensus_estimates"
CN_INDEXES = ("hs300", "zz500", "sz50")
TAXONOMY = {"cn": "CSRC", "us": "YF_SECTOR"}
SOURCE = {"cn": "baostock", "us": "yfinance"}

SnapshotOutcome = tuple[JobLease, WriteResult | None, dict[str, Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _provenance(source: str, symbol: str, market: str, retrieved_at: str) -> dict[str, Any]:
    return {
        "source": source,
        "source_symbol": symbol,
        "symbol": symbol,
        "market": market,
        "retrieved_at": retrieved_at,
        "effective_at": retrieved_at,
        "schema_version": SCHEMA_VERSION,
    }


def _write(
    store: DatasetStore,
    state: StateStore,
    lease: JobLease,
    *,
    dataset: str,
    market: str,
    source: str,
    run_date: date,
    rows: list[dict[str, Any]],
    raw_payload: Any,
    key: str,
    validator: Callable[[dict[str, Any]], list[str]],
    retrieved_at: str,
    summary: dict[str, Any],
) -> SnapshotOutcome:
    assert lease.run_id is not None
    store.write_raw(source, dataset, market, run_date, raw_payload, key, summary)
    if not rows:
        raise RuntimeError(f"{dataset}: provider returned no rows")
    with ProcessLock(store.root / "state" / "parquet-writer.lock"):
        result = store.write_rows(
            dataset, market, source, run_date, rows, key, SCHEMA_VERSION, validator
        )
    state.set_watermark(source, dataset, market, retrieved_at)
    state.record_source_success(source)
    details = {**summary, "rows": result.row_count, "rejected": len(rows) - result.row_count}
    state.complete_job(lease.run_id, result.row_count, details)
    return lease, result, details


def _guarded(
    state: StateStore, lease: JobLease, source: str, action: Callable[[], SnapshotOutcome]
) -> SnapshotOutcome:
    assert lease.run_id is not None
    try:
        return action()
    except Exception as exc:
        state.record_source_failure(source, f"{type(exc).__name__}: {exc}")
        state.fail_job(lease.run_id, f"{type(exc).__name__}: {exc}")
        raise


def snapshot_universe(
    store: DatasetStore,
    state: StateStore,
    market: str,
    universe_path: Path,
    run_date: date,
    provider: Any | None = None,
) -> SnapshotOutcome:
    """Monthly copy of the configured research universe; for A shares also the full
    HS300 / ZZ500 / SZ50 constituent lists so index membership history accumulates."""
    source = SOURCE[market]
    month = f"{run_date.year:04d}-{run_date.month:02d}"
    lease = state.acquire_job("snapshot_universe", f"{market}:{month}", {"market": market})
    if not lease.acquired:
        return lease, None, {"noop": True, "reason": lease.reason}

    def action() -> SnapshotOutcome:
        retrieved_at = _now().isoformat()
        with universe_path.open(encoding="utf-8") as handle:
            members = list(csv.DictReader(handle))
        rows = [
            {
                **_provenance("universe_file", item["symbol"], market, retrieved_at),
                "exchange": item.get("exchange", ""),
                "name": item.get("name", ""),
                "sector": item.get("sector", ""),
                "market_cap": _float(item.get("market_cap")),
                "snapshot_month": month,
                "universe_file": universe_path.name,
            }
            for item in members
            if item.get("symbol")
        ]
        index_summary: dict[str, int] = {}
        if market == "cn":
            from quant_workbench.data.baostock_provider import BaoStockProvider

            client = provider or BaoStockProvider()
            try:
                index_rows: list[dict[str, Any]] = []
                raw_indexes: dict[str, Any] = {}
                for index_code in CN_INDEXES:
                    constituents = client.index_constituents(index_code)
                    raw_indexes[index_code] = constituents
                    index_summary[index_code] = len(constituents)
                    index_rows.extend(
                        {
                            **_provenance(source, item["symbol"], market, retrieved_at),
                            "exchange": item["exchange"],
                            "name": item.get("name", ""),
                            "index_code": index_code,
                            "source_updated_at": item.get("source_updated_at", ""),
                            "snapshot_month": month,
                        }
                        for item in constituents
                    )
            finally:
                if provider is None:
                    client.close()
            store.write_raw(
                source, INDEX_DATASET, market, run_date, raw_indexes, f"{market}-{month}"
            )
            if not index_rows:
                raise RuntimeError("index constituents: provider returned no rows")
            with ProcessLock(store.root / "state" / "parquet-writer.lock"):
                store.write_rows(
                    INDEX_DATASET,
                    market,
                    source,
                    run_date,
                    index_rows,
                    f"{market}-{month}",
                    SCHEMA_VERSION,
                    validate_index_constituent_row,
                )
            state.set_watermark(source, INDEX_DATASET, market, retrieved_at)
        return _write(
            store,
            state,
            lease,
            dataset=UNIVERSE_DATASET,
            market=market,
            source="universe_file",
            run_date=run_date,
            rows=rows,
            raw_payload={"members": members},
            key=f"{market}-{month}",
            validator=validate_snapshot_row,
            retrieved_at=retrieved_at,
            summary={"snapshot_month": month, "members": len(rows), "indexes": index_summary},
        )

    return _guarded(state, lease, "universe_file", action)


def snapshot_industry(
    store: DatasetStore,
    state: StateStore,
    market: str,
    run_date: date,
    provider: Any | None = None,
) -> SnapshotOutcome:
    """Monthly snapshot of the current industry / sector of every covered listing."""
    source = SOURCE[market]
    month = f"{run_date.year:04d}-{run_date.month:02d}"
    lease = state.acquire_job("snapshot_industry", f"{market}:{month}", {"market": market})
    if not lease.acquired:
        return lease, None, {"noop": True, "reason": lease.reason}

    def action() -> SnapshotOutcome:
        retrieved_at = _now().isoformat()
        errors: list[str] = []
        if market == "cn":
            from quant_workbench.data.baostock_provider import BaoStockProvider

            client = provider or BaoStockProvider()
            try:
                members = client.industry_classification()
            finally:
                if provider is None:
                    client.close()
        else:
            from quant_workbench.data.yfinance_provider import YFinanceProvider

            members, errors = (provider or YFinanceProvider()).sector_members()
            if len(errors) > 2:
                raise RuntimeError(f"sector screens failed: {errors[:3]}")
        rows = [
            {
                **_provenance(source, item["symbol"], market, retrieved_at),
                "exchange": item["exchange"],
                "name": item.get("name", ""),
                "taxonomy": TAXONOMY[market],
                "level": 1,
                "code": item["code"],
                "source_updated_at": item.get("source_updated_at", ""),
                "market_cap": _float(item.get("market_cap")),
                "snapshot_month": month,
            }
            for item in members
        ]
        return _write(
            store,
            state,
            lease,
            dataset=INDUSTRY_DATASET,
            market=market,
            source=source,
            run_date=run_date,
            rows=rows,
            raw_payload={"members": members, "errors": errors},
            key=f"{market}-{month}",
            validator=validate_classification_row,
            retrieved_at=retrieved_at,
            summary={
                "snapshot_month": month,
                "taxonomy": TAXONOMY[market],
                "codes": len({item["code"] for item in members}),
                "errors": errors,
            },
        )

    return _guarded(state, lease, source, action)


def consensus_rows(
    symbol: str, payload: dict[str, Any], market: str, retrieved_at: str
) -> list[dict[str, Any]]:
    """Flatten a provider consensus payload into one row per period plus a target row."""
    base = _provenance("yfinance", symbol, market, retrieved_at)
    by_period: dict[str, dict[str, Any]] = {}

    def merge(records: list[dict[str, Any]], mapping: dict[str, str]) -> None:
        for record in records:
            period = str(record.get("period") or "")
            if not period:
                continue
            row = by_period.setdefault(
                period, {**base, "record_type": "estimate", "period": period}
            )
            for origin, target in mapping.items():
                row[target] = _float(record.get(origin))
            if record.get("currency"):
                row["currency"] = str(record["currency"])

    merge(
        payload.get("earnings_estimate", []),
        {
            "avg": "eps_avg",
            "low": "eps_low",
            "high": "eps_high",
            "yearAgoEps": "eps_year_ago",
            "numberOfAnalysts": "eps_analysts",
            "growth": "eps_growth",
        },
    )
    merge(
        payload.get("revenue_estimate", []),
        {
            "avg": "revenue_avg",
            "low": "revenue_low",
            "high": "revenue_high",
            "yearAgoRevenue": "revenue_year_ago",
            "numberOfAnalysts": "revenue_analysts",
            "growth": "revenue_growth",
        },
    )
    merge(
        payload.get("eps_trend", []),
        {
            "current": "eps_trend_current",
            "7daysAgo": "eps_trend_7d",
            "30daysAgo": "eps_trend_30d",
            "60daysAgo": "eps_trend_60d",
            "90daysAgo": "eps_trend_90d",
        },
    )
    merge(
        payload.get("eps_revisions", []),
        {
            "upLast7days": "eps_up_7d",
            "upLast30days": "eps_up_30d",
            "downLast7Days": "eps_down_7d",
            "downLast30days": "eps_down_30d",
        },
    )
    rows = list(by_period.values())
    targets = payload.get("price_targets") or {}
    current_month = next(
        (item for item in payload.get("recommendations", []) if item.get("period") == "0m"), {}
    )
    if targets or current_month:
        rows.append(
            {
                **base,
                "record_type": "target",
                "period": "",
                "price_current": _float(targets.get("current")),
                "target_mean": _float(targets.get("mean")),
                "target_median": _float(targets.get("median")),
                "target_low": _float(targets.get("low")),
                "target_high": _float(targets.get("high")),
                "rating_strong_buy": _float(current_month.get("strongBuy")),
                "rating_buy": _float(current_month.get("buy")),
                "rating_hold": _float(current_month.get("hold")),
                "rating_sell": _float(current_month.get("sell")),
                "rating_strong_sell": _float(current_month.get("strongSell")),
            }
        )
    return rows


def snapshot_consensus(
    store: DatasetStore,
    state: StateStore,
    market: str,
    universe_path: Path,
    run_date: date,
    provider: Any | None = None,
    minimum_coverage: float = 0.8,
    pause_seconds: float = 0.2,
    limit: int | None = None,
) -> SnapshotOutcome:
    """Daily snapshot of published analyst consensus (US only: no free A-share source
    with stable access yet). Forms the point-in-time record for expectation-gap work."""
    if market != "us":
        raise ValueError("consensus snapshots are only implemented for the US market")
    source = "yfinance"
    lease = state.acquire_job(
        "snapshot_consensus", f"{market}:{run_date.isoformat()}", {"market": market}
    )
    if not lease.acquired:
        return lease, None, {"noop": True, "reason": lease.reason}

    def action() -> SnapshotOutcome:
        from quant_workbench.data.yfinance_provider import YFinanceProvider

        client = provider or YFinanceProvider()
        retrieved_at = _now().isoformat()
        with universe_path.open(encoding="utf-8") as handle:
            symbols = [row["symbol"] for row in csv.DictReader(handle) if row.get("symbol")]
        if limit:
            symbols = symbols[:limit]
        rows: list[dict[str, Any]] = []
        raw: dict[str, Any] = {}
        errors: list[str] = []
        covered = 0
        for index, symbol in enumerate(symbols):
            if index and pause_seconds > 0:
                time_module.sleep(pause_seconds)
            try:
                payload = client.consensus(symbol)
                raw[symbol] = payload
                current = consensus_rows(symbol, payload, market, retrieved_at)
                if any(row["record_type"] == "estimate" for row in current):
                    covered += 1
                rows.extend(current)
            except Exception as exc:
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
        # Some listings (funds, thinly covered ADRs) have no analyst estimates at all;
        # coverage counts provider answers, not the presence of estimates.
        answered = len(symbols) - len(errors)
        coverage = answered / len(symbols) if symbols else 0.0
        summary = {
            "session": run_date.isoformat(),
            "symbols": len(symbols),
            "answered": answered,
            "with_estimates": covered,
            "coverage": coverage,
            "errors": errors[:20],
        }
        if coverage < minimum_coverage:
            store.write_raw(
                source, CONSENSUS_DATASET, market, run_date, raw, f"{market}-{run_date}", summary
            )
            raise RuntimeError(f"consensus coverage {coverage:.1%} below {minimum_coverage:.0%}")
        return _write(
            store,
            state,
            lease,
            dataset=CONSENSUS_DATASET,
            market=market,
            source=source,
            run_date=run_date,
            rows=rows,
            raw_payload=raw,
            key=f"{market}-{run_date.isoformat()}",
            validator=validate_consensus_row,
            retrieved_at=retrieved_at,
            summary=summary,
        )

    return _guarded(state, lease, source, action)
