from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from quant_workbench.backtest.engine import BacktestEngine
from quant_workbench.backtest.rules import CostModel
from quant_workbench.core.classification import ClassificationMembership, ClassificationStore
from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument
from quant_workbench.strategy.base import adjusted_return
from quant_workbench.strategy.momentum import CrossSectionalMomentum
from quant_workbench.strategy.sector_momentum import SectorMomentum

UTC = timezone.utc
START = datetime(2025, 1, 1)


def instrument(symbol: str) -> Instrument:
    return Instrument(symbol, Exchange.NASDAQ, AssetClass.EQUITY, Currency.USD)


def series(symbol: str, daily_growth: str, days: int = 12, start_price: str = "100") -> list[Bar]:
    bars = []
    price = Decimal(start_price)
    for offset in range(days):
        bars.append(
            Bar(instrument(symbol), START + timedelta(days=offset), price, price, price, price)
        )
        price = (price * (1 + Decimal(daily_growth))).quantize(Decimal("0.0001"))
    return bars


def sector(symbol: str, code: str, available: datetime) -> ClassificationMembership:
    return ClassificationMembership(
        instrument_id=f"NASDAQ:{symbol}",
        taxonomy="SECTOR",
        level=1,
        code=code,
        name=code,
        valid_from=date(2020, 1, 1),
        available_from=available,
    )


class AdjustedReturnTests(unittest.TestCase):
    def test_split_inside_window_does_not_look_like_a_crash(self) -> None:
        inst = instrument("SPLT")
        bars = [
            Bar(inst, START, Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100")),
            Bar(
                inst,
                START + timedelta(days=1),
                Decimal("11"),
                Decimal("11"),
                Decimal("11"),
                Decimal("11"),
                split_ratio=Decimal("10"),
                adj_factor=Decimal("10"),
            ),
        ]
        self.assertEqual(adjusted_return(bars, 1), Decimal("0.1"))
        self.assertIsNone(adjusted_return(bars, 2))
        # The plain cross-sectional strategy now ranks on the adjusted return too.
        targets = CrossSectionalMomentum(lookback=1, top_n=1).targets({"NASDAQ:SPLT": bars})
        self.assertEqual(targets[0].weight, Decimal("1"))


class SectorMomentumTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = (
            series("TEC1", "0.02")
            + series("TEC2", "0.01")
            + series("ENE1", "0.005")
            + series("UTL1", "-0.01")
            + series("NOCL", "0.05")  # strongest, but never classified
        )
        self.strategy = SectorMomentum(
            taxonomy="SECTOR", lookback=3, top_sectors=1, stocks_per_sector=1
        )
        self.costs = CostModel(commission_rate=Decimal("0"), slippage_bps=Decimal("0"))

    def run_engine(self, store: ClassificationStore):  # type: ignore[no-untyped-def]
        return BacktestEngine(
            initial_cash=Decimal("100000"),
            rebalance_every=1,
            cost_model=self.costs,
            classifications=store,
        ).run(self.bars, self.strategy)

    def test_selects_top_stock_of_top_sector_and_ignores_unclassified(self) -> None:
        known = datetime(2024, 1, 1, tzinfo=UTC)
        store = ClassificationStore(
            [
                sector("TEC1", "TECH", known),
                sector("TEC2", "TECH", known),
                sector("ENE1", "ENERGY", known),
                sector("UTL1", "UTIL", known),
            ]
        )
        result = self.run_engine(store)
        traded = {fill.instrument.symbol for fill in result.fills}
        self.assertEqual(traded, {"TEC1"})
        # lookback=3 needs 4 bars: first signal on day index 3, first fill next open.
        self.assertEqual(result.fills[0].timestamp, START + timedelta(days=4))
        self.assertEqual(result.fills[0].reason, "板块动量:TECH")

    def test_no_positions_before_classification_becomes_known(self) -> None:
        late = datetime(2025, 1, 8, 12, tzinfo=UTC)
        store = ClassificationStore([sector("TEC1", "TECH", late), sector("ENE1", "ENERGY", late)])
        result = self.run_engine(store)
        self.assertTrue(result.fills)
        # Known during 2025-01-08 -> usable for that day's close signal -> fill on 01-09.
        self.assertEqual(result.fills[0].timestamp, datetime(2025, 1, 9))

    def test_empty_store_never_trades(self) -> None:
        result = self.run_engine(ClassificationStore())
        self.assertEqual(result.fills, ())


if __name__ == "__main__":
    unittest.main()
