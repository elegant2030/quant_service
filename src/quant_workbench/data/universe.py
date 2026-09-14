from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isnan
from typing import Any

from quant_workbench.core.models import Exchange


US_SECTORS = (
    "Basic Materials",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
)


@dataclass(frozen=True, slots=True)
class UniverseMember:
    symbol: str
    name: str
    exchange: Exchange
    sector: str
    market_cap: Decimal | None = None


@dataclass(frozen=True, slots=True)
class UniverseBuildResult:
    members: tuple[UniverseMember, ...]
    requested_size: int
    sector_counts: dict[str, int]
    errors: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return len(self.members) >= self.requested_size


def round_robin_sample(
    candidates: dict[str, list[UniverseMember]], target_size: int
) -> tuple[UniverseMember, ...]:
    """Select evenly across sectors while preferring each sector's ranked order."""
    result: list[UniverseMember] = []
    seen: set[str] = set()
    depth = 0
    while len(result) < target_size:
        added = False
        for sector in sorted(candidates):
            values = candidates[sector]
            if depth >= len(values):
                continue
            member = values[depth]
            if member.symbol not in seen:
                result.append(member)
                seen.add(member.symbol)
                added = True
                if len(result) == target_size:
                    break
        if not added:
            break
        depth += 1
    return tuple(result)


def sector_counts(members: tuple[UniverseMember, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for member in members:
        counts[member.sector] = counts.get(member.sector, 0) + 1
    return dict(sorted(counts.items()))


def market_cap_value(row: Any, key: str) -> Decimal | None:
    value = row.get(key)
    try:
        number = float(value)
        return None if isnan(number) else Decimal(str(number))
    except (TypeError, ValueError):
        return None
