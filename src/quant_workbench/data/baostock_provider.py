from __future__ import annotations

from datetime import date, datetime
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
        self.relogins = 0
        response = self.bs.login()
        if response.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {response.error_msg}")

    def close(self) -> None:
        self.bs.logout()

    def _query(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a BaoStock query; re-login once if the anonymous session was dropped.

        BaoStock keeps one anonymous session per client and silently invalidates it
        when another process logs in (e.g. the background pipeline), returning
        ``用户未登录`` for every later call.
        """
        result = function(*args, **kwargs)
        if result.error_code != "0" and "未登录" in str(result.error_msg):
            self.relogins += 1
            response = self.bs.login()
            if response.error_code != "0":
                raise RuntimeError(f"BaoStock re-login failed: {response.error_msg}")
            result = function(*args, **kwargs)
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock query failed: {result.error_msg}")
        return result

    @staticmethod
    def _rows(result: Any) -> list[dict[str, str]]:
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock query failed: {result.error_msg}")
        rows: list[dict[str, str]] = []
        while result.next():
            rows.append(dict(zip(result.fields, result.get_row_data(), strict=True)))
        return rows

    def build_universe(self, target_size: int = 220) -> UniverseBuildResult:
        industry_rows = self._rows(self.bs.query_stock_industry())
        industry_by_code = {
            row["code"]: row.get("industry") or "未分类" for row in industry_rows if row.get("code")
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

    @staticmethod
    def _code(instrument: Instrument) -> str:
        prefix = "sh" if instrument.exchange == Exchange.SSE else "sz"
        return f"{prefix}.{instrument.symbol}"

    def adjust_factors(self, instrument: Instrument, start: date, end: date) -> dict[date, Decimal]:
        """Single-event back-adjustment factor per ex-date in ``[start, end]``.

        BaoStock's ``backAdjustFactor`` is cumulative since listing, so the per-event
        factor is the ratio to the previous event. The query starts from BaoStock's
        default (2015-01-01) so the first event inside the window has a predecessor.
        """
        rows = self._rows(
            self._query(
                self.bs.query_adjust_factor, self._code(instrument), end_date=end.isoformat()
            )
        )
        rows.sort(key=lambda row: row.get("dividOperateDate", ""))
        previous = Decimal("1")
        factors: dict[date, Decimal] = {}
        for row in rows:
            cumulative = _decimal(row.get("backAdjustFactor", ""))
            if cumulative is None or cumulative <= 0:
                continue
            when = date.fromisoformat(row["dividOperateDate"])
            if start <= when <= end:
                factors[when] = cumulative / previous
            previous = cumulative
        return factors

    def bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        frequency: str = "1d",
        adjusted: bool = False,
    ) -> list[Bar]:
        """Daily bars. ``adjusted=False`` (default) returns raw prices with per-event
        ``adj_factor``; ``adjusted=True`` returns BaoStock's forward-adjusted series for
        ad-hoc research only and must never be written to canonical storage."""
        if frequency != "1d":
            raise ValueError("BaoStock prototype adapter currently supports daily bars only")
        prefix = "sh" if instrument.exchange == Exchange.SSE else "sz"
        fields = "date,open,high,low,close,preclose,volume,tradestatus,isST"
        factors = {} if adjusted else self.adjust_factors(instrument, start.date(), end.date())
        # Ex-dates that fall inside a suspension have no bar; carry their factor forward
        # to the first traded session on or after the event.
        pending_events = sorted(factors.items())
        pending_factor = Decimal("1")
        result = self._query(
            self.bs.query_history_k_data_plus,
            f"{prefix}.{instrument.symbol}",
            fields,
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            frequency="d",
            adjustflag="2" if adjusted else "3",
        )
        bars: list[Bar] = []
        for row in self._rows(result):
            session = datetime.fromisoformat(row["date"])
            while pending_events and pending_events[0][0] <= session.date():
                pending_factor *= pending_events.pop(0)[1]
            if row.get("tradestatus") != "1":
                continue
            values = {key: _decimal(row.get(key, "")) for key in ("open", "high", "low", "close")}
            if any(value is None or value <= 0 for value in values.values()):
                continue
            session_factor, pending_factor = pending_factor, Decimal("1")
            bars.append(
                Bar(
                    instrument=instrument,
                    timestamp=session,
                    open=values["open"],
                    high=values["high"],
                    low=values["low"],
                    close=values["close"],
                    volume=_decimal(row.get("volume", "")) or Decimal("0"),
                    previous_close=_decimal(row.get("preclose", "")),
                    adj_factor=None if adjusted else session_factor,
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
