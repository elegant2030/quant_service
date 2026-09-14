from __future__ import annotations

from typing import Any


PROVENANCE_FIELDS = {
    "source",
    "source_symbol",
    "symbol",
    "market",
    "retrieved_at",
    "effective_at",
    "schema_version",
}


def _missing(row: dict[str, Any], fields: set[str]) -> list[str]:
    return [f"missing:{field}" for field in sorted(fields) if row.get(field) in (None, "")]


def validate_bar_row(row: dict[str, Any]) -> list[str]:
    errors = _missing(
        row,
        PROVENANCE_FIELDS | {"open", "high", "low", "close", "session_date", "adjustment"},
    )
    try:
        open_, high, low, close = (float(row[name]) for name in ("open", "high", "low", "close"))
        if min(open_, high, low, close) <= 0:
            errors.append("non_positive_ohlc")
        if low > min(open_, close) or high < max(open_, close) or low > high:
            errors.append("invalid_ohlc_range")
        if float(row.get("volume", 0)) < 0:
            errors.append("negative_volume")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid_numeric_value")
    return errors


def validate_option_row(row: dict[str, Any]) -> list[str]:
    errors = _missing(
        row,
        PROVENANCE_FIELDS
        | {"contract_symbol", "option_type", "expiration", "strike", "underlying_price"},
    )
    if row.get("option_type") not in {"call", "put"}:
        errors.append("invalid_option_type")
    try:
        if float(row["strike"]) <= 0 or float(row["underlying_price"]) <= 0:
            errors.append("non_positive_price")
        bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
        if bid < 0 or ask < 0 or (ask and bid > ask):
            errors.append("invalid_quote")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid_numeric_value")
    return errors
