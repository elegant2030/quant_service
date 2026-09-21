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


BAR_SCHEMA_VERSION = 2
RAW_ADJUSTMENT = "raw"


def validate_bar_row(row: dict[str, Any]) -> list[str]:
    """Validate a daily bar row.

    schema_version 1 rows carry provider-adjusted prices (legacy cache import).
    schema_version >= 2 rows must be raw prices plus a positive single-event
    ``adj_factor`` so adjustment can be recomputed at read time.
    """
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
    try:
        version = int(row.get("schema_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= 2:
        if row.get("adjustment") != RAW_ADJUSTMENT:
            errors.append("schema2_requires_raw_adjustment")
        try:
            if float(row.get("adj_factor")) <= 0:
                errors.append("non_positive_adj_factor")
        except (TypeError, ValueError):
            errors.append("missing:adj_factor")
        try:
            if row.get("dividend") not in (None, "") and float(row["dividend"]) < 0:
                errors.append("negative_dividend")
            if row.get("split_ratio") not in (None, "") and float(row["split_ratio"]) <= 0:
                errors.append("non_positive_split_ratio")
        except (TypeError, ValueError):
            errors.append("invalid_corporate_action_value")
        try:
            if row.get("effective_at") and row.get("retrieved_at"):
                if str(row["effective_at"])[:10] > str(row["retrieved_at"])[:10]:
                    errors.append("effective_after_retrieved")
        except TypeError:
            errors.append("invalid_timestamp")
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


def validate_snapshot_row(row: dict[str, Any]) -> list[str]:
    """Shared rules for snapshot datasets: provenance plus ``effective_at`` equal to the
    capture time (a snapshot is only known from the moment it was taken)."""
    errors = _missing(row, PROVENANCE_FIELDS | {"exchange"})
    if row.get("effective_at") and row.get("retrieved_at"):
        if str(row["effective_at"]) > str(row["retrieved_at"]):
            errors.append("effective_after_retrieved")
    return errors


def validate_classification_row(row: dict[str, Any]) -> list[str]:
    return validate_snapshot_row(row) + _missing(row, {"taxonomy", "level", "code"})


def validate_index_constituent_row(row: dict[str, Any]) -> list[str]:
    return validate_snapshot_row(row) + _missing(row, {"index_code"})


def validate_consensus_row(row: dict[str, Any]) -> list[str]:
    errors = _missing(row, PROVENANCE_FIELDS | {"record_type"})
    record_type = row.get("record_type")
    if record_type not in {"estimate", "target"}:
        errors.append("invalid_record_type")
    if record_type == "estimate" and not row.get("period"):
        errors.append("missing:period")
    for name in ("eps_analysts", "revenue_analysts"):
        value = row.get(name)
        try:
            if value is not None and float(value) < 0:
                errors.append(f"negative:{name}")
        except (TypeError, ValueError):
            errors.append(f"invalid:{name}")
    low, high = row.get("eps_low"), row.get("eps_high")
    try:
        if low is not None and high is not None and float(low) > float(high):
            errors.append("eps_low_above_high")
    except (TypeError, ValueError):
        errors.append("invalid:eps_range")
    return errors
