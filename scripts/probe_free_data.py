"""Small, non-destructive quality probe for the selected free data providers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument
from quant_workbench.data.akshare_provider import AkShareProvider
from quant_workbench.data.yfinance_provider import YFinanceProvider


def summarize_bars(bars: Sequence[Bar]) -> dict[str, Any]:
    invalid = sum(
        bar.low > min(bar.open, bar.close)
        or bar.high < max(bar.open, bar.close)
        or min(bar.open, bar.high, bar.low, bar.close) <= 0
        for bar in bars
    )
    return {
        "rows": len(bars),
        "first": bars[0].timestamp.date().isoformat() if bars else None,
        "last": bars[-1].timestamp.date().isoformat() if bars else None,
        "latest_close": str(bars[-1].close) if bars else None,
        "zero_volume_rows": sum(bar.volume == Decimal("0") for bar in bars),
        "invalid_ohlc_rows": invalid,
    }


def main() -> None:
    end = datetime.now()
    start = end - timedelta(days=120)
    report: dict[str, Any] = {}

    try:
        us = Instrument("AAPL", Exchange.NASDAQ, AssetClass.EQUITY, Currency.USD)
        provider = YFinanceProvider()
        bars = provider.bars(us, start, end)
        financials = provider.financials(us)
        expirations = provider.option_expirations(us.symbol)
        report["yfinance"] = {
            "status": "ok",
            "bars": summarize_bars(bars),
            "financial_periods": len(financials),
            "financial_fields_latest": sum(
                value is not None
                for name, value in zip(financials[-1].__slots__, (getattr(financials[-1], n) for n in financials[-1].__slots__))
                if name not in {"symbol", "as_of", "published_at", "source"}
            )
            if financials
            else 0,
            "option_expirations": len(expirations),
        }
    except Exception as exc:
        report["yfinance"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    try:
        cn = Instrument(
            "600519", Exchange.SSE, AssetClass.EQUITY, Currency.CNY, lot_size=Decimal("100")
        )
        provider = AkShareProvider()
        report["akshare"] = {"status": "ok"}
        try:
            bars = provider.bars(cn, start, end)
            report["akshare"]["bars"] = summarize_bars(bars)
        except Exception as exc:
            report["akshare"]["status"] = "partial"
            report["akshare"]["bars_error"] = f"{type(exc).__name__}: {exc}"
        try:
            statements = provider.raw_financial_statements(cn)
            report["akshare"]["statement_rows"] = {
                name: len(frame) for name, frame in statements.items()
            }
            report["akshare"]["statement_columns"] = {
                name: len(frame.columns) for name, frame in statements.items()
            }
        except Exception as exc:
            report["akshare"]["status"] = "partial"
            report["akshare"]["statements_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        report["akshare"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
