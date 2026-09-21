"""Point-in-time classification (sector / industry / index membership).

Ported from the standalone ``quant_service`` prototype. Every membership carries two
clocks:

- ``valid_from`` / ``valid_to``: when the instrument belonged to the group.
- ``available_from``: when *we* could have known it (snapshot or publication time).

Lookups always pass ``known_at`` so a backtest cannot see a classification that was
only captured later. Free sources give no classification history, so memberships
built from today's universe file are only "known" from the snapshot time onwards
unless explicitly backdated, which is flagged in ``source`` and must be reported as a
look-ahead assumption.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

BACKDATED_SUFFIX = ":backdated"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ClassificationMembership:
    instrument_id: str  # ``Instrument.id``, e.g. ``NASDAQ:AAPL``
    taxonomy: str
    level: int
    code: str
    name: str
    valid_from: date
    available_from: datetime
    valid_to: date | None = None
    source: str = "unknown"

    def __post_init__(self) -> None:
        _require_aware(self.available_from, "available_from")
        if not self.instrument_id.strip():
            raise ValueError("instrument_id must not be empty")
        if not self.taxonomy.strip() or not self.code.strip() or not self.name.strip():
            raise ValueError("taxonomy, code and name must not be empty")
        if self.level <= 0:
            raise ValueError("classification level must be positive")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")

    def is_active(self, session: date) -> bool:
        return self.valid_from <= session and (self.valid_to is None or session <= self.valid_to)

    @property
    def is_backdated(self) -> bool:
        return self.source.endswith(BACKDATED_SUFFIX)


class ClassificationStore:
    """Point-in-time classification lookup with an explicit knowledge clock."""

    def __init__(self, memberships: Iterable[ClassificationMembership] = ()) -> None:
        self._memberships = tuple(memberships)
        self._by_instrument: dict[str, list[ClassificationMembership]] = {}
        for item in self._memberships:
            self._by_instrument.setdefault(item.instrument_id, []).append(item)

    @property
    def memberships(self) -> tuple[ClassificationMembership, ...]:
        return self._memberships

    @property
    def has_backdated_memberships(self) -> bool:
        return any(item.is_backdated for item in self._memberships)

    def resolve(
        self,
        instrument_id: str,
        *,
        taxonomy: str,
        level: int,
        session: date,
        known_at: datetime,
    ) -> ClassificationMembership | None:
        _require_aware(known_at, "known_at")
        matches = [
            item
            for item in self._by_instrument.get(instrument_id, ())
            if item.taxonomy == taxonomy
            and item.level == level
            and item.is_active(session)
            and item.available_from <= known_at
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: (item.valid_from, item.available_from), reverse=True)
        return matches[0]

    def members(
        self,
        *,
        taxonomy: str,
        level: int,
        code: str,
        session: date,
        known_at: datetime,
    ) -> frozenset[str]:
        _require_aware(known_at, "known_at")
        return frozenset(
            item.instrument_id
            for item in self._memberships
            if item.taxonomy == taxonomy
            and item.level == level
            and item.code == code
            and item.is_active(session)
            and item.available_from <= known_at
        )


def classification_store_from_universe(
    path: Path | str,
    *,
    taxonomy: str,
    available_from: datetime,
    level: int = 1,
    source: str = "universe_csv",
    backdate_to: date | None = None,
) -> ClassificationStore:
    """Build a store from a universe CSV (``symbol,name,exchange,sector,...``).

    By default memberships are valid and known only from ``available_from`` (the
    snapshot time). ``backdate_to`` pretends today's sectors were already known on
    that date: convenient for descriptive research, but it is look-ahead, so the
    source is suffixed with ``:backdated`` and results must be labelled accordingly.
    """
    _require_aware(available_from, "available_from")
    memberships: list[ClassificationMembership] = []
    with Path(path).open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            sector = (row.get("sector") or "").strip()
            symbol = (row.get("symbol") or "").strip()
            exchange = (row.get("exchange") or "").strip()
            if not sector or not symbol or not exchange:
                continue
            if backdate_to is not None:
                valid_from = backdate_to
                known = datetime.combine(backdate_to, datetime.min.time(), available_from.tzinfo)
                origin = source + BACKDATED_SUFFIX
            else:
                valid_from = available_from.date()
                known = available_from
                origin = source
            memberships.append(
                ClassificationMembership(
                    instrument_id=f"{exchange}:{symbol.upper()}",
                    taxonomy=taxonomy,
                    level=level,
                    code=sector,
                    name=sector,
                    valid_from=valid_from,
                    available_from=known,
                    source=origin,
                )
            )
    return ClassificationStore(memberships)
