from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping, Protocol, Sequence, runtime_checkable

from quant_workbench.core.classification import ClassificationMembership, ClassificationStore
from quant_workbench.core.models import Bar, TargetWeight


class Strategy(Protocol):
    def targets(self, history: dict[str, Sequence[Bar]]) -> Sequence[TargetWeight]: ...


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """What a strategy may know when it decides after the close of ``session``.

    ``now`` is the knowledge clock used for point-in-time lookups: anything with
    ``available_from <= now`` is visible. The engine sets it to the end of the session
    day, because the decision executes at the next open.
    """

    now: datetime
    session: date
    step: int
    history: Mapping[str, Sequence[Bar]]
    classifications: ClassificationStore

    def classification(
        self, instrument_id: str, *, taxonomy: str, level: int = 1
    ) -> ClassificationMembership | None:
        return self.classifications.resolve(
            instrument_id,
            taxonomy=taxonomy,
            level=level,
            session=self.session,
            known_at=self.now,
        )


@runtime_checkable
class ContextStrategy(Protocol):
    """Strategies that need the clock or point-in-time reference data."""

    def targets_in_context(self, context: StrategyContext) -> Sequence[TargetWeight]: ...


def adjusted_return(bars: Sequence[Bar], lookback: int) -> Decimal | None:
    """Close-to-close total return over ``lookback`` bars, corporate-action aware.

    Raw bars carry a single-event ``adj_factor`` on ex-dates; the product of the factors
    after the base bar turns the raw price ratio into a split/dividend adjusted return.
    Bars without factors (already adjusted or legacy data) behave as factor 1.
    """
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if len(bars) <= lookback:
        return None
    window = bars[-1 - lookback :]
    factor = Decimal("1")
    for bar in window[1:]:
        if bar.adj_factor is not None:
            factor *= bar.adj_factor
    return window[-1].close * factor / window[0].close - 1
