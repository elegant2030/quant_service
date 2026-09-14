from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from quant_workbench.core.models import Bar, FinancialSnapshot, Instrument


class CsvMarketDataProvider:
    """Load normalized daily bars with timestamp,open,high,low,close[,volume,previous_close]."""

    def __init__(self, files: dict[str, Path]):
        self.files = files

    def bars(
        self, instrument: Instrument, start: datetime, end: datetime, frequency: str = "1d"
    ) -> list[Bar]:
        if frequency != "1d":
            raise ValueError("the CSV provider currently supports daily bars only")
        result: list[Bar] = []
        with self.files[instrument.id].open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                timestamp = datetime.fromisoformat(row["timestamp"])
                if not start <= timestamp <= end:
                    continue
                result.append(
                    Bar(
                        instrument=instrument,
                        timestamp=timestamp,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row.get("volume") or "0"),
                        previous_close=Decimal(row["previous_close"])
                        if row.get("previous_close")
                        else None,
                    )
                )
        return sorted(result, key=lambda bar: bar.timestamp)


class CsvFundamentalDataProvider:
    """Load one normalized financial-statement file per instrument."""

    def __init__(self, files: dict[str, Path]):
        self.files = files

    def financials(
        self, instrument: Instrument, start: date | None = None, end: date | None = None
    ) -> list[FinancialSnapshot]:
        result: list[FinancialSnapshot] = []
        with self.files[instrument.id].open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                as_of = datetime.fromisoformat(row["as_of"]).date()
                if start and as_of < start:
                    continue
                if end and as_of > end:
                    continue
                values = {
                    key: Decimal(value) if value not in (None, "") else None
                    for key, value in row.items()
                    if key not in {"symbol", "as_of", "published_at", "source"}
                }
                result.append(
                    FinancialSnapshot(
                        symbol=row.get("symbol") or instrument.symbol,
                        as_of=as_of,
                        published_at=datetime.fromisoformat(row["published_at"])
                        if row.get("published_at")
                        else None,
                        source=row.get("source") or "csv",
                        **values,
                    )
                )
        return sorted(result, key=lambda snapshot: snapshot.as_of)
