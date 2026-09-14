import unittest
from datetime import date, datetime
from decimal import Decimal

from quant_workbench.backtest.rules import TradingRules
from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument, Side


class TradingRuleTests(unittest.TestCase):
    def setUp(self):
        self.instrument = Instrument(
            "600000", Exchange.SSE, AssetClass.EQUITY, Currency.CNY, lot_size=Decimal("100")
        )

    def test_a_share_lot_and_t_plus_one(self):
        rules = TradingRules()
        self.assertEqual(rules.normalize_quantity(self.instrument, Decimal("255")), Decimal("200"))
        self.assertFalse(rules.can_sell(self.instrument, date(2025, 1, 2), date(2025, 1, 2)))
        self.assertTrue(rules.can_sell(self.instrument, date(2025, 1, 2), date(2025, 1, 3)))

    def test_locked_limit_up(self):
        price = Decimal("11")
        bar = Bar(
            self.instrument,
            datetime(2025, 1, 2),
            price,
            price,
            price,
            price,
            previous_close=Decimal("10"),
        )
        self.assertTrue(TradingRules().is_locked_limit(bar, Side.BUY))


if __name__ == "__main__":
    unittest.main()
