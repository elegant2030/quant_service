from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Sequence

from quant_workbench.core.models import AssetClass, TargetWeight
from quant_workbench.strategy.base import StrategyContext, adjusted_return


@dataclass(frozen=True, slots=True)
class SectorMomentum:
    """Sector-first, stock-second momentum baseline (long only, equal weight).

    1. Rank sectors by the median trailing return of their point-in-time members.
    2. Keep the ``top_sectors`` with a positive score.
    3. Inside each, hold the ``stocks_per_sector`` strongest names with positive return.

    Instruments whose sector is not known at the decision time are skipped, so a store
    without history produces no positions before its first snapshot.
    """

    taxonomy: str
    classification_level: int = 1
    lookback: int = 60
    top_sectors: int = 3
    stocks_per_sector: int = 3
    eligible_asset_classes: frozenset[AssetClass] = frozenset({AssetClass.EQUITY, AssetClass.ETF})

    def __post_init__(self) -> None:
        if min(self.lookback, self.top_sectors, self.stocks_per_sector) <= 0:
            raise ValueError("strategy windows and selection counts must be positive")

    def targets_in_context(self, context: StrategyContext) -> Sequence[TargetWeight]:
        by_sector: dict[str, list[tuple[str, Decimal]]] = {}
        instruments = {}
        for instrument_id in sorted(context.history):
            bars = context.history[instrument_id]
            if not bars:
                continue
            instrument = bars[-1].instrument
            instruments[instrument_id] = instrument
            if instrument.asset_class not in self.eligible_asset_classes:
                continue
            if bars[-1].timestamp.date() != context.session:
                continue  # suspended or not yet listed today: no fresh price to rank on
            momentum = adjusted_return(bars, self.lookback)
            if momentum is None:
                continue
            membership = context.classification(
                instrument_id, taxonomy=self.taxonomy, level=self.classification_level
            )
            if membership is None:
                continue
            by_sector.setdefault(membership.code, []).append((instrument_id, momentum))

        sector_scores = sorted(
            (
                (sector, median(value for _, value in members))
                for sector, members in by_sector.items()
            ),
            key=lambda item: (-item[1], item[0]),
        )
        chosen_sectors = [sector for sector, score in sector_scores if score > 0][
            : self.top_sectors
        ]
        selected: dict[str, str] = {}
        for sector in chosen_sectors:
            ranked = sorted(by_sector[sector], key=lambda item: (-item[1], item[0]))
            for instrument_id, momentum in ranked[: self.stocks_per_sector]:
                if momentum > 0:
                    selected[instrument_id] = sector

        weight = Decimal("1") / len(selected) if selected else Decimal("0")
        return [
            TargetWeight(
                instrument=instrument,
                weight=weight if instrument_id in selected else Decimal("0"),
                reason=(
                    f"板块动量:{selected[instrument_id]}"
                    if instrument_id in selected
                    else f"{self.lookback}日板块动量未入选"
                ),
            )
            for instrument_id, instrument in instruments.items()
        ]
