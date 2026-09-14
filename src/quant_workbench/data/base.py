from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Sequence

from quant_workbench.core.models import Bar, FinancialSnapshot, Instrument


class MarketDataProvider(Protocol):
    def bars(
        self, instrument: Instrument, start: datetime, end: datetime, frequency: str = "1d"
    ) -> Sequence[Bar]: ...


class FundamentalDataProvider(Protocol):
    def financials(
        self, instrument: Instrument, start: date | None = None, end: date | None = None
    ) -> Sequence[FinancialSnapshot]: ...


class NewsProvider(Protocol):
    def articles(self, query: str, start: datetime, end: datetime) -> Sequence[dict[str, str]]: ...

