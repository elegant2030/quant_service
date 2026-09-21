from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import duckdb

from quant_workbench.core.classification import (
    classification_store_from_snapshots,
    load_classification_store,
)
from quant_workbench.data.validation import validate_classification_row, validate_consensus_row
from quant_workbench.jobs.orchestrator import run_due_jobs
from quant_workbench.jobs.snapshots import (
    CONSENSUS_DATASET,
    INDUSTRY_DATASET,
    UNIVERSE_DATASET,
    consensus_rows,
    snapshot_consensus,
    snapshot_industry,
    snapshot_universe,
)
from quant_workbench.ops.health import build_health_report
from quant_workbench.store import DatasetStore, StateStore

UTC = timezone.utc


class FakeBaoStock:
    def __init__(self, industry: str = "J66货币金融服务") -> None:
        self.industry = industry
        self.closed = False

    def industry_classification(self):  # type: ignore[no-untyped-def]
        return [
            {
                "symbol": "600000",
                "exchange": "SSE",
                "name": "浦发银行",
                "code": self.industry,
                "source_updated_at": "2026-09-21",
            },
            {
                "symbol": "000002",
                "exchange": "SZSE",
                "name": "万科A",
                "code": "K70房地产业",
                "source_updated_at": "2026-09-21",
            },
        ]

    def index_constituents(self, index_code):  # type: ignore[no-untyped-def]
        return [
            {
                "symbol": "600000",
                "exchange": "SSE",
                "name": "浦发银行",
                "source_updated_at": "2026-09-21",
            }
        ]

    def close(self) -> None:
        self.closed = True


AAPL_PAYLOAD = {
    "earnings_estimate": [
        {
            "period": "0q",
            "avg": 1.98,
            "low": 1.93,
            "high": 2.07,
            "yearAgoEps": 1.85,
            "numberOfAnalysts": 27,
            "growth": 0.0689,
            "currency": "USD",
        },
        {
            "period": "+1y",
            "avg": 9.58,
            "low": 8.69,
            "high": 10.67,
            "yearAgoEps": 8.82,
            "numberOfAnalysts": 40,
            "growth": 0.0864,
            "currency": "USD",
        },
    ],
    "revenue_estimate": [
        {
            "period": "0q",
            "avg": 1.136e11,
            "low": 1.122e11,
            "high": 1.172e11,
            "numberOfAnalysts": 27,
            "yearAgoRevenue": 1.025e11,
            "growth": 0.1089,
        },
    ],
    "eps_trend": [
        {
            "period": "0q",
            "current": 1.98,
            "7daysAgo": 1.98,
            "30daysAgo": 1.97,
            "60daysAgo": 2.02,
            "90daysAgo": 2.01,
        }
    ],
    "eps_revisions": [
        {
            "period": "0q",
            "upLast7days": 1,
            "upLast30days": 7,
            "downLast30days": 14,
            "downLast7Days": 0,
        }
    ],
    "price_targets": {
        "current": 336.13,
        "high": 405.0,
        "low": 215.0,
        "mean": 328.2,
        "median": 340.0,
    },
    "recommendations": [
        {"period": "0m", "strongBuy": 6, "buy": 19, "hold": 13, "sell": 3, "strongSell": 3}
    ],
}


class FakeConsensusProvider:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()

    def consensus(self, symbol):  # type: ignore[no-untyped-def]
        if symbol in self.failing:
            raise RuntimeError("rate limited")
        if symbol == "FUND":
            return {
                "earnings_estimate": [],
                "revenue_estimate": [],
                "eps_trend": [],
                "eps_revisions": [],
                "price_targets": {},
                "recommendations": [],
            }
        return AAPL_PAYLOAD


class SnapshotJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.config = root / "config"
        self.config.mkdir()
        (self.config / "universe_cn.csv").write_text(
            "symbol,name,exchange,sector,market_cap\n600000,浦发银行,SSE,J66货币金融服务,\n",
            encoding="utf-8",
        )
        (self.config / "universe_us.csv").write_text(
            "symbol,name,exchange,sector,market_cap\nAAPL,Apple,NASDAQ,Technology,5e12\n"
            "MSFT,Microsoft,NASDAQ,Technology,4e12\nFUND,Some Fund,NYSE,Financial Services,1e9\n"
            "BAD1,Bad One,NYSE,Energy,1e9\nBAD2,Bad Two,NYSE,Energy,1e9\n",
            encoding="utf-8",
        )
        self.store = DatasetStore(root / "lake")
        self.state = StateStore(self.store.root / "state" / "control.db")

    def tearDown(self) -> None:
        self.state.close()
        self.directory.cleanup()

    def read(self, dataset: str, market: str):  # type: ignore[no-untyped-def]
        pattern = self.store.root / "canonical" / f"dataset={dataset}" / f"market={market}"
        return duckdb.sql(
            f"select * from read_parquet('{pattern}/**/*.parquet', union_by_name=true)"
        ).df()

    def test_universe_snapshot_is_monthly_and_captures_cn_indexes(self) -> None:
        path = self.config / "universe_cn.csv"
        lease, result, details = snapshot_universe(
            self.store, self.state, "cn", path, date(2026, 9, 21), provider=FakeBaoStock()
        )
        assert result is not None
        self.assertTrue(lease.acquired)
        self.assertEqual(details["indexes"], {"hs300": 1, "zz500": 1, "sz50": 1})
        frame = self.read(UNIVERSE_DATASET, "cn")
        self.assertEqual(list(frame["snapshot_month"]), ["2026-09"])
        self.assertEqual(list(frame["effective_at"]), list(frame["retrieved_at"]))
        self.assertEqual(
            sorted(self.read("index_constituents", "cn")["index_code"]), ["hs300", "sz50", "zz500"]
        )
        raw = list((self.store.root / "raw").rglob("*.json.gz"))
        self.assertEqual(len(raw), 2)
        again = snapshot_universe(
            self.store, self.state, "cn", path, date(2026, 9, 29), provider=FakeBaoStock()
        )
        self.assertFalse(again[0].acquired)
        october = snapshot_universe(
            self.store, self.state, "cn", path, date(2026, 10, 9), provider=FakeBaoStock()
        )
        self.assertTrue(october[0].acquired)

    def test_industry_snapshots_build_a_point_in_time_store(self) -> None:
        snapshot_industry(self.store, self.state, "cn", date(2026, 9, 21), provider=FakeBaoStock())
        snapshot_industry(
            self.store,
            self.state,
            "cn",
            date(2026, 10, 9),
            provider=FakeBaoStock("J67资本市场服务"),
        )
        frame = self.read(INDUSTRY_DATASET, "cn")
        self.assertEqual(len(frame), 4)
        self.assertEqual(set(frame["taxonomy"]), {"CSRC"})
        store = load_classification_store(self.store.root, "cn", "CSRC")
        bank = [m for m in store.memberships if m.instrument_id == "SSE:600000"]
        self.assertEqual(len(bank), 2)
        self.assertEqual(len([m for m in store.memberships if m.instrument_id == "SZSE:000002"]), 1)
        far_future = datetime.now(UTC) + timedelta(days=1)
        before_any_capture = store.resolve(
            "SSE:600000",
            taxonomy="CSRC",
            level=1,
            session=date(2026, 1, 5),
            known_at=datetime(2026, 1, 5, 23, tzinfo=UTC),
        )
        self.assertIsNone(before_any_capture)
        today = store.resolve(
            "SSE:600000",
            taxonomy="CSRC",
            level=1,
            session=datetime.now(UTC).date(),
            known_at=far_future,
        )
        assert today is not None
        self.assertEqual(today.code, "J67资本市场服务")
        self.assertTrue(today.source.endswith(":snapshot"))

    def test_snapshot_rows_merge_runs_and_close_memberships_on_change(self) -> None:
        def row(captured: str, code: str) -> dict:
            return {
                "exchange": "NASDAQ",
                "symbol": "meta",
                "taxonomy": "YF_SECTOR",
                "level": 1,
                "code": code,
                "name": code,
                "source": "yfinance",
                "retrieved_at": captured,
            }

        store = classification_store_from_snapshots(
            [
                row("2026-09-01T21:00:00+00:00", "Technology"),
                row("2026-10-01T21:00:00+00:00", "Technology"),
                row("2026-11-02T21:00:00+00:00", "Communication Services"),
            ]
        )
        first, second = sorted(store.memberships, key=lambda item: item.valid_from)
        self.assertEqual(
            (first.code, first.valid_from, first.valid_to),
            ("Technology", date(2026, 9, 1), date(2026, 11, 1)),
        )
        self.assertEqual((second.code, second.valid_to), ("Communication Services", None))
        self.assertEqual(first.instrument_id, "NASDAQ:META")

    def test_consensus_rows_flatten_periods_and_targets(self) -> None:
        rows = consensus_rows("AAPL", AAPL_PAYLOAD, "us", "2026-09-21T21:00:00+00:00")
        by_key = {(row["record_type"], row["period"]): row for row in rows}
        self.assertEqual(set(by_key), {("estimate", "0q"), ("estimate", "+1y"), ("target", "")})
        quarter = by_key[("estimate", "0q")]
        self.assertEqual((quarter["eps_avg"], quarter["eps_analysts"]), (1.98, 27.0))
        self.assertEqual(quarter["revenue_avg"], 1.136e11)
        self.assertEqual((quarter["eps_trend_90d"], quarter["eps_down_30d"]), (2.01, 14.0))
        self.assertEqual(by_key[("target", "")]["target_median"], 340.0)
        self.assertTrue(all(validate_consensus_row(row) == [] for row in rows))
        self.assertIn(
            "eps_low_above_high",
            validate_consensus_row({**quarter, "eps_low": 3.0, "eps_high": 2.0}),
        )
        self.assertIn("missing:taxonomy", validate_classification_row({**quarter, "exchange": "X"}))

    def test_consensus_snapshot_tolerates_uncovered_listings_but_not_outages(self) -> None:
        path = self.config / "universe_us.csv"
        with self.assertRaisesRegex(RuntimeError, "coverage 60.0%"):
            snapshot_consensus(
                self.store,
                self.state,
                "us",
                path,
                date(2026, 9, 18),
                provider=FakeConsensusProvider({"BAD1", "BAD2"}),
                pause_seconds=0,
            )
        self.assertIsNone(self.state.get_watermark("yfinance", CONSENSUS_DATASET, "us"))
        lease, result, details = snapshot_consensus(
            self.store,
            self.state,
            "us",
            path,
            date(2026, 9, 21),
            provider=FakeConsensusProvider({"BAD1"}),
            pause_seconds=0,
        )
        assert result is not None
        self.assertEqual((details["answered"], details["with_estimates"]), (4, 3))
        self.assertEqual(result.row_count, 9)
        frame = self.read(CONSENSUS_DATASET, "us")
        self.assertEqual(sorted(frame["symbol"].unique()), ["AAPL", "BAD2", "MSFT"])

    def test_health_reports_missing_and_stale_snapshots(self) -> None:
        now = datetime(2026, 9, 21, 12, tzinfo=UTC)
        report = build_health_report(self.store, self.state, now=now)
        self.assertIn("us consensus snapshot never captured", report["warnings"])
        self.assertIn("cn industry snapshot never captured", report["warnings"])
        self.state.set_watermark("baostock", INDUSTRY_DATASET, "cn", "2026-08-03T10:00:00+00:00")
        self.state.set_watermark("yfinance", CONSENSUS_DATASET, "us", "2026-09-10T22:00:00+00:00")
        report = build_health_report(self.store, self.state, now=now)
        self.assertIn("cn industry snapshot stale: 2026-08 < 2026-09", report["errors"])
        self.assertTrue(any(e.startswith("us consensus snapshot stale") for e in report["errors"]))
        early = build_health_report(
            self.store, self.state, now=datetime(2026, 9, 3, 12, tzinfo=UTC)
        )
        self.assertIn("cn industry snapshot stale: 2026-08 < 2026-09", early["warnings"])

    def test_orchestrator_runs_due_snapshots_once(self) -> None:
        calls: list[str] = []

        def fake(name):  # type: ignore[no-untyped-def]
            def runner(store, state, market, *args, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(f"{name}:{market}")
                lease = state.acquire_job(f"fake_{name}", f"{market}:{len(calls)}")
                source = {
                    "universe": "universe_file",
                    "industry": {"cn": "baostock", "us": "yfinance"}[market],
                    "consensus": "yfinance",
                }[name]
                dataset = {
                    "universe": UNIVERSE_DATASET,
                    "industry": INDUSTRY_DATASET,
                    "consensus": CONSENSUS_DATASET,
                }[name]
                state.set_watermark(source, dataset, market, "2026-09-21T22:00:00+00:00")
                state.complete_job(lease.run_id or 0, 1)
                return lease, None, {}

            return runner

        now = datetime(2026, 9, 21, 23, 30, tzinfo=UTC)
        module = "quant_workbench.jobs.orchestrator"
        with (
            mock.patch(f"{module}.ingest_incremental_bars", side_effect=RuntimeError("skip")),
            mock.patch(f"{module}.snapshot_options", side_effect=RuntimeError("skip")),
            mock.patch(f"{module}.snapshot_universe", fake("universe")),
            mock.patch(f"{module}.snapshot_industry", fake("industry")),
            mock.patch(f"{module}.snapshot_consensus", fake("consensus")),
        ):
            first = run_due_jobs(self.store, self.state, self.config, ["SPY"], now)
            second = run_due_jobs(self.store, self.state, self.config, ["SPY"], now)
            skipped = run_due_jobs(
                self.store, self.state, self.config, ["SPY"], now, snapshots=False
            )
        self.assertEqual(
            sorted(calls),
            ["consensus:us", "industry:cn", "industry:us", "universe:cn", "universe:us"],
        )
        self.assertEqual(second["results"]["snapshot_consensus_us"]["reason"], "already_current")
        self.assertEqual(second["results"]["snapshot_industry_cn"]["reason"], "already_current")
        self.assertIn("snapshot_universe_us", first["results"])
        self.assertNotIn("snapshot_universe_us", skipped["results"])


if __name__ == "__main__":
    unittest.main()
