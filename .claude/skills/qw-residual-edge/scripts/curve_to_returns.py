#!/usr/bin/env python3
"""Align Quant Workbench equity curves into the return CSV the analyzer expects.

Quant Workbench addition (not part of the upstream skill). Standard library only.

Every input is a CSV with a date/timestamp column and a level column (equity, close,
value, ...). Levels are converted to simple period returns and inner-joined on date, so
the output only contains sessions present in every series.

Example:
    python3 curve_to_returns.py \
        --strategy <curves>/us_momentum_120_monthly.csv \
        --baseline equal_weight=<curves>/us_equal_weight_monthly.csv \
        --output /tmp/us_momentum_returns.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

DATE_COLUMNS = ("date", "timestamp", "session_date", "datetime")
LEVEL_COLUMNS = ("equity", "close_adj", "adj_close", "close", "value", "nav", "price")


def read_levels(path: Path, level_column: str | None = None) -> dict[str, float]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = [name.strip() for name in (reader.fieldnames or [])]
        date_column = next((name for name in DATE_COLUMNS if name in fields), None)
        if date_column is None:
            raise ValueError(f"{path}: no date column among {DATE_COLUMNS}")
        column = level_column or next((name for name in LEVEL_COLUMNS if name in fields), None)
        if column is None or column not in fields:
            raise ValueError(f"{path}: no level column among {LEVEL_COLUMNS}")
        levels: dict[str, float] = {}
        for row in reader:
            day = str(row[date_column]).strip()[:10]
            value = float(row[column])
            if value <= 0:
                raise ValueError(f"{path}: non-positive level on {day}")
            if day in levels:
                raise ValueError(f"{path}: duplicate date {day}")
            levels[day] = value
    if len(levels) < 2:
        raise ValueError(f"{path}: need at least two observations")
    return levels


def to_returns(levels: dict[str, float]) -> dict[str, float]:
    days = sorted(levels)
    return {
        current: levels[current] / levels[previous] - 1.0
        for previous, current in zip(days, days[1:], strict=False)
    }


def align(series: dict[str, dict[str, float]]) -> list[dict[str, str]]:
    """Inner-join on the dates shared by every series, after aligning LEVELS.

    Returns are computed on the common calendar so a session missing in one series does
    not silently turn a two-day move of another series into a one-day return.
    """
    common = set.intersection(*(set(levels) for levels in series.values()))
    if len(common) < 2:
        raise ValueError("series share fewer than two dates")
    days = sorted(common)
    rows: list[dict[str, str]] = []
    for previous, current in zip(days, days[1:], strict=False):
        row = {"date": current}
        for name, levels in series.items():
            row[name] = repr(levels[current] / levels[previous] - 1.0)
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--strategy", required=True, type=Path)
    parser.add_argument("--strategy-column", default=None)
    parser.add_argument(
        "--baseline",
        action="append",
        default=[],
        metavar="NAME=PATH[:COLUMN]",
        help="Repeatable. Output column is <NAME>_return.",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.baseline:
        parser.error("at least one --baseline is required")

    series = {"strategy_return": read_levels(args.strategy, args.strategy_column)}
    for item in args.baseline:
        name, _, location = item.partition("=")
        if not name or not location:
            parser.error(f"invalid --baseline value: {item}")
        path_text, _, column = location.partition(":")
        key = f"{name}_return"
        if key in series:
            parser.error(f"duplicate baseline name: {name}")
        series[key] = read_levels(Path(path_text), column or None)

    rows = align(series)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", *series])
        writer.writeheader()
        writer.writerows(rows)
    dropped = {name: len(levels) - len(rows) - 1 for name, levels in series.items()}
    print(f"wrote {len(rows)} aligned returns to {args.output}; unmatched sessions: {dropped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
