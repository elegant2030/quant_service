"""Tests for the Quant Workbench curve_to_returns helper."""

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from curve_to_returns import align, main, read_levels, to_returns  # noqa: E402


def write(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_levels_become_simple_returns(tmp_path):
    path = write(
        tmp_path / "curve.csv",
        ["timestamp", "equity", "cash"],
        [["2026-01-02T00:00:00", "100", "0"], ["2026-01-05T00:00:00", "110", "0"]],
    )
    levels = read_levels(path)
    assert levels == {"2026-01-02": 100.0, "2026-01-05": 110.0}
    assert to_returns(levels)["2026-01-05"] == pytest.approx(0.10)


def test_alignment_uses_the_common_calendar(tmp_path):
    strategy = {"2026-01-02": 100.0, "2026-01-05": 110.0, "2026-01-06": 121.0}
    # The baseline has no 01-05 session: its 01-06 return must span 01-02 -> 01-06 for
    # BOTH series, otherwise a two-day baseline move is compared with a one-day move.
    baseline = {"2026-01-02": 50.0, "2026-01-06": 55.0}
    rows = align({"strategy_return": strategy, "spy_return": baseline})
    assert [row["date"] for row in rows] == ["2026-01-06"]
    assert float(rows[0]["strategy_return"]) == pytest.approx(0.21)
    assert float(rows[0]["spy_return"]) == pytest.approx(0.10)


def test_cli_writes_analyzer_ready_csv(tmp_path):
    strategy = write(
        tmp_path / "s.csv", ["timestamp", "equity"], [["2026-01-02", 100], ["2026-01-05", 101]]
    )
    baseline = write(
        tmp_path / "b.csv", ["date", "close", "x"], [["2026-01-02", 10, 1], ["2026-01-05", 10.2, 1]]
    )
    output = tmp_path / "out" / "returns.csv"
    code = main(
        [
            "--strategy",
            str(strategy),
            "--baseline",
            f"spy={baseline}:close",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    with output.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["date", "strategy_return", "spy_return"]
    assert float(rows[0]["spy_return"]) == pytest.approx(0.02)


def test_bad_inputs_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="non-positive"):
        read_levels(
            write(tmp_path / "z.csv", ["date", "equity"], [["2026-01-02", 0], ["2026-01-05", 1]])
        )
    with pytest.raises(ValueError, match="duplicate"):
        read_levels(
            write(tmp_path / "d.csv", ["date", "equity"], [["2026-01-02", 1], ["2026-01-02", 2]])
        )
    with pytest.raises(ValueError, match="fewer than two"):
        align({"a": {"2026-01-02": 1.0}, "b": {"2026-01-05": 1.0}})
