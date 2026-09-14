from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import pstdev
from typing import Sequence

from quant_workbench.core.models import Bar, TargetWeight


@dataclass(slots=True)
class EqualWeight:
    def targets(self, history: dict[str, Sequence[Bar]]) -> list[TargetWeight]:
        available = [bars[-1].instrument for bars in history.values() if bars]
        weight = Decimal("1") / len(available) if available else Decimal("0")
        return [TargetWeight(item, weight, "等权配置") for item in available]


@dataclass(slots=True)
class CrossSectionalLowVolatility:
    lookback: int = 60
    top_n: int = 30

    def targets(self, history: dict[str, Sequence[Bar]]) -> list[TargetWeight]:
        ranked: list[tuple[float, Bar]] = []
        for bars in history.values():
            if len(bars) <= self.lookback:
                continue
            window = bars[-self.lookback - 1 :]
            returns = [float(window[i].close / window[i - 1].close - 1) for i in range(1, len(window))]
            if len(returns) > 1:
                ranked.append((pstdev(returns), bars[-1]))
        selected = sorted(ranked, key=lambda item: item[0])[: self.top_n]
        selected_ids = {bar.instrument.id for _, bar in selected}
        weight = Decimal("1") / len(selected_ids) if selected_ids else Decimal("0")
        return [
            TargetWeight(
                bars[-1].instrument,
                weight if bars[-1].instrument.id in selected_ids else Decimal("0"),
                f"{self.lookback}日低波动",
            )
            for bars in history.values()
            if bars
        ]
