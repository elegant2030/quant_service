from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_DOWN

from quant_workbench.core.models import AssetClass, Bar, Exchange, Instrument, Side


A_SHARE_EXCHANGES = {Exchange.SSE, Exchange.SZSE, Exchange.BSE}


class TradingRules:
    def normalize_quantity(self, instrument: Instrument, quantity: Decimal) -> Decimal:
        lot = instrument.lot_size
        if lot <= 0:
            raise ValueError("lot size must be positive")
        lots = (abs(quantity) / lot).to_integral_value(rounding=ROUND_DOWN)
        return lots * lot * (Decimal("-1") if quantity < 0 else Decimal("1"))

    def can_sell(self, instrument: Instrument, acquired_on: date, sell_on: date) -> bool:
        if instrument.exchange in A_SHARE_EXCHANGES and instrument.asset_class == AssetClass.EQUITY:
            return sell_on > acquired_on
        return True

    def is_locked_limit(self, bar: Bar, side: Side) -> bool:
        if bar.instrument.exchange not in A_SHARE_EXCHANGES or bar.previous_close is None:
            return False
        board_limit = Decimal(str(bar.instrument.metadata.get("price_limit", "0.10")))
        upper = bar.previous_close * (1 + board_limit)
        lower = bar.previous_close * (1 - board_limit)
        tolerance = Decimal("0.0001")
        if side == Side.BUY:
            return abs(bar.low - upper) <= tolerance and abs(bar.high - upper) <= tolerance
        return abs(bar.low - lower) <= tolerance and abs(bar.high - lower) <= tolerance


class CostModel:
    def __init__(
        self,
        commission_rate: Decimal = Decimal("0.0003"),
        minimum_commission: Decimal = Decimal("0"),
        slippage_bps: Decimal = Decimal("2"),
    ):
        self.commission_rate = commission_rate
        self.minimum_commission = minimum_commission
        self.slippage_bps = slippage_bps

    def execution_price(self, price: Decimal, side: Side) -> Decimal:
        impact = self.slippage_bps / Decimal("10000")
        return price * (1 + impact if side == Side.BUY else 1 - impact)

    def commission(self, notional: Decimal) -> Decimal:
        if notional == 0:
            return Decimal("0")
        return max(self.minimum_commission, abs(notional) * self.commission_rate)

