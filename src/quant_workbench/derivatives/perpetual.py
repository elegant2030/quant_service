from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PerpetualPosition:
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    leverage: Decimal
    accumulated_funding: Decimal = Decimal("0")

    @property
    def notional(self) -> Decimal:
        return abs(self.quantity * self.mark_price)

    @property
    def initial_margin(self) -> Decimal:
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")
        return abs(self.quantity * self.entry_price) / self.leverage

    @property
    def unrealized_pnl(self) -> Decimal:
        return self.quantity * (self.mark_price - self.entry_price) - self.accumulated_funding


def funding_payment(position_quantity: Decimal, mark_price: Decimal, funding_rate: Decimal) -> Decimal:
    """Positive means the position pays funding; negative means it receives funding."""
    return position_quantity * mark_price * funding_rate


def approximate_liquidation_price(
    entry_price: Decimal,
    leverage: Decimal,
    maintenance_margin_rate: Decimal,
    is_long: bool,
) -> Decimal:
    """Exchange-agnostic estimate; real liquidation engines include tiering and fees."""
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    initial_margin_rate = Decimal("1") / leverage
    if is_long:
        return entry_price * (Decimal("1") - initial_margin_rate + maintenance_margin_rate)
    return entry_price * (Decimal("1") + initial_margin_rate - maintenance_margin_rate)

