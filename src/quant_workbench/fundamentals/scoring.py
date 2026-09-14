from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quant_workbench.core.models import FinancialSnapshot


@dataclass(frozen=True, slots=True)
class FundamentalScore:
    quality: Decimal
    growth: Decimal
    value: Decimal
    safety: Decimal
    composite: Decimal
    flags: tuple[str, ...]


def _bounded(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if high == low:
        return Decimal("0.5")
    return max(Decimal("0"), min(Decimal("1"), (value - low) / (high - low)))


def score_snapshot(snapshot: FinancialSnapshot) -> FundamentalScore:
    """Produce a transparent 0..100 heuristic score, not an investment recommendation."""
    flags: list[str] = []

    quality_parts: list[Decimal] = []
    if snapshot.operating_margin is not None:
        quality_parts.append(_bounded(snapshot.operating_margin, Decimal("-0.10"), Decimal("0.30")))
    if snapshot.net_income is not None and snapshot.operating_cash_flow is not None:
        denom = max(abs(snapshot.net_income), Decimal("1"))
        cash_conversion = snapshot.operating_cash_flow / denom
        quality_parts.append(_bounded(cash_conversion, Decimal("0"), Decimal("1.5")))
        if snapshot.net_income > 0 and snapshot.operating_cash_flow < 0:
            flags.append("盈利为正但经营现金流为负")

    growth_parts = [
        _bounded(value, Decimal("-0.20"), Decimal("0.30"))
        for value in (snapshot.revenue_growth, snapshot.earnings_growth)
        if value is not None
    ]

    safety_parts: list[Decimal] = []
    if snapshot.total_debt is not None and snapshot.total_equity is not None:
        debt_to_equity = snapshot.total_debt / max(snapshot.total_equity, Decimal("1"))
        safety_parts.append(Decimal("1") - _bounded(debt_to_equity, Decimal("0"), Decimal("2")))
        if debt_to_equity > Decimal("2"):
            flags.append("债务权益比偏高")
    if snapshot.cash is not None and snapshot.total_debt is not None:
        safety_parts.append(_bounded(snapshot.cash / max(snapshot.total_debt, Decimal("1")), Decimal("0"), Decimal("1")))

    value_parts: list[Decimal] = []
    if snapshot.market_cap and snapshot.free_cash_flow is not None:
        fcf_yield = snapshot.free_cash_flow / snapshot.market_cap
        value_parts.append(_bounded(fcf_yield, Decimal("-0.02"), Decimal("0.10")))
        if snapshot.free_cash_flow < 0:
            flags.append("自由现金流为负")

    def average(parts: list[Decimal]) -> Decimal:
        return sum(parts, Decimal("0")) / len(parts) if parts else Decimal("0.5")

    quality = average(quality_parts) * 100
    growth = average(growth_parts) * 100
    value = average(value_parts) * 100
    safety = average(safety_parts) * 100
    composite = quality * Decimal("0.30") + growth * Decimal("0.25") + value * Decimal(
        "0.25"
    ) + safety * Decimal("0.20")
    return FundamentalScore(
        quality=quality.quantize(Decimal("0.01")),
        growth=growth.quantize(Decimal("0.01")),
        value=value.quantize(Decimal("0.01")),
        safety=safety.quantize(Decimal("0.01")),
        composite=composite.quantize(Decimal("0.01")),
        flags=tuple(flags),
    )

