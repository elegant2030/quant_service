from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from quant_workbench.reports.market_brief import (
    build_market_brief,
    due_report_stages,
    render_telegram,
    run_due_market_briefs,
)
from quant_workbench.store import DatasetStore


def synthetic_bars() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    dates = pd.bdate_range("2026-05-20", periods=82)
    for market in ("us", "cn"):
        for sector_index in range(6):
            sector = f"{market}-sector-{sector_index}"
            for stock_index in range(6):
                symbol = f"{market}{sector_index}{stock_index}"
                drift = 0.0008 + sector_index * 0.0007 + stock_index * 0.0001
                for day_index, day in enumerate(dates):
                    close = 50 * (1 + drift) ** day_index
                    rows.append(
                        {
                            "market": market,
                            "symbol": symbol,
                            "name": f"name-{symbol}",
                            "sector": sector,
                            "session_date": day.date().isoformat(),
                            "close": close,
                            "volume": 1_000_000 * (1 + stock_index / 20),
                        }
                    )
    return pd.DataFrame(rows)


class ScheduleTests(unittest.TestCase):
    def test_three_market_local_cutoffs(self) -> None:
        with patch("quant_workbench.reports.market_brief._is_market_session", return_value=True):
            before = datetime(2026, 9, 14, 12, 29, tzinfo=timezone.utc)  # 08:29 ET
            self.assertEqual(due_report_stages("us", before), [])
            self.assertEqual(
                due_report_stages(
                    "us", datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
                ),
                ["premarket"],
            )
            self.assertEqual(
                due_report_stages(
                    "us", datetime(2026, 9, 14, 20, 30, tzinfo=timezone.utc)
                ),
                ["premarket", "midday", "postmarket"],
            )
            self.assertEqual(
                due_report_stages(
                    "cn", datetime(2026, 9, 14, 3, 30, tzinfo=timezone.utc)
                ),
                ["premarket", "midday"],
            )

    def test_non_session_has_no_reports(self) -> None:
        with patch("quant_workbench.reports.market_brief._is_market_session", return_value=False):
            self.assertEqual(
                due_report_stages(
                    "us", datetime(2026, 9, 13, 22, tzinfo=timezone.utc)
                ),
                [],
            )


class ReportTests(unittest.TestCase):
    def test_report_has_exact_counts_and_writes_both_formats(self) -> None:
        frame = synthetic_bars()
        live = {
            "source": "fixture",
            "retrieved_at": "2026-09-14T16:30:00+00:00",
            "rows": [
                {
                    "symbol": symbol,
                    "price": float(group.sort_values("session_date")["close"].iloc[-1]) * 1.01,
                    "volume": 800_000,
                    "quoted_at": "2026-09-14T12:30:00-04:00",
                }
                for symbol, group in frame[frame["market"] == "us"].groupby("symbol")
            ],
            "errors": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(directory)
            with (
                patch("quant_workbench.reports.market_brief._load_daily_bars", return_value=frame),
                patch("quant_workbench.reports.market_brief._option_pulse", return_value=[]),
            ):
                report, artifacts = build_market_brief(
                    store,
                    "us",
                    "midday",
                    datetime(2026, 9, 14, 16, 30, tzinfo=timezone.utc),
                    live_fetcher=lambda _: live,
                    send=False,
                )
            self.assertEqual(len(report["sectors"]), 5)
            self.assertEqual(len(report["stocks"]), 15)
            self.assertTrue(artifacts.json_path.is_file())
            self.assertTrue(artifacts.markdown_path.is_file())
            self.assertIn("美股实时快照(36)", report["data_mode"])
            self.assertIn("候选股票 TOP15", render_telegram(report))
            self.assertIn("技术面", report["stocks"][0]["reason"]["basis"])
            self.assertIn("未纳入", report["stocks"][0]["reason"]["fundamental"])
            payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["market"], "us")
            self.assertEqual(payload["stage"], "midday")

    def test_due_runner_is_idempotent(self) -> None:
        frame = synthetic_bars()
        now = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(directory)
            with (
                patch(
                    "quant_workbench.reports.market_brief._is_market_session",
                    side_effect=lambda market, _day: market == "cn",
                ),
                patch("quant_workbench.reports.market_brief._load_daily_bars", return_value=frame),
                patch("quant_workbench.reports.market_brief._option_pulse", return_value=[]),
            ):
                first = run_due_market_briefs(store, now, fetch_live=False, send=False)
                second = run_due_market_briefs(store, now, fetch_live=False, send=False)
            self.assertEqual(
                [(item["market"], item["stage"]) for item in first["generated"]],
                [("cn", "premarket")],
            )
            self.assertEqual(second["generated"], [])
            self.assertEqual(second["skipped"][0]["reason"], "already_generated")


if __name__ == "__main__":
    unittest.main()
