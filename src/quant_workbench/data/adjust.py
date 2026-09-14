"""Read-time price adjustment for raw daily bars.

Canonical daily bars (schema_version >= 2) store prices exactly as traded plus a
single-event back-adjustment factor ``adj_factor`` on the session where a corporate
action took effect (1.0 otherwise). Nothing stored ever changes retroactively; the
cumulative adjustment is derived here whenever a comparable price series is needed.

Conventions
-----------
- ``back``   (后复权): earliest prices unchanged, later prices scaled up so the
  series is continuous. ``price_back = raw * cumprod(adj_factor)``.
- ``forward`` (前复权): latest price unchanged, earlier prices scaled down so the
  series ends at today's quote. ``price_forward = price_back / cumprod_at_last``.

Both give identical returns; ``forward`` matches live quotes, ``back`` is stable
over time for stored research results.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

PRICE_COLUMNS = ("open", "high", "low", "close")
ONE = Decimal("1")


def event_factor(
    previous_close: Decimal | float | None,
    dividend: Decimal | float | None = None,
    split_ratio: Decimal | float | None = None,
) -> Decimal:
    """Single-session back-adjustment factor from cash dividend and split ratio.

    A split of ``r`` (``2`` for 2-for-1, ``0.1`` for a 1-for-10 reverse split) scales
    later raw prices by ``r``. A cash dividend ``D`` with prior close ``P`` scales later
    prices by ``P / (P - D)`` (the exact inverse of Yahoo's ``1 - D/P`` convention).
    """
    factor = ONE
    if split_ratio not in (None, 0):
        ratio = Decimal(str(split_ratio))
        if ratio <= 0:
            raise ValueError("split_ratio must be positive")
        factor *= ratio
    if dividend not in (None, 0):
        amount = Decimal(str(dividend))
        if amount < 0:
            raise ValueError("dividend must be non-negative")
        if amount > 0:
            if previous_close in (None, 0):
                raise ValueError("previous_close is required to adjust for a dividend")
            prior = Decimal(str(previous_close))
            if amount >= prior:
                raise ValueError("dividend must be smaller than the previous close")
            factor *= prior / (prior - amount)
    return factor


def apply_adjustment(frame: Any, mode: str = "forward", inplace: bool = False) -> Any:
    """Add ``*_adj`` columns and ``cum_adj_factor`` to a pandas frame of raw bars.

    Requires ``symbol``, ``session_date`` and price columns. Rows without ``adj_factor``
    (legacy schema 1) are treated as factor 1.0 and flagged in ``adjustment_applied``.
    The input order is preserved; adjustment is computed per symbol in date order.
    """
    import pandas as pd

    if mode not in {"forward", "back"}:
        raise ValueError("mode must be 'forward' or 'back'")
    result = frame if inplace else frame.copy()
    if "adj_factor" in result.columns:
        factors = pd.to_numeric(result["adj_factor"], errors="coerce")
        result["adjustment_applied"] = factors.notna()
    else:
        factors = pd.Series(float("nan"), index=result.index)
        result["adjustment_applied"] = False
    factors = factors.fillna(1.0).astype(float)
    order = result.sort_values(["symbol", "session_date"]).index
    cumulative = factors.loc[order].groupby(result.loc[order, "symbol"], sort=False).cumprod()
    cumulative = cumulative.reindex(result.index)
    if mode == "forward":
        last = (
            cumulative.loc[order]
            .groupby(result.loc[order, "symbol"], sort=False)
            .transform("last")
            .reindex(result.index)
        )
        scale = cumulative / last
    else:
        scale = cumulative
    result["cum_adj_factor"] = scale
    for column in PRICE_COLUMNS:
        if column in result.columns:
            result[f"{column}_adj"] = pd.to_numeric(result[column], errors="coerce") * scale
    if "volume" in result.columns:
        result["volume_adj"] = pd.to_numeric(result["volume"], errors="coerce") / scale
    return result
