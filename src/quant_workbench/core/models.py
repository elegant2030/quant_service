from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Exchange(StrEnum):
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    AMEX = "AMEX"
    SSE = "SSE"
    SZSE = "SZSE"
    BSE = "BSE"
    CRYPTO = "CRYPTO"


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    OPTION = "option"
    FUTURE = "future"
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_PERPETUAL = "crypto_perpetual"


class Currency(StrEnum):
    USD = "USD"
    CNY = "CNY"
    USDT = "USDT"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    exchange: Exchange
    asset_class: AssetClass
    currency: Currency
    multiplier: Decimal = Decimal("1")
    lot_size: Decimal = Decimal("1")
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def id(self) -> str:
        return f"{self.exchange}:{self.symbol.upper()}"


@dataclass(frozen=True, slots=True)
class Bar:
    instrument: Instrument
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    previous_close: Decimal | None = None

    def __post_init__(self) -> None:
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.low > self.high or not self.low <= self.open <= self.high:
            raise ValueError("invalid OHLC range")
        if not self.low <= self.close <= self.high:
            raise ValueError("close is outside the high/low range")


@dataclass(frozen=True, slots=True)
class FinancialSnapshot:
    symbol: str
    as_of: date
    published_at: datetime | None = None
    source: str = ""
    revenue: Decimal | None = None
    net_income: Decimal | None = None
    operating_cash_flow: Decimal | None = None
    free_cash_flow: Decimal | None = None
    total_assets: Decimal | None = None
    total_equity: Decimal | None = None
    total_debt: Decimal | None = None
    cash: Decimal | None = None
    shares_outstanding: Decimal | None = None
    market_cap: Decimal | None = None
    revenue_growth: Decimal | None = None
    earnings_growth: Decimal | None = None
    gross_margin: Decimal | None = None
    operating_margin: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TargetWeight:
    instrument: Instrument
    weight: Decimal
    reason: str = ""

    def __post_init__(self) -> None:
        if not Decimal("-1") <= self.weight <= Decimal("1"):
            raise ValueError("target weight must be between -1 and 1")


@dataclass(frozen=True, slots=True)
class Fill:
    instrument: Instrument
    timestamp: datetime
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    reason: str = ""


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    gross_exposure: Decimal
