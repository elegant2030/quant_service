from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quant_workbench.data.validation import validate_bar_row
from quant_workbench.ops.backup import create_backup_snapshot, verify_backup_snapshot
from quant_workbench.ops.calendar import latest_completed_session
from quant_workbench.ops.health import build_health_report
from quant_workbench.store import DatasetStore, StateStore


def valid_bar() -> dict[str, object]:
    return {
        "source": "test",
        "source_symbol": "AAA",
        "symbol": "AAA",
        "market": "us",
        "session_date": "2026-09-11",
        "effective_at": "2026-09-11",
        "retrieved_at": "2026-09-13T00:00:00+00:00",
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1000.0,
        "adjustment": "raw",
        "schema_version": 1,
    }


class DatasetStoreSchemaTests(unittest.TestCase):
    def test_columns_that_first_appear_in_later_rows_are_kept(self) -> None:
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(Path(directory))
            result = store.write_rows(
                "mixed",
                "us",
                "test",
                date(2026, 9, 21),
                [{"symbol": "A", "eps": 1.0}, {"symbol": "B", "target": 9.5}],
                "mixed-rows",
            )
            table = pq.read_table(result.path).to_pylist()
        self.assertEqual(
            table,
            [
                {"symbol": "A", "eps": 1.0, "target": None},
                {"symbol": "B", "eps": None, "target": 9.5},
            ],
        )


class StateStoreTests(unittest.TestCase):
    def test_successful_job_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(Path(directory) / "control.db") as state:
                first = state.acquire_job("daily", "2026-09-13")
                self.assertTrue(first.acquired)
                self.assertIsNotNone(first.run_id)
                state.complete_job(first.run_id or 0, 10)

                second = state.acquire_job("daily", "2026-09-13")
                self.assertFalse(second.acquired)
                self.assertEqual(second.reason, "already_succeeded")

    def test_watermark_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(Path(directory) / "control.db") as state:
                state.set_watermark("source", "bars", "us", "2026-09-11")
                self.assertEqual(state.get_watermark("source", "bars", "us"), "2026-09-11")

    def test_market_calendar_uses_last_completed_session_on_weekend(self) -> None:
        sunday = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
        self.assertEqual(latest_completed_session("us", sunday), date(2026, 9, 11))
        self.assertEqual(latest_completed_session("cn", sunday), date(2026, 9, 11))

    def test_three_source_failures_open_circuit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(Path(directory) / "control.db") as state:
                for _ in range(3):
                    state.record_source_failure("test-source", "network unavailable")
                self.assertTrue(state.circuit_is_open("test-source"))


class DatasetStoreTests(unittest.TestCase):
    def test_parquet_is_atomic_and_invalid_rows_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(directory)
            invalid = valid_bar()
            invalid["low"] = 110.0
            result = store.write_rows(
                "daily_bars",
                "us",
                "test",
                date(2026, 9, 13),
                [valid_bar(), invalid],
                "test-run",
                validator=validate_bar_row,
            )
            self.assertEqual(result.row_count, 1)
            self.assertTrue(result.path.exists())
            self.assertTrue(result.manifest_path.exists())
            self.assertEqual(store.inventory()["quarantine_files"], 1)

    def test_backup_snapshot_is_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(directory)
            with StateStore(store.root / "state" / "control.db") as state:
                store.write_rows(
                    "daily_bars",
                    "us",
                    "test",
                    date(2026, 9, 13),
                    [valid_bar()],
                    "backup-test",
                    validator=validate_bar_row,
                )
                snapshot_path, _ = create_backup_snapshot(store, state, date(2026, 9, 13))
            report = verify_backup_snapshot(store, snapshot_path)
            self.assertEqual(report["status"], "ok")

    def test_watchdog_detects_empty_stale_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(directory)
            with StateStore(store.root / "state" / "control.db") as state:
                report = build_health_report(
                    store,
                    state,
                    now=datetime(2026, 9, 13, 12, tzinfo=timezone.utc),
                )
            self.assertEqual(report["status"], "error")
            self.assertTrue(any("daily bars stale" in item for item in report["errors"]))


if __name__ == "__main__":
    unittest.main()
