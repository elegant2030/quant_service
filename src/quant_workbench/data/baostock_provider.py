from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from quant_workbench.core.models import Bar, Exchange, Instrument
from quant_workbench.data.universe import (
    UniverseBuildResult,
    UniverseMember,
    round_robin_sample,
    sector_counts,
)


def _decimal(value: str) -> Decimal | None:
    if value in ("", None):
        return None
    try:
        return Decimal(value)
    except Exception:
        return None


class BaoStockProvider:
    """Free A-share provider using BaoStock's anonymous data service."""

    def __init__(self) -> None:
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError("Install the data extras: pip install -e '.[data]'") from exc
        self.bs = bs
        response = self.bs.login()
        if response.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {response.error_msg}")

    def close(self) -> None:
        self.bs.logout()

    @staticmethod
    def _rows(result: Any) -> list[dict[str, str]]:
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock query failed: {result.error_msg}")
        rows: list[dict[str, str]] = []
        while result.next():
            rows.append(dict(zip(result.fields, result.get_row_data())))
        return rows

    def build_universe(self, target_size: int = 220) -> UniverseBuildResult:
        industry_rows = self._rows(self.bs.query_stock_industry())
        industry_by_code = {
            row["code"]: row.get("industry") or "未分类"
            for row in industry_rows
            if row.get("code")
        }
        index_rows = self._rows(self.bs.query_hs300_stocks())
        candidates: dict[str, list[UniverseMember]] = {}
        for row in index_rows:
            code = row.get("code", "")
            if "." not in code:
                continue
            prefix, symbol = code.split(".", 1)
            exchange = Exchange.SSE if prefix == "sh" else Exchange.SZSE
            sector = industry_by_code.get(code, "未分类")
            candidates.setdefault(sector, []).append(
                UniverseMember(
                    symbol=symbol,
                    name=row.get("code_name") or symbol,
                    exchange=exchange,
                    sector=sector,
                )
            )
        selected = round_robin_sample(candidates, target_size)
        errors = (
            ()
            if len(selected) >= target_size
            else (f"HS300 returned only {len(selected)} usable classified constituents",)
        )
        return UniverseBuildResult(selected, target_size, sector_counts(selected), errors)

    def bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        frequency: str = "1d",
        adjusted: bool = False,
    ) -> list[Bar]:
        if frequency != "1d":
            raise ValueError("BaoStock prototype adapter currently supports daily bars only")
        prefix = "sh" if instrument.exchange == Exchange.SSE else "sz"
        fields = "date,open,high,low,close,preclose,volume,tradestatus,isST"
        result = self.bs.query_history_k_data_plus(
            f"{prefix}.{instrument.symbol}",
            fields,
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            frequency="d",
            adjustflag="2" if adjusted else "3",
        )
        bars: list[Bar] = []
        for row in self._rows(result):
            if row.get("tradestatus") != "1":
                continue
            values = {key: _decimal(row.get(key, "")) for key in ("open", "high", "low", "close")}
            if any(value is None or value <= 0 for value in values.values()):
                continue
            bars.append(
                Bar(
                    instrument=instrument,
                    timestamp=datetime.fromisoformat(row["date"]),
                    open=values["open"],
                    high=values["high"],
                    low=values["low"],
                    close=values["close"],
                    volume=_decimal(row.get("volume", "")) or Decimal("0"),
                    previous_close=_decimal(row.get("preclose", "")),
                )
            )
        return bars

    def quarterly_indicators(
        self, instrument: Instrument, year: int, quarter: int
    ) -> dict[str, list[dict[str, str]]]:
        prefix = "sh" if instrument.exchange == Exchange.SSE else "sz"
        code = f"{prefix}.{instrument.symbol}"
        queries: dict[str, Callable[..., Any]] = {
            "profit": self.bs.query_profit_data,
            "operation": self.bs.query_operation_data,
            "growth": self.bs.query_growth_data,
            "balance": self.bs.query_balance_data,
            "cash_flow": self.bs.query_cash_flow_data,
        }
        return {
            name: self._rows(query(code=code, year=year, quarter=quarter))
            for name, query in queries.items()
        }
