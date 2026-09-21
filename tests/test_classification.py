from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quant_workbench.core.classification import (
    ClassificationMembership,
    ClassificationStore,
    classification_store_from_universe,
)

UTC = timezone.utc


def membership(**overrides: object) -> ClassificationMembership:
    values: dict[str, object] = {
        "instrument_id": "NASDAQ:TEST",
        "taxonomy": "SECTOR",
        "level": 1,
        "code": "TECH",
        "name": "Technology",
        "valid_from": date(2025, 1, 1),
        "available_from": datetime(2025, 1, 5, tzinfo=UTC),
    }
    values.update(overrides)
    return ClassificationMembership(**values)  # type: ignore[arg-type]


class ClassificationStoreTests(unittest.TestCase):
    def test_lookup_respects_the_knowledge_clock(self) -> None:
        item = membership()
        store = ClassificationStore([item])
        query = {"taxonomy": "SECTOR", "level": 1}
        self.assertIsNone(
            store.resolve(
                "NASDAQ:TEST",
                session=date(2025, 1, 3),
                known_at=datetime(2025, 1, 3, tzinfo=UTC),
                **query,
            )
        )
        self.assertEqual(
            store.resolve(
                "NASDAQ:TEST",
                session=date(2025, 1, 6),
                known_at=datetime(2025, 1, 6, tzinfo=UTC),
                **query,
            ),
            item,
        )

    def test_reclassification_picks_latest_valid_and_members_follow(self) -> None:
        old = membership(valid_to=date(2025, 6, 30))
        new = membership(
            code="COMM",
            name="Communication",
            valid_from=date(2025, 7, 1),
            available_from=datetime(2025, 7, 1, tzinfo=UTC),
        )
        store = ClassificationStore([old, new])
        known = datetime(2025, 12, 31, tzinfo=UTC)
        june = store.resolve(
            "NASDAQ:TEST", taxonomy="SECTOR", level=1, session=date(2025, 6, 15), known_at=known
        )
        august = store.resolve(
            "NASDAQ:TEST", taxonomy="SECTOR", level=1, session=date(2025, 8, 15), known_at=known
        )
        assert june is not None and august is not None
        self.assertEqual((june.code, august.code), ("TECH", "COMM"))
        self.assertEqual(
            store.members(
                taxonomy="SECTOR", level=1, code="TECH", session=date(2025, 8, 15), known_at=known
            ),
            frozenset(),
        )
        self.assertEqual(
            store.members(
                taxonomy="SECTOR", level=1, code="COMM", session=date(2025, 8, 15), known_at=known
            ),
            frozenset({"NASDAQ:TEST"}),
        )

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            membership(available_from=datetime(2025, 1, 5))
        with self.assertRaises(ValueError):
            membership(valid_to=date(2024, 12, 31))
        with self.assertRaises(ValueError):
            membership(level=0)
        with self.assertRaises(ValueError):
            ClassificationStore([membership()]).resolve(
                "NASDAQ:TEST",
                taxonomy="SECTOR",
                level=1,
                session=date(2025, 2, 1),
                known_at=datetime(2025, 2, 1),
            )


class UniverseLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "universe_us.csv"
        self.path.write_text(
            "symbol,name,exchange,sector,market_cap\n"
            "AAA,Alpha,NASDAQ,Technology,1\n"
            "bbb,Beta,NYSE,Energy,2\n"
            "CCC,Gamma,NYSE,,3\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_snapshot_is_only_known_from_its_capture_time(self) -> None:
        captured = datetime(2026, 9, 13, 20, tzinfo=UTC)
        store = classification_store_from_universe(
            self.path, taxonomy="YF_SECTOR", available_from=captured
        )
        self.assertEqual(len(store.memberships), 2)
        self.assertFalse(store.has_backdated_memberships)
        before = store.resolve(
            "NASDAQ:AAA",
            taxonomy="YF_SECTOR",
            level=1,
            session=date(2026, 9, 12),
            known_at=datetime(2026, 9, 12, 23, tzinfo=UTC),
        )
        after = store.resolve(
            "NYSE:BBB",
            taxonomy="YF_SECTOR",
            level=1,
            session=date(2026, 9, 14),
            known_at=datetime(2026, 9, 14, 23, tzinfo=UTC),
        )
        self.assertIsNone(before)
        assert after is not None
        self.assertEqual(after.code, "Energy")

    def test_backdating_is_explicit_and_flagged(self) -> None:
        store = classification_store_from_universe(
            self.path,
            taxonomy="YF_SECTOR",
            available_from=datetime(2026, 9, 13, 20, tzinfo=UTC),
            backdate_to=date(2023, 8, 15),
        )
        self.assertTrue(store.has_backdated_memberships)
        found = store.resolve(
            "NASDAQ:AAA",
            taxonomy="YF_SECTOR",
            level=1,
            session=date(2024, 1, 2),
            known_at=datetime(2024, 1, 2, 23, tzinfo=UTC),
        )
        assert found is not None
        self.assertTrue(found.is_backdated)
        self.assertEqual(found.source, "universe_csv:backdated")


if __name__ == "__main__":
    unittest.main()
