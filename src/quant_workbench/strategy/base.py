from __future__ import annotations

from typing import Protocol, Sequence

from quant_workbench.core.models import Bar, TargetWeight


class Strategy(Protocol):
    def targets(self, history: dict[str, Sequence[Bar]]) -> Sequence[TargetWeight]: ...

