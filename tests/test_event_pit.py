from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quant_workbench.events.pit import (
    CninfoAnnouncementProvider,
    YFinanceNewsProvider,
    ingest_events,
    validate_event_row,
)
from quant_workbench.store import DatasetStore, StateStore


class EventProviderTests(unittest.TestCase):
    def test_yfinance_normalizes_timestamp_url_and_direction(self) -> None:
        payload = [
            {
                "id": "news-1",
                "content": {
                    "id": "news-1",
                    "title": "Apple raises guidance after record revenue",
                    "summary": "Management raised guidance.",
                    "pubDate": "2026-09-14T12:00:00Z",
                    "provider": {"displayName": "Fixture Wire"},
                    "canonicalUrl": {"url": "https://example.com/apple"},
                    "finance": {"stockTickers": [{"symbol": "AAPL"}]},
                },
            }
        ]
        provider = YFinanceNewsProvider(lambda _symbol: payload)
        retrieved = datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc)
        rows, raw = provider.fetch(
            "AAPL", datetime(2026, 9, 1, tzinfo=timezone.utc), retrieved
        )
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["direction"], 0)
        self.assertEqual(rows[0]["effective_at"], "2026-09-14T12:00:00+00:00")
        self.assertEqual(validate_event_row(rows[0]), [])
        self.assertEqual(raw, payload)

    def test_cninfo_date_is_conservatively_effective_next_midnight(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "代码": "600900",
                    "简称": "长江电力",
                    "公告标题": "长江电力关于控股股东增持股份结果的公告",
                    "公告时间": "2026-09-10",
                    "公告链接": "https://www.cninfo.com.cn/new/disclosure/detail?announcementId=123",
                }
            ]
        )
        provider = CninfoAnnouncementProvider(lambda **_kwargs: frame)
        retrieved = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
        rows, _ = provider.fetch(
            "600900", datetime(2026, 9, 1, tzinfo=timezone.utc), retrieved
        )
        self.assertEqual(rows[0]["effective_at"], "2026-09-10T16:00:00+00:00")
        self.assertEqual(rows[0]["event_id"], "cninfo:123")
        self.assertGreater(rows[0]["direction"], 0)
        self.assertEqual(validate_event_row(rows[0]), [])

    def test_ingestion_is_idempotent(self) -> None:
        payload = [
            {
                "id": "news-2",
                "content": {
                    "title": "Company wins contract",
                    "summary": "",
                    "pubDate": "2026-09-14T01:00:00Z",
                    "provider": {"displayName": "Fixture Wire"},
                    "canonicalUrl": {"url": "https://example.com/contract"},
                    "finance": {"stockTickers": [{"symbol": "AAPL"}]},
                },
            }
        ]
        provider = YFinanceNewsProvider(lambda _symbol: payload)
        with tempfile.TemporaryDirectory() as directory:
            universe = Path(directory) / "universe_us.csv"
            universe.write_text("symbol,exchange,name,sector\nAAPL,NASDAQ,Apple,Technology\n")
            store = DatasetStore(Path(directory) / "lake")
            state = StateStore(store.root / "state" / "control.db")
            try:
                first = ingest_events(
                    store,
                    state,
                    "us",
                    universe,
                    datetime(2026, 9, 14, tzinfo=timezone.utc).date(),
                    provider=provider,
                )
                second = ingest_events(
                    store,
                    state,
                    "us",
                    universe,
                    datetime(2026, 9, 14, tzinfo=timezone.utc).date(),
                    provider=provider,
                )
            finally:
                state.close()
            self.assertEqual(first["rows"], 1)
            self.assertFalse(second["acquired"])
            self.assertEqual(second["reason"], "already_succeeded")


if __name__ == "__main__":
    unittest.main()
