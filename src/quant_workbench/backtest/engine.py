from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt
from statistics import fmean, pstdev
from typing import Sequence

from quant_workbench.backtest.rules import CostModel, TradingRules
from quant_workbench.core.models import Bar, EquityPoint, Fill, Side, TargetWeight
from quant_workbench.strategy.base import Strategy


@dataclass(slots=True)
class Position:
    quantity: Decimal = Decimal("0")
    average_cost: Decimal = Decimal("0")
    acquired_on: date | None = None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity_curve: tuple[EquityPoint, ...]
    fills: tuple[Fill, ...]
    rejected_orders: tuple[str, ...]
    metrics: dict[str, float]


class BacktestEngine:
    """Daily-bar engine; signals at close execute at the next available open."""

    def __init__(
        self,
        initial_cash: Decimal = Decimal("1000000"),
        cost_model: CostModel | None = None,
        trading_rules: TradingRules | None = None,
        rebalance_every: int = 5,
    ):
        self.initial_cash = initial_cash
        self.cost_model = cost_model or CostModel()
        self.trading_rules = trading_rules or TradingRules()
        self.rebalance_every = rebalance_every

    def run(self, bars: Sequence[Bar], strategy: Strategy) -> BacktestResult:
        by_time: dict[object, dict[str, Bar]] = defaultdict(dict)
        for bar in bars:
            by_time[bar.timestamp][bar.instrument.id] = bar
        timeline = sorted(by_time)
        history: dict[str, list[Bar]] = defaultdict(list)
        positions: dict[str, Position] = defaultdict(Position)
        cash = self.initial_cash
        fills: list[Fill] = []
        rejected: list[str] = []
        curve: list[EquityPoint] = []
        pending: list[TargetWeight] = []
        last_prices: dict[str, Decimal] = {}
        instruments = {bar.instrument.id: bar.instrument for bar in bars}

        for index, timestamp in enumerate(timeline):
            current = by_time[timestamp]
            prices = {**last_prices, **{key: bar.open for key, bar in current.items()}}
            equity_at_open = cash + sum(
                position.quantity
                * prices.get(key, position.average_cost)
                * instruments[key].multiplier
                for key, position in positions.items()
            )

            executable: list[tuple[TargetWeight, Bar, Decimal]] = []
            for target in pending:
                key = target.instrument.id
                bar = current.get(key)
                if bar is None:
                    continue
                position = positions[key]
                desired = self.trading_rules.normalize_quantity(
                    target.instrument,
                    equity_at_open * target.weight / (bar.open * target.instrument.multiplier),
                )
                if desired < 0:
                    rejected.append(f"{timestamp.isoformat()} {key}: shorting is not enabled")
                    continue
                delta = desired - position.quantity
                if delta == 0:
                    continue
                executable.append((target, bar, delta))

            # Raise cash before opening/increasing positions. This also makes a rebalance
            # independent of the strategy's symbol ordering.
            executable.sort(key=lambda item: item[2] > 0)
            for target, bar, delta in executable:
                key = target.instrument.id
                position = positions[key]
                side = Side.BUY if delta > 0 else Side.SELL
                if self.trading_rules.is_locked_limit(bar, side):
                    rejected.append(f"{timestamp.isoformat()} {key}: locked price limit")
                    continue
                if side == Side.SELL and position.acquired_on is not None:
                    if not self.trading_rules.can_sell(target.instrument, position.acquired_on, timestamp.date()):
                        rejected.append(f"{timestamp.isoformat()} {key}: T+1 restriction")
                        continue
                execution_price = self.cost_model.execution_price(bar.open, side)
                quantity = abs(delta)
                notional = quantity * execution_price * target.instrument.multiplier
                commission = self.cost_model.commission(notional)
                if side == Side.BUY and notional + commission > cash:
                    affordable = self.trading_rules.normalize_quantity(
                        target.instrument,
                        cash / (execution_price * target.instrument.multiplier),
                    )
                    quantity = min(quantity, affordable)
                    notional = quantity * execution_price * target.instrument.multiplier
                    commission = self.cost_model.commission(notional)
                if quantity <= 0:
                    rejected.append(f"{timestamp.isoformat()} {key}: insufficient cash")
                    continue
                if side == Side.BUY:
                    old_cost = position.quantity * position.average_cost
                    position.quantity += quantity
                    position.average_cost = (old_cost + quantity * execution_price) / position.quantity
                    position.acquired_on = timestamp.date()
                    cash -= notional + commission
                else:
                    quantity = min(quantity, position.quantity)
                    notional = quantity * execution_price * target.instrument.multiplier
                    commission = self.cost_model.commission(notional)
                    position.quantity -= quantity
                    cash += notional - commission
                    if position.quantity == 0:
                        position.average_cost = Decimal("0")
                        position.acquired_on = None
                fills.append(
                    Fill(target.instrument, timestamp, side, quantity, execution_price, commission, target.reason)
                )
            pending = []

            for key, bar in current.items():
                history[key].append(bar)
                last_prices[key] = bar.close
            close_equity = cash + sum(
                position.quantity * last_prices[key] * instruments[key].multiplier
                for key, position in positions.items()
            )
            gross = sum(
                abs(position.quantity * last_prices[key] * instruments[key].multiplier)
                for key, position in positions.items()
            )
            curve.append(EquityPoint(timestamp, close_equity, cash, gross))
            if index % self.rebalance_every == 0:
                pending = list(strategy.targets(history))

        return BacktestResult(tuple(curve), tuple(fills), tuple(rejected), self._metrics(curve))

    @staticmethod
    def _metrics(curve: Sequence[EquityPoint]) -> dict[str, float]:
        if len(curve) < 2:
            return {"total_return": 0.0, "annualized_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
        values = [float(point.equity) for point in curve]
        returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1]]
        total_return = values[-1] / values[0] - 1
        annualized_return = (1 + total_return) ** (252 / max(len(returns), 1)) - 1
        volatility = pstdev(returns) if len(returns) > 1 else 0.0
        sharpe = fmean(returns) / volatility * sqrt(252) if volatility else 0.0
        peak = values[0]
        max_drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1)
        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
        }
