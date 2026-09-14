from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from math import isnan
from time import sleep
from typing import Any

from quant_workbench.core.models import Bar, Exchange, Instrument
from quant_workbench.data.universe import (
    UniverseBuildResult,
    UniverseMember,
    market_cap_value,
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


class AkShareProvider:
    """Free A-share prototype adapter backed by AKShare's public-source interfaces."""

    def __init__(self) -> None:
        try:
            import akshare as ak
            import requests
        except ImportError as exc:
            raise RuntimeError("Install the data extras: pip install -e '.[data]'") from exc
        self.ak = ak
        self.requests = requests

    def build_universe(self, target_size: int = 220) -> UniverseBuildResult:
        industries = self._call(self.ak.sw_index_first_info)
        candidates: dict[str, list[UniverseMember]] = {}
        errors: list[str] = []
        for _, industry in industries.iterrows():
            code = str(industry["行业代码"])
            sector = str(industry["行业名称"])
            try:
                frame = self._call(self.ak.sw_index_third_cons, symbol=code)
                values: list[UniverseMember] = []
                for _, row in frame.sort_values("市值", ascending=False).iterrows():
                    symbol = str(row["股票代码"]).split(".", 1)[0].zfill(6)
                    if symbol.startswith(("4", "8", "9")):
                        exchange = Exchange.BSE
                    elif symbol.startswith(("5", "6", "9")):
                        exchange = Exchange.SSE
                    else:
                        exchange = Exchange.SZSE
                    values.append(
                        UniverseMember(
                            symbol=symbol,
                            name=str(row.get("股票简称") or symbol),
                            exchange=exchange,
                            sector=str(row.get("申万1级") or sector),
                            market_cap=market_cap_value(row, "市值"),
                        )
                    )
                candidates[sector] = values
            except Exception as exc:
                errors.append(f"{sector}: {type(exc).__name__}: {exc}")
            sleep(0.75)
        selected = round_robin_sample(candidates, target_size)
        return UniverseBuildResult(selected, target_size, sector_counts(selected), tuple(errors))

    def _call(self, function: Any, /, **kwargs: Any) -> Any:
        for attempt in range(5):
            try:
                return function(**kwargs)
            except Exception as exc:
                retryable = isinstance(exc, self.requests.RequestException) or (
                    isinstance(exc, ValueError) and "No tables found" in str(exc)
                )
                if not retryable or attempt == 4:
                    raise
                sleep(2**attempt)
        raise RuntimeError("unreachable")

    def bars(
        self, instrument: Instrument, start: datetime, end: datetime, frequency: str = "1d"
    ) -> list[Bar]:
        if frequency != "1d":
            raise ValueError("AKShare prototype adapter currently supports daily bars only")
        fetch_start = start.date() - timedelta(days=10)
        frame = self._call(
            self.ak.stock_zh_a_hist,
            symbol=instrument.symbol,
            period="daily",
            start_date=fetch_start.strftime("%Y%m%d"),
            end_date=end.date().strftime("%Y%m%d"),
            adjust="",
            timeout=15,
        )
        result: list[Bar] = []
        previous_close: Decimal | None = None
        for _, row in frame.sort_values("日期").iterrows():
            timestamp = datetime.combine(row["日期"], datetime.min.time())
            open_ = _decimal(row.get("开盘"))
            high = _decimal(row.get("最高"))
            low = _decimal(row.get("最低"))
            close = _decimal(row.get("收盘"))
            if None in (open_, high, low, close):
                continue
            if timestamp >= start:
                result.append(
                    Bar(
                        instrument=instrument,
                        timestamp=timestamp,
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=(_decimal(row.get("成交量")) or Decimal("0")) * 100,
                        previous_close=previous_close,
                    )
                )
            previous_close = close
        return result

    def raw_financial_statements(self, instrument: Instrument) -> dict[str, Any]:
        prefix = "sh" if instrument.exchange == Exchange.SSE else "sz"
        stock = f"{prefix}{instrument.symbol}"
        return {
            statement: self._call(
                self.ak.stock_financial_report_sina, stock=stock, symbol=statement
            )
            for statement in ("资产负债表", "利润表", "现金流量表")
        }

    def disclosure_calendar(self, period: str) -> Any:
        return self._call(self.ak.stock_report_disclosure, market="沪深京", period=period)
