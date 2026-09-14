from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from math import isnan
import re
from typing import Any

from quant_workbench.core.models import Bar, FinancialSnapshot, Instrument
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
        frame = self.yf.Ticker(instrument.symbol).history(
            start=start.date().isoformat(),
            end=(end.date() + timedelta(days=1)).isoformat(),
            interval=frequency,
            auto_adjust=adjusted,
            actions=False,
            repair=False,
        )
        result: list[Bar] = []
        previous_close: Decimal | None = None
        for index, row in frame.iterrows():
            timestamp = index.to_pydatetime()
            if timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            close = _decimal(row.get("Close"))
            open_ = _decimal(row.get("Open"))
            high = _decimal(row.get("High"))
            low = _decimal(row.get("Low"))
            if None in (open_, high, low, close):
                continue
            if low > min(open_, close) or high < max(open_, close):
                continue
            result.append(
                Bar(
                    instrument=instrument,
                    timestamp=timestamp,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=_decimal(row.get("Volume")) or Decimal("0"),
                    previous_close=previous_close,
                )
            )
            previous_close = close
        return result

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
                    actions=False,
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
                        results[symbol] = self._frame_to_bars(by_symbol[symbol], part)
                    except Exception as exc:
                        errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            except Exception as exc:
                errors.extend(f"{symbol}: {type(exc).__name__}: {exc}" for symbol in chunk)
        return results, errors

    @staticmethod
    def _frame_to_bars(instrument: Instrument, frame: Any) -> list[Bar]:
        result: list[Bar] = []
        previous_close: Decimal | None = None
        for index, row in frame.iterrows():
            timestamp = index.to_pydatetime()
            if timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            close = _decimal(row.get("Close"))
            open_ = _decimal(row.get("Open"))
            high = _decimal(row.get("High"))
            low = _decimal(row.get("Low"))
            if None in (open_, high, low, close):
                continue
            if low > min(open_, close) or high < max(open_, close):
                continue
            result.append(
                Bar(
                    instrument=instrument,
                    timestamp=timestamp,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=_decimal(row.get("Volume")) or Decimal("0"),
                    previous_close=previous_close,
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

    def option_expirations(self, symbol: str) -> tuple[str, ...]:
        return tuple(self.yf.Ticker(symbol).options)

    def option_chain(self, symbol: str, expiration: str) -> dict[str, list[dict[str, Any]]]:
        chain = self.yf.Ticker(symbol).option_chain(expiration)
        return {
            "calls": chain.calls.to_dict(orient="records"),
            "puts": chain.puts.to_dict(orient="records"),
        }
