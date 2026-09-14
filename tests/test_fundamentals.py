import unittest
from datetime import date
from decimal import Decimal

from quant_workbench.core.models import FinancialSnapshot
from quant_workbench.fundamentals.scoring import score_snapshot


class FundamentalTests(unittest.TestCase):
    def test_score_is_bounded_and_flags_cash_flow_mismatch(self):
        snapshot = FinancialSnapshot(
            symbol="TEST",
            as_of=date(2025, 12, 31),
            net_income=Decimal("10"),
            operating_cash_flow=Decimal("-2"),
            free_cash_flow=Decimal("-3"),
            market_cap=Decimal("100"),
            operating_margin=Decimal("0.2"),
            revenue_growth=Decimal("0.1"),
            total_debt=Decimal("20"),
            total_equity=Decimal("80"),
            cash=Decimal("10"),
        )
        score = score_snapshot(snapshot)
        self.assertGreaterEqual(score.composite, 0)
        self.assertLessEqual(score.composite, 100)
        self.assertIn("盈利为正但经营现金流为负", score.flags)


if __name__ == "__main__":
    unittest.main()

