from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from math import sqrt
from statistics import fmean, pstdev
from typing import Sequence

from quant_workbench.backtest.rules import CostModel, TradingRules
from quant_workbench.core.classification import ClassificationStore
from quant_workbench.core.models import Bar, EquityPoint, Fill, Side, TargetWeight
from quant_workbench.strategy.base import ContextStrategy, Strategy, StrategyContext


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
    corporate_actions: tuple[str, ...] = ()


class BacktestEngine:
    """Daily-bar engine; signals at close execute at the next available open."""

    def __init__(
        self,
        initial_cash: Decimal = Decimal("1000000"),
        cost_model: CostModel | None = None,
        trading_rules: TradingRules | None = None,
        rebalance_every: int = 5,
        classifications: ClassificationStore | None = None,
        apply_corporate_actions: bool = True,
    ):
        self.initial_cash = initial_cash
        self.cost_model = cost_model or CostModel()
        self.trading_rules = trading_rules or TradingRules()
        self.rebalance_every = rebalance_every
        self.classifications = classifications or ClassificationStore()
        self.apply_corporate_actions = apply_corporate_actions

    @staticmethod
    def _knowledge_clock(session: date) -> datetime:
        """Decisions made after the close of ``session`` execute at the next open, so
        everything published by the end of that calendar day (UTC) is knowable."""
        return datetime.combine(session, time(23, 59, 59), timezone.utc)

    def _apply_corporate_action(self, bar: Bar, position: Position) -> tuple[Decimal, str | None]:
        """Adjust a held position for the bar's ex-date facts; returns (cash, note).

        Raw bars record what happened on the session: ``split_ratio`` scales the share
        count, ``dividend`` pays cash per share (post-split terms). Sources that only
        give a combined ``adj_factor`` (BaoStock) are handled as a cost-free total
        return reinvestment, which may leave fractional lots.
        """
        if position.quantity == 0:
            return Decimal("0"), None
        key = bar.instrument.id
        stamp = bar.timestamp.date().isoformat()
        cash = Decimal("0")
        notes: list[str] = []
        if bar.split_ratio is not None and bar.split_ratio != 1:
            position.quantity *= bar.split_ratio
            position.average_cost /= bar.split_ratio
            notes.append(f"split x{bar.split_ratio}")
        if bar.dividend is not None and bar.dividend > 0:
            cash = position.quantity * bar.dividend * bar.instrument.multiplier
            notes.append(f"dividend {bar.dividend}/share cash {cash}")
        if not notes and bar.adj_factor is not None and bar.adj_factor != 1:
            position.quantity *= bar.adj_factor
            position.average_cost /= bar.adj_factor
            notes.append(f"reinvested adj_factor x{bar.adj_factor}")
        if not notes:
            return Decimal("0"), None
        return cash, f"{stamp} {key}: " + ", ".join(notes)

    def run(self, bars: Sequence[Bar], strategy: Strategy | ContextStrategy) -> BacktestResult:
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

        corporate_actions: list[str] = []
        uses_context = isinstance(strategy, ContextStrategy)

        for index, timestamp in enumerate(timeline):
            current = by_time[timestamp]
            if self.apply_corporate_actions:
                # Ex-date effects apply before the open, ahead of any pending orders.
                for key, bar in current.items():
                    if key in positions:
                        received, note = self._apply_corporate_action(bar, positions[key])
                        cash += received
                        if note:
                            corporate_actions.append(note)
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
                if uses_context:
                    session = timestamp.date()
                    context = StrategyContext(
                        now=self._knowledge_clock(session),
                        session=session,
                        step=index,
                        history=history,
                        classifications=self.classifications,
                    )
                    pending = list(strategy.targets_in_context(context))
                else:
                    pending = list(strategy.targets(history))

        return BacktestResult(
            tuple(curve),
            tuple(fills),
            tuple(rejected),
            self._metrics(curve),
            tuple(corporate_actions),
        )

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
