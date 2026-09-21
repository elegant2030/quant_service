from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
from math import isnan
from typing import Any

from quant_workbench.core.models import Bar, FinancialSnapshot, Instrument
from quant_workbench.data.adjust import event_factor
from quant_workbench.data.universe import (
    US_SECTORS,
    UniverseBuildResult,
    UniverseMember,
    round_robin_sample,
    sector_counts,
)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isnan(number):
        return None
    return Decimal(str(number))


class YFinanceProvider:
    """Free prototype adapter. Yahoo data terms restrict it to personal/research use."""

    def __init__(self) -> None:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("Install the data extras: pip install -e '.[data]'") from exc
        self.yf = yf

    def build_universe(self, target_size: int = 220) -> UniverseBuildResult:
        per_sector = max(22, (target_size + len(US_SECTORS) - 1) // len(US_SECTORS) + 2)
        candidates: dict[str, list[UniverseMember]] = {}
        errors: list[str] = []
        exchange_map = {"NMS": "NASDAQ", "NYQ": "NYSE", "ASE": "AMEX"}
        from quant_workbench.core.models import Exchange

        for sector in US_SECTORS:
            try:
                query = self.yf.EquityQuery(
                    "and",
                    [
                        self.yf.EquityQuery("eq", ["region", "us"]),
                        self.yf.EquityQuery("eq", ["sector", sector]),
                        self.yf.EquityQuery("is-in", ["exchange", "NMS", "NYQ", "ASE"]),
                        self.yf.EquityQuery("gt", ["intradaymarketcap", 500_000_000]),
                    ],
                )
                response = self.yf.screen(
                    query,
                    size=min(250, per_sector + 10),
                    sortField="intradaymarketcap",
                    sortAsc=False,
                )
                sector_members: list[UniverseMember] = []
                for quote in response.get("quotes", []):
                    exchange_name = exchange_map.get(str(quote.get("exchange")))
                    symbol = str(quote.get("symbol") or "")
                    if not exchange_name or not symbol or re.search(r"-P[A-Z]$", symbol):
                        continue
                    sector_members.append(
                        UniverseMember(
                            symbol=symbol,
                            name=str(quote.get("shortName") or quote.get("longName") or symbol),
                            exchange=Exchange(exchange_name),
                            sector=sector,
                            market_cap=_decimal(quote.get("marketCap")),
                        )
                    )
                    if len(sector_members) == per_sector:
                        break
                candidates[sector] = sector_members
            except Exception as exc:
                errors.append(f"{sector}: {type(exc).__name__}: {exc}")
        selected = round_robin_sample(candidates, target_size)
        return UniverseBuildResult(selected, target_size, sector_counts(selected), tuple(errors))

    def bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        frequency: str = "1d",
        adjusted: bool = False,
    ) -> list[Bar]:
        """Daily bars. ``adjusted=False`` (default) returns true raw prices plus
        dividend / split facts; ``adjusted=True`` returns Yahoo's adjusted series for
        ad-hoc research only and must never be written to canonical storage."""
        frame = self.yf.Ticker(instrument.symbol).history(
            start=start.date().isoformat(),
            end=(end.date() + timedelta(days=1)).isoformat(),
            interval=frequency,
            auto_adjust=adjusted,
            actions=True,
            repair=False,
        )
        return self._frame_to_bars(instrument, frame, raw=not adjusted)

    def bars_many(
        self,
        instruments: list[Instrument],
        start: datetime,
        end: datetime,
        frequency: str = "1d",
        chunk_size: int = 50,
        adjusted: bool = False,
    ) -> tuple[dict[str, list[Bar]], list[str]]:
        results: dict[str, list[Bar]] = {}
        errors: list[str] = []
        by_symbol = {instrument.symbol: instrument for instrument in instruments}
        symbols = list(by_symbol)
        for offset in range(0, len(symbols), chunk_size):
            chunk = symbols[offset : offset + chunk_size]
            try:
                frame = self.yf.download(
                    chunk,
                    start=start.date().isoformat(),
                    end=(end.date() + timedelta(days=1)).isoformat(),
                    interval=frequency,
                    auto_adjust=adjusted,
                    actions=True,
                    repair=False,
                    progress=False,
                    group_by="ticker",
                    threads=True,
                    multi_level_index=True,
                )
                for symbol in chunk:
                    try:
                        if getattr(frame.columns, "nlevels", 1) == 1:
                            part = frame
                        elif symbol in frame.columns.get_level_values(0):
                            part = frame[symbol]
                        else:
                            part = frame.xs(symbol, axis=1, level=1)
                        results[symbol] = self._frame_to_bars(
                            by_symbol[symbol], part, raw=not adjusted
                        )
                    except Exception as exc:
                        errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            except Exception as exc:
                errors.extend(f"{symbol}: {type(exc).__name__}: {exc}" for symbol in chunk)
        return results, errors

    @staticmethod
    def _frame_to_bars(instrument: Instrument, frame: Any, raw: bool = True) -> list[Bar]:
        """Convert a yfinance history frame into bars.

        Yahoo's ``Close`` is split-adjusted (retroactively divided by later splits) but
        not dividend-adjusted. With ``raw=True`` the split adjustment is undone so the
        stored price is exactly what traded that day and never changes when a future
        split happens; ``split_ratio`` / ``dividend`` are recorded on the ex-date row
        and ``adj_factor`` is the single-session back-adjustment factor.
        """
        records: list[dict[str, Any]] = []
        for index, row in frame.iterrows():
            timestamp = index.to_pydatetime()
            if timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            values = {name: _decimal(row.get(name)) for name in ("Open", "High", "Low", "Close")}
            if None in values.values():
                continue
            split = _decimal(row.get("Stock Splits"))
            dividend = _decimal(row.get("Dividends"))
            records.append(
                {
                    "timestamp": timestamp,
                    "open": values["Open"],
                    "high": values["High"],
                    "low": values["Low"],
                    "close": values["Close"],
                    "volume": _decimal(row.get("Volume")) or Decimal("0"),
                    "split": split if split and split > 0 else None,
                    "dividend": dividend if dividend and dividend > 0 else None,
                }
            )
        records.sort(key=lambda item: item["timestamp"])
        if raw:
            # Undo Yahoo's retroactive split adjustment: prices AND dividend amounts are
            # expressed in post-split share terms, so multiply both (and divide volume)
            # by every split that happened strictly after the session.
            later_splits = Decimal("1")
            for record in reversed(records):
                if later_splits != 1:
                    for name in ("open", "high", "low", "close"):
                        record[name] = record[name] * later_splits
                    record["volume"] = record["volume"] / later_splits
                    if record["dividend"]:
                        record["dividend"] = record["dividend"] * later_splits
                if record["split"]:
                    later_splits *= record["split"]
        result: list[Bar] = []
        previous_close: Decimal | None = None
        for record in records:
            open_, high, low, close = (record[name] for name in ("open", "high", "low", "close"))
            if low > min(open_, close) or high < max(open_, close):
                previous_close = close
                continue
            adj_factor: Decimal | None = None
            if raw:
                try:
                    adj_factor = event_factor(previous_close, record["dividend"], record["split"])
                except ValueError:
                    # Dividend on the first row of a window: the caller must fetch a
                    # warm-up period; flag by leaving adj_factor unset.
                    adj_factor = None
            result.append(
                Bar(
                    instrument=instrument,
                    timestamp=record["timestamp"],
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=record["volume"],
                    previous_close=previous_close,
                    dividend=record["dividend"] if raw else None,
                    split_ratio=record["split"] if raw else None,
                    adj_factor=adj_factor,
                )
            )
            previous_close = close
        return result

    def financials(self, instrument: Instrument, frequency: str = "quarterly") -> list[FinancialSnapshot]:
        ticker = self.yf.Ticker(instrument.symbol)
        income = ticker.get_income_stmt(freq=frequency)
        balance = ticker.get_balance_sheet(freq=frequency)
        cashflow = ticker.get_cashflow(freq=frequency)
        columns = sorted(set(income.columns) | set(balance.columns) | set(cashflow.columns))

        def get(frame: Any, column: Any, *names: str) -> Decimal | None:
            for name in names:
                if name in frame.index and column in frame.columns:
                    value = _decimal(frame.at[name, column])
                    if value is not None:
                        return value
            return None

        snapshots: list[FinancialSnapshot] = []
        raw: list[dict[str, Any]] = []
        for column in columns:
            revenue = get(income, column, "TotalRevenue", "OperatingRevenue")
            net_income = get(income, column, "NetIncome", "NetIncomeCommonStockholders")
            operating_income = get(income, column, "OperatingIncome")
            gross_profit = get(income, column, "GrossProfit")
            raw.append(
                {
                    "column": column,
                    "revenue": revenue,
                    "net_income": net_income,
                    "operating_income": operating_income,
                    "gross_profit": gross_profit,
                    "operating_cash_flow": get(cashflow, column, "OperatingCashFlow", "TotalCashFromOperatingActivities"),
                    "free_cash_flow": get(cashflow, column, "FreeCashFlow"),
                    "total_assets": get(balance, column, "TotalAssets"),
                    "total_equity": get(balance, column, "StockholdersEquity", "TotalStockholderEquity"),
                    "total_debt": get(balance, column, "TotalDebt"),
                    "cash": get(
                        balance,
                        column,
                        "CashCashEquivalentsAndShortTermInvestments",
                        "CashAndCashEquivalents",
                    ),
                    "shares_outstanding": get(balance, column, "OrdinarySharesNumber", "ShareIssued"),
                }
            )
        raw.sort(key=lambda item: item["column"])
        for index, item in enumerate(raw):
            previous = raw[index - 1] if index else None

            def growth(field: str) -> Decimal | None:
                current_value = item[field]
                previous_value = previous[field] if previous else None
                if current_value is None or previous_value in (None, Decimal("0")):
                    return None
                return current_value / abs(previous_value) - 1

            revenue = item["revenue"]
            snapshots.append(
                FinancialSnapshot(
                    symbol=instrument.symbol,
                    as_of=item["column"].date(),
                    source="yfinance",
                    revenue=revenue,
                    net_income=item["net_income"],
                    operating_cash_flow=item["operating_cash_flow"],
                    free_cash_flow=item["free_cash_flow"],
                    total_assets=item["total_assets"],
                    total_equity=item["total_equity"],
                    total_debt=item["total_debt"],
                    cash=item["cash"],
                    shares_outstanding=item["shares_outstanding"],
                    revenue_growth=growth("revenue"),
                    earnings_growth=growth("net_income"),
                    gross_margin=item["gross_profit"] / revenue
                    if item["gross_profit"] is not None and revenue
                    else None,
                    operating_margin=item["operating_income"] / revenue
                    if item["operating_income"] is not None and revenue
                    else None,
                )
            )
        return snapshots

    EXCHANGE_MAP = {"NMS": "NASDAQ", "NYQ": "NYSE", "ASE": "AMEX"}

    def sector_members(
        self, max_per_sector: int = 250, minimum_market_cap: int = 500_000_000
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Current Yahoo sector for the largest US listings (one screen per sector)."""
        members: list[dict[str, Any]] = []
        errors: list[str] = []
        for sector in US_SECTORS:
            try:
                query = self.yf.EquityQuery(
                    "and",
                    [
                        self.yf.EquityQuery("eq", ["region", "us"]),
                        self.yf.EquityQuery("eq", ["sector", sector]),
                        self.yf.EquityQuery("is-in", ["exchange", *self.EXCHANGE_MAP]),
                        self.yf.EquityQuery("gt", ["intradaymarketcap", minimum_market_cap]),
                    ],
                )
                response = self.yf.screen(
                    query, size=min(250, max_per_sector), sortField="intradaymarketcap", sortAsc=False
                )
                for quote in response.get("quotes", []):
                    exchange = self.EXCHANGE_MAP.get(str(quote.get("exchange")))
                    symbol = str(quote.get("symbol") or "")
                    if not exchange or not symbol:
                        continue
                    members.append(
                        {
                            "symbol": symbol,
                            "exchange": exchange,
                            "name": str(quote.get("shortName") or quote.get("longName") or symbol),
                            "code": sector,
                            "market_cap": quote.get("marketCap"),
                            "quote_type": quote.get("quoteType"),
                        }
                    )
            except Exception as exc:
                errors.append(f"{sector}: {type(exc).__name__}: {exc}")
        return members, errors

    def consensus(self, symbol: str) -> dict[str, Any]:
        """Analyst estimates as currently published (no history exists upstream).

        Returns plain records so callers can persist the raw payload unchanged.
        """
        ticker = self.yf.Ticker(symbol)

        def records(frame: Any) -> list[dict[str, Any]]:
            if frame is None or getattr(frame, "empty", True):
                return []
            return frame.reset_index().to_dict(orient="records")

        return {
            "earnings_estimate": records(ticker.earnings_estimate),
            "revenue_estimate": records(ticker.revenue_estimate),
            "eps_trend": records(ticker.eps_trend),
            "eps_revisions": records(ticker.eps_revisions),
            "price_targets": dict(ticker.analyst_price_targets or {}),
            "recommendations": records(ticker.recommendations_summary),
        }

    def option_expirations(self, symbol: str) -> tuple[str, ...]:
        return tuple(self.yf.Ticker(symbol).options)

    def option_chain(self, symbol: str, expiration: str) -> dict[str, list[dict[str, Any]]]:
        chain = self.yf.Ticker(symbol).option_chain(expiration)
        return {
            "calls": chain.calls.to_dict(orient="records"),
            "puts": chain.puts.to_dict(orient="records"),
        }
