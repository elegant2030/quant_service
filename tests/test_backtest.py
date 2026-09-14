import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from quant_workbench.backtest.engine import BacktestEngine
from quant_workbench.backtest.rules import CostModel
from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument, TargetWeight


class BuyAndHold:
    def targets(self, history):
        instrument = next(iter(history.values()))[-1].instrument
        return [TargetWeight(instrument, Decimal("0.5"), "test")]


class BacktestTests(unittest.TestCase):
    def test_signal_executes_on_next_bar_without_lookahead(self):
        instrument = Instrument("TEST", Exchange.NASDAQ, AssetClass.EQUITY, Currency.USD)
        start = datetime(2025, 1, 1)
        bars = []
        for offset, price in enumerate((10, 11, 12)):
            value = Decimal(price)
            bars.append(
                Bar(
                    instrument,
                    start + timedelta(days=offset),
                    value,
                    value,
                    value,
                    value,
                )
            )
        result = BacktestEngine(
            initial_cash=Decimal("1000"),
            rebalance_every=1,
            cost_model=CostModel(commission_rate=Decimal("0"), slippage_bps=Decimal("0")),
        ).run(bars, BuyAndHold())
        self.assertEqual(result.fills[0].timestamp, start + timedelta(days=1))
        self.assertEqual(result.fills[0].price, Decimal("11"))
        self.assertGreater(result.equity_curve[-1].equity, Decimal("1000"))


if __name__ == "__main__":
    unittest.main()

