from __future__ import annotations

import csv
import hashlib
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from tenacity import retry, stop_after_attempt, wait_exponential

from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument
from quant_workbench.data.baostock_provider import BaoStockProvider
from quant_workbench.data.validation import (
    BAR_SCHEMA_VERSION,
    RAW_ADJUSTMENT,
    validate_bar_row,
    validate_option_row,
)
from quant_workbench.data.yfinance_provider import YFinanceProvider
from quant_workbench.ops.calendar import latest_session
from quant_workbench.ops.lock import ProcessLock
from quant_workbench.store.files import DatasetStore, WriteResult
from quant_workbench.store.state import JobLease, StateStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _universe_metadata(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return {row["symbol"]: row for row in csv.DictReader(handle)}


def _universe_instruments(path: Path, market: str) -> list[Instrument]:
    metadata = _universe_metadata(path)
    currency = Currency.USD if market == "us" else Currency.CNY
    lot_size = Decimal("1" if market == "us" else "100")
    return [
        Instrument(
            symbol,
            Exchange(row["exchange"]),
            AssetClass.EQUITY,
            currency,
            lot_size=lot_size,
            metadata={"name": row.get("name", symbol), "sector": row.get("sector", "")},
        )
        for symbol, row in metadata.items()
    ]


def _bar_row(bar: Bar, market: str, source: str, retrieved_at: str) -> dict[str, Any]:
    return {
        "source": source,
        "source_symbol": bar.instrument.symbol,
        "symbol": bar.instrument.symbol,
        "market": market,
        "exchange": bar.instrument.exchange.value,
        "sector": str(bar.instrument.metadata.get("sector", "")),
        "name": str(bar.instrument.metadata.get("name", bar.instrument.symbol)),
        "session_date": bar.timestamp.date().isoformat(),
        "effective_at": bar.timestamp.date().isoformat(),
        "retrieved_at": retrieved_at,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.volume),
        "previous_close": float(bar.previous_close) if bar.previous_close is not None else None,
        "dividend": float(bar.dividend) if bar.dividend is not None else None,
        "split_ratio": float(bar.split_ratio) if bar.split_ratio is not None else None,
        "adj_factor": float(bar.adj_factor) if bar.adj_factor is not None else None,
        "adjustment": RAW_ADJUSTMENT,
        "schema_version": BAR_SCHEMA_VERSION,
    }


def _cached_bar_rows(
    bars_directory: Path,
    universe: dict[str, dict[str, str]],
    market: str,
    source: str,
) -> Iterable[dict[str, Any]]:
    retrieved_at = _utc_now().isoformat()
    for path in sorted(bars_directory.glob("*.csv")):
        symbol = path.stem
        metadata = universe.get(symbol, {})
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                timestamp = datetime.fromisoformat(row["timestamp"])
                yield {
                    "source": source,
                    "source_symbol": symbol,
                    "symbol": symbol,
                    "market": market,
                    "exchange": metadata.get("exchange", ""),
                    "sector": metadata.get("sector", ""),
                    "name": metadata.get("name", symbol),
                    "session_date": timestamp.date().isoformat(),
                    "effective_at": timestamp.date().isoformat(),
                    "retrieved_at": retrieved_at,
                    "open": _float_or_none(row.get("open")),
                    "high": _float_or_none(row.get("high")),
                    "low": _float_or_none(row.get("low")),
                    "close": _float_or_none(row.get("close")),
                    "volume": _float_or_none(row.get("volume")) or 0.0,
                    "previous_close": _float_or_none(row.get("previous_close")),
                    "adjustment": "provider_adjusted",
                    "schema_version": 1,
                }


def ingest_cached_bars(
    store: DatasetStore,
    state: StateStore,
    market: str,
    bars_directory: Path,
    universe_path: Path,
    source: str,
    run_date: date,
) -> tuple[JobLease, WriteResult | None]:
    job_name = "ingest_cached_bars"
    identity = f"{market}:{source}:schema1"
    lease = state.acquire_job(
        job_name,
        identity,
        {"bars_directory": str(bars_directory), "universe": str(universe_path)},
    )
    if not lease.acquired:
        return lease, None
    assert lease.run_id is not None
    try:
        universe = _universe_metadata(universe_path)
        rows = list(_cached_bar_rows(bars_directory, universe, market, source))
        with ProcessLock(store.root / "state" / "parquet-writer.lock"):
            result = store.write_rows(
                "daily_bars",
                market,
                source,
                run_date,
                rows,
                identity.replace(":", "-"),
                validator=validate_bar_row,
            )
        latest = max(row["session_date"] for row in rows)
        state.set_watermark(source, "daily_bars", market, latest)
        state.record_source_success(source)
        state.complete_job(lease.run_id, result.row_count)
        return lease, result
    except Exception as exc:
        state.record_source_failure(source, f"{type(exc).__name__}: {exc}")
        state.fail_job(lease.run_id, f"{type(exc).__name__}: {exc}")
        raise


US_WARMUP_DAYS = 7  # calendar days fetched before ``start`` so previous_close is known


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def _fetch_cn_bars(
    provider: BaoStockProvider, instrument: Instrument, start: date, end: date
) -> list[Bar]:
    return provider.bars(
        instrument,
        datetime.combine(start, datetime.min.time()),
        datetime.combine(end, datetime.max.time()),
        adjusted=False,
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def _fetch_us_bars(
    provider: YFinanceProvider,
    instruments: list[Instrument],
    start: date,
    end: date,
) -> tuple[dict[str, list[Bar]], list[str]]:
    """Raw bars from ``start - US_WARMUP_DAYS``; callers trim to ``start``."""
    return provider.bars_many(
        instruments,
        datetime.combine(start - timedelta(days=US_WARMUP_DAYS), datetime.min.time()),
        datetime.combine(end, datetime.max.time()),
        adjusted=False,
    )


def ingest_incremental_bars(
    store: DatasetStore,
    state: StateStore,
    market: str,
    universe_path: Path,
    as_of: date,
    minimum_coverage: float = 0.98,
) -> tuple[JobLease, WriteResult | None, dict[str, Any]]:
    if market not in {"us", "cn"}:
        raise ValueError(f"unsupported market: {market}")
    source = "yfinance" if market == "us" else "baostock"
    target = latest_session(market, as_of)
    identity = f"{market}:{source}:{target.isoformat()}"
    lease = state.acquire_job(
        "ingest_incremental_bars",
        identity,
        {"market": market, "source": source, "target_session": target.isoformat()},
    )
    if not lease.acquired:
        return lease, None, {"target_session": target.isoformat(), "noop": True, "errors": []}
    assert lease.run_id is not None
    if state.circuit_is_open(source):
        error = f"source circuit is open: {source}"
        state.fail_job(lease.run_id, error)
        raise RuntimeError(error)

    try:
        instruments = _universe_instruments(universe_path, market)
        global_watermark = state.get_watermark(source, "daily_bars", market)
        grouped: dict[date, list[Instrument]] = defaultdict(list)
        already_current: dict[str, str] = {}
        for instrument in instruments:
            value = state.get_watermark(source, "daily_bars", market, instrument.symbol)
            value = value or global_watermark
            watermark = date.fromisoformat(value) if value else target - timedelta(days=10)
            if watermark >= target:
                already_current[instrument.symbol] = watermark.isoformat()
            else:
                grouped[watermark + timedelta(days=1)].append(instrument)
        if already_current:
            state.set_symbol_watermarks(source, "daily_bars", market, already_current)
        if not grouped:
            state.complete_job(
                lease.run_id,
                0,
                {"target_session": target.isoformat(), "noop": True, "reason": "already_current"},
            )
            return (
                lease,
                None,
                {
                    "target_session": target.isoformat(),
                    "noop": True,
                    "reason": "already_current",
                    "errors": [],
                },
            )

        retrieved_at = _utc_now().isoformat()
        bars_by_symbol: dict[str, list[Bar]] = {}
        successful_symbols: set[str] = set()
        errors: list[str] = []
        for start, group in grouped.items():
            fetched, successful, group_errors = _fetch_market_bars(market, group, start, target)
            bars_by_symbol.update(fetched)
            successful_symbols.update(successful)
            errors.extend(group_errors)

        pending_count = sum(len(group) for group in grouped.values())
        coverage = len(successful_symbols) / pending_count if pending_count else 1.0
        bars = [bar for values in bars_by_symbol.values() for bar in values]
        rows = [_bar_row(bar, market, source, retrieved_at) for bar in bars]
        store.write_raw(
            source,
            "daily_bars_adapter_output",
            market,
            target,
            {"rows": rows, "errors": errors},
            identity.replace(":", "-"),
            {"capture_level": "adapter_output", "coverage": coverage},
        )
        if coverage < minimum_coverage:
            raise RuntimeError(
                f"coverage {coverage:.2%} is below {minimum_coverage:.2%}; errors={errors[:5]}"
            )
        if not rows:
            raise RuntimeError(
                "provider returned no rows for expected session "
                f"{target}; upstream may not be ready"
            )
        with ProcessLock(store.root / "state" / "parquet-writer.lock"):
            result = store.write_rows(
                "daily_bars",
                market,
                source,
                target,
                rows,
                identity.replace(":", "-"),
                validator=validate_bar_row,
            )
        advanced = {symbol: target.isoformat() for symbol in successful_symbols}
        state.set_symbol_watermarks(source, "daily_bars", market, advanced)
        state.set_watermark(source, "daily_bars", market, target.isoformat())
        state.record_source_success(source)
        state.complete_job(
            lease.run_id,
            result.row_count,
            {
                "target_session": target.isoformat(),
                "coverage": coverage,
                "successful_symbols": len(successful_symbols),
                "pending_symbols": pending_count,
                "errors": errors,
            },
        )
        return (
            lease,
            result,
            {
                "target_session": target.isoformat(),
                "noop": False,
                "coverage": coverage,
                "errors": errors,
            },
        )
    except Exception as exc:
        state.record_source_failure(source, f"{type(exc).__name__}: {exc}")
        state.fail_job(lease.run_id, f"{type(exc).__name__}: {exc}")
        raise


def _fetch_market_bars(
    market: str,
    instruments: list[Instrument],
    start: date,
    end: date,
    provider: Any | None = None,
) -> tuple[dict[str, list[Bar]], set[str], list[str]]:
    """Fetch raw bars for every instrument in ``[start, end]``.

    Returns bars per symbol, the set of symbols the provider answered for, and errors.
    ``provider`` may be injected for tests; otherwise the market's default provider.
    """
    bars_by_symbol: dict[str, list[Bar]] = {}
    successful: set[str] = set()
    errors: list[str] = []
    if market == "us":
        client = provider or YFinanceProvider()
        batches, batch_errors = _fetch_us_bars(client, instruments, start, end)
        failed = {message.split(":", 1)[0] for message in batch_errors}
        errors.extend(batch_errors)
        for instrument in instruments:
            if instrument.symbol in batches and instrument.symbol not in failed:
                successful.add(instrument.symbol)
                bars_by_symbol[instrument.symbol] = [
                    bar
                    for bar in batches[instrument.symbol]
                    if start <= bar.timestamp.date() <= end
                ]
            elif instrument.symbol not in failed:
                errors.append(f"{instrument.symbol}: missing provider result")
    else:
        client = provider or BaoStockProvider()
        try:
            for instrument in instruments:
                try:
                    bars_by_symbol[instrument.symbol] = _fetch_cn_bars(
                        client, instrument, start, end
                    )
                    successful.add(instrument.symbol)
                except Exception as exc:
                    errors.append(f"{instrument.symbol}: {type(exc).__name__}: {exc}")
        finally:
            if provider is None:
                client.close()
    return bars_by_symbol, successful, errors


def _daily_bar_parts(store: DatasetStore, market: str) -> list[Path]:
    partition = store.root / "canonical" / "dataset=daily_bars" / f"market={market}"
    return sorted(partition.rglob("*.parquet"))


def _compare_with_existing(
    store: DatasetStore, market: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Explain the backfill: forward-adjusted new closes vs. previously stored closes."""
    parts = _daily_bar_parts(store, market)
    if not parts or not rows:
        return {"compared_rows": 0, "note": "no existing rows"}
    try:
        import duckdb
        import pandas as pd

        from quant_workbench.data.adjust import apply_adjustment
    except ImportError:
        return {"compared_rows": 0, "note": "duckdb/pandas unavailable"}
    new = pd.DataFrame(rows)[["symbol", "session_date", "close", "adj_factor"]]
    new = apply_adjustment(new, mode="forward")
    connection = duckdb.connect()
    try:
        old = connection.execute(
            """
            SELECT symbol, session_date, close AS old_close,
                   COALESCE(adjustment, '') AS old_adjustment
            FROM read_parquet(?, union_by_name=true)
            """,
            [[str(path) for path in parts]],
        ).df()
    finally:
        connection.close()
    merged = new.merge(old, on=["symbol", "session_date"], how="inner")
    if merged.empty:
        return {"compared_rows": 0, "note": "no overlapping (symbol, session_date)"}
    rel = ((merged["close_adj"] - merged["old_close"]) / merged["old_close"]).abs()
    raw_rel = ((merged["close"] - merged["old_close"]) / merged["old_close"]).abs()
    worst = merged.assign(rel=rel).sort_values("rel", ascending=False).head(5)
    return {
        "compared_rows": int(len(merged)),
        "old_adjustment_values": sorted(merged["old_adjustment"].unique().tolist()),
        "forward_adjusted_vs_old": {
            "median_abs_rel_diff": float(rel.median()),
            "p99_abs_rel_diff": float(rel.quantile(0.99)),
            "max_abs_rel_diff": float(rel.max()),
            "rows_over_1pct": int((rel > 0.01).sum()),
        },
        "raw_vs_old": {
            "median_abs_rel_diff": float(raw_rel.median()),
            "max_abs_rel_diff": float(raw_rel.max()),
        },
        "worst": [
            {
                "symbol": r.symbol,
                "session_date": str(r.session_date),
                "old": float(r.old_close),
                "new_adj": float(r.close_adj),
                "new_raw": float(r.close),
            }
            for r in worst.itertuples()
        ],
    }


def _archive_parts(store: DatasetStore, parts: list[Path], run_date: date) -> list[str]:
    """Move superseded canonical parts (and manifests) under ``archive/``; never delete."""
    archived: list[str] = []
    for path in parts:
        for file in (path, path.with_suffix(".manifest.json")):
            if not file.exists():
                continue
            relative = file.relative_to(store.root)
            target = store.root / "archive" / run_date.isoformat() / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(file, target)
            archived.append(str(relative))
    return archived


def backfill_daily_bars(
    store: DatasetStore,
    state: StateStore,
    market: str,
    universe_path: Path,
    start: date,
    run_date: date,
    end: date | None = None,
    minimum_coverage: float = 0.98,
    provider: Any | None = None,
    force: bool = False,
) -> tuple[JobLease, WriteResult | None, dict[str, Any]]:
    """Re-fetch the full raw history and replace provider-adjusted (schema 1) parts.

    Existing parts are moved to ``archive/<run_date>/`` after the new part is written,
    so nothing is deleted. Watermarks are untouched: the target session is the
    current global watermark (or the latest completed session).
    """
    if market not in {"us", "cn"}:
        raise ValueError(f"unsupported market: {market}")
    source = "yfinance" if market == "us" else "baostock"
    watermark = state.get_watermark(source, "daily_bars", market)
    target = end or (
        date.fromisoformat(watermark) if watermark else latest_session(market, run_date)
    )
    identity = (
        f"{market}:{source}:schema{BAR_SCHEMA_VERSION}:{start.isoformat()}:{target.isoformat()}"
    )
    if force:
        identity += f":{_utc_now().strftime('%Y%m%dT%H%M%S')}"
    lease = state.acquire_job(
        "backfill_daily_bars",
        identity,
        {"market": market, "source": source, "start": start.isoformat(), "end": target.isoformat()},
    )
    if not lease.acquired:
        return lease, None, {"noop": True, "reason": lease.reason, "errors": []}
    assert lease.run_id is not None
    try:
        instruments = _universe_instruments(universe_path, market)
        retrieved_at = _utc_now().isoformat()
        bars_by_symbol, successful, errors = _fetch_market_bars(
            market, instruments, start, target, provider
        )
        coverage = len(successful) / len(instruments) if instruments else 1.0
        rows = [
            _bar_row(bar, market, source, retrieved_at)
            for bars in bars_by_symbol.values()
            for bar in bars
        ]
        store.write_raw(
            source,
            "daily_bars_backfill",
            market,
            run_date,
            {"rows": rows, "errors": errors},
            identity.replace(":", "-"),
            {"capture_level": "adapter_output", "coverage": coverage, "start": start.isoformat()},
        )
        if coverage < minimum_coverage:
            raise RuntimeError(
                f"coverage {coverage:.2%} is below {minimum_coverage:.2%}; errors={errors[:5]}"
            )
        if not rows:
            raise RuntimeError("provider returned no rows for backfill")
        comparison = _compare_with_existing(store, market, rows)
        previous_parts = _daily_bar_parts(store, market)
        with ProcessLock(store.root / "state" / "parquet-writer.lock"):
            result = store.write_rows(
                "daily_bars",
                market,
                source,
                run_date,
                rows,
                identity.replace(":", "-"),
                schema_version=BAR_SCHEMA_VERSION,
                validator=validate_bar_row,
            )
            archived = _archive_parts(
                store, [path for path in previous_parts if path != result.path], run_date
            )
        state.record_source_success(source)
        summary = {
            "target_session": target.isoformat(),
            "start": start.isoformat(),
            "coverage": coverage,
            "rows": result.row_count,
            "rejected": len(rows) - result.row_count,
            "symbols": len(successful),
            "archived_parts": archived,
            "comparison": comparison,
            "errors": errors,
        }
        state.complete_job(lease.run_id, result.row_count, summary)
        return lease, result, {"noop": False, **summary}
    except Exception as exc:
        state.record_source_failure(source, f"{type(exc).__name__}: {exc}")
        state.fail_job(lease.run_id, f"{type(exc).__name__}: {exc}")
        raise


def _select_expirations(values: Iterable[str], today: date) -> list[str]:
    parsed = [(datetime.fromisoformat(value).date(), value) for value in values]
    eligible = [(expiry, raw) for expiry, raw in parsed if 21 <= (expiry - today).days <= 90]
    pool = eligible or [(expiry, raw) for expiry, raw in parsed if expiry >= today]
    selected: list[str] = []
    for target in (30, 45, 60):
        if not pool:
            break
        _, raw = min(pool, key=lambda item: abs((item[0] - today).days - target))
        if raw not in selected:
            selected.append(raw)
    return selected


def snapshot_options(
    store: DatasetStore,
    state: StateStore,
    symbols: list[str],
    run_date: date,
) -> tuple[JobLease, WriteResult | None, list[str]]:
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    digest = hashlib.sha256(",".join(normalized_symbols).encode()).hexdigest()[:10]
    identity = f"{run_date.isoformat()}-{digest}"
    lease = state.acquire_job("snapshot_options", identity, {"symbols": normalized_symbols})
    if not lease.acquired:
        return lease, None, []
    assert lease.run_id is not None
    provider = YFinanceProvider()
    retrieved_at = _utc_now()
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    try:
        for symbol in normalized_symbols:
            try:
                ticker = provider.yf.Ticker(symbol)
                history = ticker.history(period="10d", auto_adjust=False, actions=False)
                spot = _float_or_none(history["Close"].iloc[-1])
                if not spot:
                    raise RuntimeError("missing underlying price")
                expirations = _select_expirations(ticker.options, run_date)
                raw_chains: dict[str, Any] = {}
                for expiration in expirations:
                    chain = provider.option_chain(symbol, expiration)
                    raw_chains[expiration] = chain
                    for option_type in ("call", "put"):
                        for item in chain[f"{option_type}s"]:
                            bid = _float_or_none(item.get("bid"))
                            ask = _float_or_none(item.get("ask"))
                            mid = (bid + ask) / 2 if bid is not None and ask is not None else None
                            rows.append(
                                {
                                    "source": "yfinance",
                                    "source_symbol": symbol,
                                    "symbol": symbol,
                                    "market": "us",
                                    "retrieved_at": retrieved_at.isoformat(),
                                    "effective_at": retrieved_at.isoformat(),
                                    "contract_symbol": str(item.get("contractSymbol") or ""),
                                    "option_type": option_type,
                                    "expiration": expiration,
                                    "strike": _float_or_none(item.get("strike")),
                                    "underlying_price": spot,
                                    "last_price": _float_or_none(item.get("lastPrice")),
                                    "bid": bid,
                                    "ask": ask,
                                    "mid": mid,
                                    "quote_spread_ratio": (
                                        (ask - bid) / mid
                                        if mid and bid is not None and ask is not None
                                        else None
                                    ),
                                    "volume": _float_or_none(item.get("volume")),
                                    "open_interest": _float_or_none(item.get("openInterest")),
                                    "implied_volatility": _float_or_none(
                                        item.get("impliedVolatility")
                                    ),
                                    "last_trade_at": item.get("lastTradeDate"),
                                    "currency": item.get("currency") or "USD",
                                    "contract_size": item.get("contractSize") or "REGULAR",
                                    "schema_version": 1,
                                }
                            )
                store.write_raw(
                    "yfinance",
                    "option_chain",
                    "us",
                    run_date,
                    {"symbol": symbol, "underlying_price": spot, "chains": raw_chains},
                    f"{run_date.isoformat()}-{symbol}",
                    {"expirations": expirations},
                )
            except Exception as exc:
                failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
        if not rows:
            raise RuntimeError("no option rows collected; " + "; ".join(failures))
        with ProcessLock(store.root / "state" / "parquet-writer.lock"):
            result = store.write_rows(
                "option_chain",
                "us",
                "yfinance",
                run_date,
                rows,
                identity,
                validator=validate_option_row,
            )
        state.set_watermark("yfinance", "option_chain", "us", retrieved_at.isoformat())
        state.record_source_success("yfinance")
        state.complete_job(lease.run_id, result.row_count)
        return lease, result, failures
    except Exception as exc:
        state.record_source_failure("yfinance", f"{type(exc).__name__}: {exc}")
        state.fail_job(lease.run_id, f"{type(exc).__name__}: {exc}")
        raise
