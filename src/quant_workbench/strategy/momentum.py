from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from quant_workbench.core.models import Bar, TargetWeight
from quant_workbench.strategy.base import adjusted_return


@dataclass(slots=True)
class CrossSectionalMomentum:
    """Long-only reference strategy using trailing corporate-action adjusted returns."""

    lookback: int = 20
    top_n: int = 3

    def targets(self, history: dict[str, Sequence[Bar]]) -> list[TargetWeight]:
        ranked: list[tuple[Decimal, Bar]] = []
        for bars in history.values():
            momentum = adjusted_return(bars, self.lookback)
            if momentum is None:
                continue
            ranked.append((momentum, bars[-1]))
        selected = sorted(ranked, key=lambda item: item[0], reverse=True)[: self.top_n]
        positive = [bar for momentum, bar in selected if momentum > 0]
        weight = Decimal("1") / len(positive) if positive else Decimal("0")
        selected_ids = {bar.instrument.id for bar in positive}
        result: list[TargetWeight] = []
        for bars in history.values():
            if not bars:
                continue
            instrument = bars[-1].instrument
            result.append(
                TargetWeight(
                    instrument=instrument,
                    weight=weight if instrument.id in selected_ids else Decimal("0"),
                    reason=f"{self.lookback}日横截面动量",
                )
            )
        return result

