import unittest
from decimal import Decimal

from quant_workbench.core.models import Exchange
from quant_workbench.data.universe import UniverseMember, round_robin_sample, sector_counts


class UniverseTests(unittest.TestCase):
    def test_round_robin_preserves_sector_balance(self):
        candidates = {
            "A": [UniverseMember(f"A{i}", f"A{i}", Exchange.NYSE, "A", Decimal(i)) for i in range(5)],
            "B": [UniverseMember(f"B{i}", f"B{i}", Exchange.NASDAQ, "B", Decimal(i)) for i in range(5)],
            "C": [UniverseMember(f"C{i}", f"C{i}", Exchange.AMEX, "C", Decimal(i)) for i in range(5)],
        }
        selected = round_robin_sample(candidates, 8)
        self.assertEqual(len(selected), 8)
        counts = sector_counts(selected)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)


if __name__ == "__main__":
    unittest.main()
