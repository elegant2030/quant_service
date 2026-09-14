from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument
from quant_workbench.data.adjust import apply_adjustment, event_factor
from quant_workbench.data.validation import validate_bar_row
from quant_workbench.data.yfinance_provider import YFinanceProvider
from quant_workbench.jobs.ingestion import backfill_daily_bars, ingest_incremental_bars
from quant_workbench.store import DatasetStore, StateStore

NVDA = Instrument("NVDA", Exchange.NASDAQ, AssetClass.EQUITY, Currency.USD)


def raw_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
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
        "adj_factor": 1.0,
        "dividend": None,
        "split_ratio": None,
        "schema_version": 2,
    }
    row.update(overrides)
    return row


class EventFactorTests(unittest.TestCase):
    def test_split_and_dividend_factors(self) -> None:
        self.assertEqual(event_factor(None), Decimal("1"))
        self.assertEqual(event_factor(100, split_ratio=10), Decimal("10"))
        self.assertEqual(event_factor(100, split_ratio=Decimal("0.1")), Decimal("0.1"))
        factor = event_factor(Decimal("1521.5"), dividend=Decimal("30.88"))
        self.assertAlmostEqual(float(factor), 1521.5 / 1490.62, places=9)
        combined = event_factor(100, dividend=1, split_ratio=2)
        self.assertAlmostEqual(float(combined), 2 * 100 / 99, places=12)

    def test_dividend_requires_previous_close(self) -> None:
        with self.assertRaises(ValueError):
            event_factor(None, dividend=1)
        with self.assertRaises(ValueError):
            event_factor(100, dividend=100)
        # yfinance reports 0.0 on sessions without a split; that means "no split".
        self.assertEqual(event_factor(100, split_ratio=0), Decimal("1"))


class ApplyAdjustmentTests(unittest.TestCase):
    def frame(self):  # type: ignore[no-untyped-def]
        import pandas as pd

        # Raw NVDA-like series: 10-for-1 split on day 3, $1 dividend on day 4.
        return pd.DataFrame(
            {
                "symbol": ["N"] * 4 + ["A"] * 2,
                "session_date": ["2024-06-07", "2024-06-10", "2024-06-11", "2024-06-12"]
                + ["2024-06-07", "2024-06-10"],
                "close": [1200.0, 121.0, 122.0, 125.0, 50.0, 51.0],
                "volume": [100.0, 1000.0, 1000.0, 1000.0, 10.0, 10.0],
                "adj_factor": [1.0, 10.0, 121.0 / 120.0, 1.0, 1.0, 1.0],
            }
        )

    def test_back_adjustment_is_continuous_and_forward_ends_at_raw(self) -> None:
        back = apply_adjustment(self.frame(), mode="back")
        n = back[back.symbol == "N"]
        self.assertEqual(list(n["close_adj"].round(4)), [1200.0, 1210.0, 1230.1667, 1260.4167])
        self.assertEqual(list(n["volume_adj"].round(4)), [100.0, 100.0, 99.1736, 99.1736])
        forward = apply_adjustment(self.frame(), mode="forward")
        n = forward[forward.symbol == "N"]
        self.assertAlmostEqual(float(n["close_adj"].iloc[-1]), 125.0)
        self.assertAlmostEqual(float(n["close_adj"].iloc[0]), 1200.0 / (10 * 121.0 / 120.0))
        a = forward[forward.symbol == "A"]
        self.assertEqual(list(a["close_adj"]), [50.0, 51.0])
        # Returns agree between conventions.
        back_ret = back[back.symbol == "N"]["close_adj"].pct_change().dropna()
        fwd_ret = n["close_adj"].pct_change().dropna()
        self.assertTrue(((back_ret - fwd_ret).abs() < 1e-12).all())

    def test_legacy_rows_without_factor_are_flagged(self) -> None:
        frame = self.frame().drop(columns=["adj_factor"])
        result = apply_adjustment(frame, mode="forward")
        self.assertFalse(result["adjustment_applied"].any())
        self.assertEqual(list(result["close_adj"]), list(frame["close"]))

    def test_input_order_is_preserved(self) -> None:
        shuffled = self.frame().iloc[[5, 3, 0, 4, 2, 1]].reset_index(drop=True)
        result = apply_adjustment(shuffled, mode="back")
        self.assertEqual(list(result["session_date"]), list(shuffled["session_date"]))
        row = result[(result.symbol == "N") & (result.session_date == "2024-06-12")]
        self.assertAlmostEqual(float(row["close_adj"].iloc[0]), 1260.4167, places=4)


class ValidationTests(unittest.TestCase):
    def test_schema2_rules(self) -> None:
        self.assertEqual(validate_bar_row(raw_row()), [])
        self.assertIn("missing:adj_factor", validate_bar_row(raw_row(adj_factor=None)))
        self.assertIn("non_positive_adj_factor", validate_bar_row(raw_row(adj_factor=0)))
        self.assertIn(
            "schema2_requires_raw_adjustment",
            validate_bar_row(raw_row(adjustment="provider_adjusted")),
        )
        self.assertIn("negative_dividend", validate_bar_row(raw_row(dividend=-1)))
        self.assertIn(
            "effective_after_retrieved",
            validate_bar_row(raw_row(effective_at="2026-09-14", retrieved_at="2026-09-13")),
        )
        legacy = raw_row(schema_version=1, adjustment="provider_adjusted", adj_factor=None)
        self.assertEqual(validate_bar_row(legacy), [])


class YFinanceFrameTests(unittest.TestCase):
    def test_raw_parsing_undoes_split_adjustment_and_records_actions(self) -> None:
        import pandas as pd

        # Mirrors yfinance output around the 2024-06-10 NVDA 10:1 split (auto_adjust=False):
        # pre-split closes are already divided by 10 by Yahoo.
        # Yahoo also divides pre-split dividend amounts by the split (0.001 here was
        # a $0.01 dividend paid on pre-split shares).
        frame = pd.DataFrame(
            {
                "Open": [121.0, 121.0, 120.0, 121.0, 120.5],
                "High": [123.0, 123.0, 122.0, 122.5, 126.0],
                "Low": [120.0, 120.0, 119.5, 120.0, 120.0],
                "Close": [121.0, 120.888, 121.79, 120.91, 125.2],
                "Volume": [1.0, 400_000_000.0, 300_000_000.0, 350_000_000.0, 320_000_000.0],
                "Dividends": [0.0, 0.001, 0.0, 0.01, 0.0],
                "Stock Splits": [0.0, 0.0, 10.0, 0.0, 0.0],
            },
            index=pd.to_datetime(
                ["2024-06-06", "2024-06-07", "2024-06-10", "2024-06-11", "2024-06-12"]
            ),
        )
        bars = YFinanceProvider._frame_to_bars(NVDA, frame, raw=True)
        self.assertEqual([bar.timestamp.date() for bar in bars][0], date(2024, 6, 6))
        day_before, first, split_day, dividend_day, last = bars
        self.assertEqual(day_before.close, Decimal("1210.0"))
        self.assertEqual(first.close, Decimal("1208.88"))
        self.assertEqual(first.volume, Decimal("40000000"))
        self.assertEqual(first.dividend, Decimal("0.01"))
        self.assertAlmostEqual(float(first.adj_factor), 1210.0 / (1210.0 - 0.01), places=12)
        self.assertIsNone(first.split_ratio)
        self.assertEqual(split_day.close, Decimal("121.79"))
        self.assertEqual(split_day.previous_close, Decimal("1208.88"))
        self.assertEqual(split_day.split_ratio, Decimal("10"))
        self.assertEqual(split_day.adj_factor, Decimal("10"))
        self.assertEqual(dividend_day.dividend, Decimal("0.01"))
        self.assertAlmostEqual(float(dividend_day.adj_factor), 121.79 / (121.79 - 0.01), places=12)
        self.assertEqual(last.adj_factor, Decimal("1"))
        # Back-adjusted continuity across the split.
        self.assertAlmostEqual(float(split_day.close * split_day.adj_factor), 1217.9)

    def test_dividend_on_first_row_leaves_factor_unset(self) -> None:
        import pandas as pd

        frame = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.0],
                "Close": [10.5],
                "Volume": [1.0],
                "Dividends": [0.5],
                "Stock Splits": [0.0],
            },
            index=pd.to_datetime(["2024-06-07"]),
        )
        (bar,) = YFinanceProvider._frame_to_bars(NVDA, frame, raw=True)
        self.assertIsNone(bar.adj_factor)
        self.assertEqual(bar.dividend, Decimal("0.5"))


class FakeBaoStockResult:
    def __init__(self, fields: list[str], rows: list[list[str]]) -> None:
        self.error_code = "0"
        self.error_msg = ""
        self.fields = fields
        self._rows = rows
        self._index = -1

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._index]


class FakeBaoStockModule:
    """Two dividends (cumulative 1.6 → 1.64 → 1.681) with the first ex-date suspended."""

    def __init__(self, drop_session_calls: int = 0) -> None:
        self.drop_session_calls = drop_session_calls
        self.logins = 0

    def login(self):  # type: ignore[no-untyped-def]
        self.logins += 1
        return FakeBaoStockResult([], [])

    def _maybe_dropped(self):  # type: ignore[no-untyped-def]
        if self.drop_session_calls > 0:
            self.drop_session_calls -= 1
            failure = FakeBaoStockResult([], [])
            failure.error_code = "10001001"
            failure.error_msg = "用户未登录"
            return failure
        return None

    def query_adjust_factor(self, code: str, start_date=None, end_date=None):  # type: ignore[no-untyped-def]
        dropped = self._maybe_dropped()
        if dropped is not None:
            return dropped
        fields = [
            "code",
            "dividOperateDate",
            "foreAdjustFactor",
            "backAdjustFactor",
            "adjustFactor",
        ]
        return FakeBaoStockResult(
            fields,
            [
                [code, "2022-07-01", "0.9", "1.600000", "1.600000"],
                [code, "2024-07-25", "0.95", "1.640000", "1.640000"],
                [code, "2025-07-14", "1.0", "1.681000", "1.681000"],
            ],
        )

    def query_history_k_data_plus(self, code, fields, start_date, end_date, frequency, adjustflag):  # type: ignore[no-untyped-def]
        dropped = self._maybe_dropped()
        if dropped is not None:
            return dropped
        names = fields.split(",")
        rows = [
            ["2024-07-24", "6.08", "6.10", "6.00", "6.08", "6.08", "0", "0", "0"],
            ["2024-07-25", "5.93", "5.93", "5.93", "5.93", "5.93", "0", "0", "0"],
            ["2024-07-30", "5.90", "6.00", "5.85", "5.95", "5.93", "1000", "1", "0"],
            ["2024-07-31", "5.95", "6.05", "5.90", "6.00", "5.95", "1000", "1", "0"],
            ["2025-07-14", "6.00", "6.10", "5.95", "6.05", "5.85", "1000", "1", "0"],
        ]
        return FakeBaoStockResult(names, rows)


class BaoStockFactorTests(unittest.TestCase):
    def test_factor_on_suspended_ex_date_carries_to_next_traded_bar(self) -> None:
        from quant_workbench.data.baostock_provider import BaoStockProvider

        provider = BaoStockProvider.__new__(BaoStockProvider)
        provider.bs = FakeBaoStockModule()
        instrument = Instrument("600027", Exchange.SSE, AssetClass.EQUITY, Currency.CNY)
        bars = provider.bars(
            instrument, datetime(2024, 7, 1), datetime(2025, 12, 31), adjusted=False
        )
        self.assertEqual(
            [bar.timestamp.date().isoformat() for bar in bars],
            ["2024-07-30", "2024-07-31", "2025-07-14"],
        )
        self.assertAlmostEqual(float(bars[0].adj_factor), 1.64 / 1.6, places=9)
        self.assertEqual(bars[1].adj_factor, Decimal("1"))
        self.assertAlmostEqual(float(bars[2].adj_factor), 1.681 / 1.64, places=9)
        # The 2022 event precedes the window and only serves as the ratio base.
        factors = provider.adjust_factors(instrument, date(2024, 7, 1), date(2025, 12, 31))
        self.assertEqual(sorted(factors), [date(2024, 7, 25), date(2025, 7, 14)])

    def test_dropped_session_triggers_relogin_and_retry(self) -> None:
        from quant_workbench.data.baostock_provider import BaoStockProvider

        provider = BaoStockProvider.__new__(BaoStockProvider)
        provider.bs = FakeBaoStockModule(drop_session_calls=1)
        provider.relogins = 0
        instrument = Instrument("600027", Exchange.SSE, AssetClass.EQUITY, Currency.CNY)
        bars = provider.bars(instrument, datetime(2024, 7, 1), datetime(2025, 12, 31))
        self.assertEqual(len(bars), 3)
        self.assertEqual(provider.relogins, 1)
        self.assertEqual(provider.bs.logins, 1)
        # A persistent failure still surfaces as an error after one re-login.
        provider.bs = FakeBaoStockModule(drop_session_calls=5)
        with self.assertRaises(RuntimeError):
            provider.bars(instrument, datetime(2024, 7, 1), datetime(2025, 12, 31))


class FakeUSProvider:
    """Deterministic provider: raw bars from 2023-08-10, 2:1 split on 2023-08-16."""

    def __init__(self, closes: dict[str, list[float]] | None = None) -> None:
        self.calls = 0
        self.closes = closes

    def bars_many(self, instruments, start, end, frequency="1d", adjusted=False):  # type: ignore[no-untyped-def]
        self.calls += 1
        results = {}
        sessions = [date(2023, 8, 14), date(2023, 8, 15), date(2023, 8, 16), date(2023, 8, 17)]
        for instrument in instruments:
            closes = (self.closes or {}).get(instrument.symbol, [200.0, 202.0, 101.5, 103.0])
            bars = []
            previous = None
            for session, close in zip(sessions, closes, strict=True):
                if not (start.date() <= session <= end.date()):
                    continue
                split = Decimal("2") if session == date(2023, 8, 16) else None
                bars.append(
                    Bar(
                        instrument=instrument,
                        timestamp=datetime.combine(session, datetime.min.time()),
                        open=Decimal(str(close)),
                        high=Decimal(str(close + 1)),
                        low=Decimal(str(close - 1)),
                        close=Decimal(str(close)),
                        volume=Decimal("100"),
                        previous_close=previous,
                        split_ratio=split,
                        adj_factor=split or Decimal("1"),
                    )
                )
                previous = Decimal(str(close))
            results[instrument.symbol] = bars
        return results, []


class BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.universe = root / "universe_us.csv"
        self.universe.write_text(
            "symbol,name,exchange,sector\nAAA,Alpha,NASDAQ,Technology\nBBB,Beta,NYSE,Energy\n",
            encoding="utf-8",
        )
        self.store = DatasetStore(root / "lake")
        self.state = StateStore(self.store.root / "state" / "control.db")

    def tearDown(self) -> None:
        self.state.close()
        self.directory.cleanup()

    def _seed_legacy_part(self) -> None:
        rows = [
            raw_row(
                symbol=symbol,
                source_symbol=symbol,
                source="yfinance",
                session_date=session,
                effective_at=session,
                close=close,
                open=close,
                high=close + 1,
                low=close - 1,
                schema_version=1,
                adjustment="provider_adjusted",
                adj_factor=None,
            )
            for symbol in ("AAA", "BBB")
            for session, close in (
                ("2023-08-15", 101.0),
                ("2023-08-16", 101.5),
                ("2023-08-17", 103.0),
            )
        ]
        self.store.write_rows(
            "daily_bars", "us", "yfinance", date(2026, 9, 1), rows, "us-yfinance-schema1"
        )
        self.state.set_watermark("yfinance", "daily_bars", "us", "2023-08-17")

    def test_backfill_replaces_legacy_part_and_explains_difference(self) -> None:
        self._seed_legacy_part()
        provider = FakeUSProvider()
        lease, result, summary = backfill_daily_bars(
            self.store,
            self.state,
            "us",
            self.universe,
            date(2023, 8, 15),
            date(2026, 9, 13),
            provider=provider,
        )
        self.assertTrue(lease.acquired)
        assert result is not None
        self.assertEqual(result.row_count, 6)
        self.assertEqual(summary["coverage"], 1.0)
        self.assertEqual(len(summary["archived_parts"]), 2)
        self.assertTrue((self.store.root / "archive").exists())
        remaining = list((self.store.root / "canonical" / "dataset=daily_bars").rglob("*.parquet"))
        self.assertEqual(remaining, [result.path])
        comparison = summary["comparison"]
        self.assertEqual(comparison["compared_rows"], 6)
        # Forward-adjusted raw closes reproduce the legacy adjusted closes exactly.
        self.assertLess(comparison["forward_adjusted_vs_old"]["max_abs_rel_diff"], 1e-9)
        # Raw closes differ from the legacy adjusted ones before the split.
        self.assertGreater(comparison["raw_vs_old"]["max_abs_rel_diff"], 0.9)
        # Watermark untouched.
        self.assertEqual(self.state.get_watermark("yfinance", "daily_bars", "us"), "2023-08-17")

        import duckdb

        frame = duckdb.sql(
            "SELECT symbol, session_date, close, adj_factor, split_ratio, schema_version, "
            f"adjustment FROM read_parquet('{result.path}') ORDER BY symbol, session_date"
        ).df()
        self.assertEqual(set(frame["schema_version"]), {2})
        self.assertEqual(set(frame["adjustment"]), {"raw"})
        self.assertEqual(list(frame[frame.symbol == "AAA"]["close"]), [202.0, 101.5, 103.0])
        self.assertEqual(list(frame[frame.symbol == "AAA"]["adj_factor"]), [1.0, 2.0, 1.0])

    def test_backfill_is_idempotent_and_rerun_yields_identical_raw_closes(self) -> None:
        self._seed_legacy_part()
        first = backfill_daily_bars(
            self.store,
            self.state,
            "us",
            self.universe,
            date(2023, 8, 15),
            date(2026, 9, 13),
            provider=FakeUSProvider(),
        )
        second = backfill_daily_bars(
            self.store,
            self.state,
            "us",
            self.universe,
            date(2023, 8, 15),
            date(2026, 9, 13),
            provider=FakeUSProvider(),
        )
        self.assertFalse(second[0].acquired)
        self.assertEqual(second[2]["reason"], "already_succeeded")
        forced = backfill_daily_bars(
            self.store,
            self.state,
            "us",
            self.universe,
            date(2023, 8, 15),
            date(2026, 9, 13),
            provider=FakeUSProvider(),
            force=True,
        )
        self.assertTrue(forced[0].acquired)
        assert first[1] is not None and forced[1] is not None
        import duckdb

        archived = (
            self.store.root / "archive" / "2026-09-13" / first[1].path.relative_to(self.store.root)
        )
        self.assertTrue(archived.exists())
        before = duckdb.sql(
            f"SELECT symbol, session_date, close FROM read_parquet('{archived}') ORDER BY 1,2"
        ).fetchall()
        after = duckdb.sql(
            f"SELECT symbol, session_date, close FROM read_parquet('{forced[1].path}') ORDER BY 1,2"
        ).fetchall()
        self.assertEqual(before, after)

    def test_low_coverage_fails_and_leaves_canonical_untouched(self) -> None:
        self._seed_legacy_part()

        class HalfProvider(FakeUSProvider):
            def bars_many(self, instruments, start, end, frequency="1d", adjusted=False):  # type: ignore[no-untyped-def]
                results, _ = super().bars_many(instruments[:1], start, end)
                return results, ["BBB: RuntimeError: rate limited"]

        with self.assertRaises(RuntimeError):
            backfill_daily_bars(
                self.store,
                self.state,
                "us",
                self.universe,
                date(2023, 8, 15),
                date(2026, 9, 13),
                provider=HalfProvider(),
            )
        parts = list((self.store.root / "canonical" / "dataset=daily_bars").rglob("*.parquet"))
        self.assertEqual(len(parts), 1)
        self.assertFalse((self.store.root / "archive").exists())

    def test_incremental_ingest_writes_schema2_rows(self) -> None:
        from unittest import mock

        self._seed_legacy_part()
        self.state.set_watermark("yfinance", "daily_bars", "us", "2023-08-15")
        with mock.patch("quant_workbench.jobs.ingestion.YFinanceProvider", FakeUSProvider):
            lease, result, summary = ingest_incremental_bars(
                self.store, self.state, "us", self.universe, date(2023, 8, 17)
            )
        self.assertTrue(lease.acquired)
        assert result is not None
        self.assertEqual(summary["target_session"], "2023-08-17")
        import duckdb

        frame = duckdb.sql(
            f"SELECT symbol, session_date, close, adj_factor, previous_close, schema_version "
            f"FROM read_parquet('{result.path}') ORDER BY symbol, session_date"
        ).df()
        self.assertEqual(set(frame["schema_version"]), {2})
        self.assertEqual(list(frame["session_date"].unique()), ["2023-08-16", "2023-08-17"])
        aaa = frame[frame.symbol == "AAA"]
        self.assertEqual(list(aaa["adj_factor"]), [2.0, 1.0])
        # Warm-up window supplied previous_close for the first incremental session.
        self.assertEqual(float(aaa["previous_close"].iloc[0]), 202.0)


if __name__ == "__main__":
    unittest.main()
