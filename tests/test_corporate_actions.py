from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from quant_workbench.backtest.engine import BacktestEngine
from quant_workbench.backtest.rules import CostModel
from quant_workbench.core.models import (
    AssetClass,
    Bar,
    Currency,
    Exchange,
    Instrument,
    TargetWeight,
)

START = datetime(2025, 1, 1)
INSTRUMENT = Instrument("TEST", Exchange.NASDAQ, AssetClass.EQUITY, Currency.USD)
NO_COSTS = CostModel(commission_rate=Decimal("0"), slippage_bps=Decimal("0"))


class BuyOnceAndHold:
    """Targets 100% once; later decisions keep the position untouched."""

    def __init__(self) -> None:
        self.done = False

    def targets(self, history):  # type: ignore[no-untyped-def]
        if self.done:
            return []
        self.done = True
        return [TargetWeight(INSTRUMENT, Decimal("1"), "test")]


def bar(offset: int, price: str, **actions: Decimal) -> Bar:
    value = Decimal(price)
    return Bar(INSTRUMENT, START + timedelta(days=offset), value, value, value, value, **actions)


def run(bars: list[Bar], **engine_options: object):  # type: ignore[no-untyped-def]
    engine = BacktestEngine(
        initial_cash=Decimal("1000"), rebalance_every=1, cost_model=NO_COSTS, **engine_options
    )
    return engine.run(bars, BuyOnceAndHold())


class CorporateActionTests(unittest.TestCase):
    def test_split_keeps_equity_continuous(self) -> None:
        bars = [
            bar(0, "100"),
            bar(1, "100"),
            bar(2, "10", split_ratio=Decimal("10"), adj_factor=Decimal("10")),
            bar(3, "11"),
        ]
        result = run(bars)
        equity = [point.equity for point in result.equity_curve]
        self.assertEqual(
            equity, [Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("1100")]
        )
        self.assertEqual(len(result.corporate_actions), 1)
        self.assertIn("split x10", result.corporate_actions[0])
        ignored = run(bars, apply_corporate_actions=False)
        self.assertEqual(ignored.equity_curve[2].equity, Decimal("100"))

    def test_cash_dividend_is_credited_on_ex_date(self) -> None:
        bars = [
            bar(0, "100"),
            bar(1, "100"),
            bar(2, "98", dividend=Decimal("2"), adj_factor=Decimal("100") / Decimal("98")),
            bar(3, "98"),
        ]
        result = run(bars)
        point = result.equity_curve[2]
        self.assertEqual(point.cash, Decimal("20"))
        self.assertEqual(point.equity, Decimal("1000"))
        self.assertIn("dividend 2/share", result.corporate_actions[0])

    def test_factor_only_source_reinvests(self) -> None:
        # BaoStock style: only adj_factor is known (price drops 100 -> 98 on the ex-date).
        factor = Decimal("100") / Decimal("98")
        bars = [bar(0, "100"), bar(1, "100"), bar(2, "98", adj_factor=factor), bar(3, "98")]
        result = run(bars)
        self.assertAlmostEqual(float(result.equity_curve[2].equity), 1000.0, places=9)
        self.assertIn("reinvested adj_factor", result.corporate_actions[0])

    def test_actions_without_a_position_are_ignored(self) -> None:
        bars = [bar(0, "100", split_ratio=Decimal("2"), adj_factor=Decimal("2")), bar(1, "100")]
        result = run(bars)
        self.assertEqual(result.corporate_actions, ())


if __name__ == "__main__":
    unittest.main()
