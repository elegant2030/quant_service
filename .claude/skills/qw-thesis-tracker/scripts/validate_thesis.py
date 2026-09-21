#!/usr/bin/env python3
"""Validate a Quant Workbench thesis record (YAML or JSON).

Enforces the properties that make a thesis useful and keep it inside the project's red
lines: falsifiable pillars, evidence references, append-only dated updates, tracking of
disconfirming evidence, review cadence, and NO trading instructions, price targets or
position sizing. Deterministic; standard library only (PyYAML is used when available,
otherwise pass a .json file).

Exit codes: 0 valid (warnings allowed), 1 invalid, 2 unusable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

STATUSES = {"active", "weakened", "falsified", "retired", "watching"}
TRENDS = {"on_track", "ahead", "behind", "broken", "pending"}
STRENGTHS = {"low", "medium", "high"}
EVIDENCE_KINDS = {"filing", "announcement", "transcript", "dataset", "news"}
REVIEW_MAX_AGE_DAYS = 92

# Red line 1: these keys must not exist anywhere in the record.
FORBIDDEN_KEYS = {
    "target_price",
    "price_target",
    "stop_loss",
    "stop_loss_trigger",
    "take_profit",
    "position",
    "position_size",
    "position_sizing",
    "weight",
    "shares",
    "quantity",
    "action",
    "recommendation",
    "rating",
    "order",
    "entry_price",
    "exit_price",
}
# ...and these phrases must not appear in free text (unless the sentence negates them).
FORBIDDEN_PHRASES = re.compile(
    r"(建议买入|建议卖出|建议加仓|建议减仓|目标价\s*[:：]?\s*\d|止损位|仓位\s*\d|"
    r"\b(buy|sell|trim|add to|increase|exit) (the )?position\b|\bprice target of\b|\bstrong buy\b)",
    re.IGNORECASE,
)
NEGATION = re.compile(r"(不|非|never|not a|no |without|context only)", re.IGNORECASE)


def load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise SystemExit("PyYAML is not installed; convert the record to .json") from exc
    return yaml.safe_load(text)


def _iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _walk(node: Any, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}" if path else str(key), key, value
            yield from _walk(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def validate(record: Any, today: date | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    today = today or date.today()
    if not isinstance(record, dict):
        return ["record must be a mapping"], []

    for field in ("instrument", "statement", "pillars", "risks", "evidence", "updates", "status"):
        if not record.get(field):
            errors.append(f"missing or empty: {field}")
    instrument = record.get("instrument") or {}
    for field in ("symbol", "market", "exchange"):
        if not instrument.get(field):
            errors.append(f"missing: instrument.{field}")
    if instrument.get("market") not in (None, "us", "cn"):
        errors.append("instrument.market must be us or cn")
    if record.get("status") and record["status"] not in STATUSES:
        errors.append(f"status must be one of {sorted(STATUSES)}")

    # --- red line 1: no instructions, targets or sizing anywhere
    for location, key, value in _walk(record):
        if str(key).lower() in FORBIDDEN_KEYS:
            errors.append(f"forbidden field (red line 1): {location}")
        if (
            isinstance(value, str)
            and FORBIDDEN_PHRASES.search(value)
            and not NEGATION.search(value)
        ):
            errors.append(f"trading-instruction wording (red line 1) at {location}")

    # --- evidence registry
    evidence_ids: set[str] = set()
    for index, item in enumerate(record.get("evidence") or []):
        identifier = str(item.get("id") or "")
        if not identifier:
            errors.append(f"evidence[{index}] missing id")
            continue
        if identifier in evidence_ids:
            errors.append(f"duplicate evidence id: {identifier}")
        evidence_ids.add(identifier)
        if item.get("kind") not in EVIDENCE_KINDS:
            errors.append(f"evidence {identifier}: kind must be one of {sorted(EVIDENCE_KINDS)}")
        if not item.get("reference"):
            errors.append(f"evidence {identifier}: missing reference")
        if not item.get("published_at"):
            errors.append(f"evidence {identifier}: missing published_at (needed for PIT)")
        sha = str(item.get("material_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            warnings.append(
                f"evidence {identifier}: material_sha256 is not a stored-document hash yet"
            )

    # --- pillars must be falsifiable and measurable
    pillars = record.get("pillars") or []
    if pillars and not 3 <= len(pillars) <= 5:
        warnings.append(f"{len(pillars)} pillars; 3 to 5 keeps a thesis reviewable")
    pillar_ids: set[str] = set()
    for index, pillar in enumerate(pillars):
        identifier = str(pillar.get("id") or f"#{index}")
        if identifier in pillar_ids:
            errors.append(f"duplicate pillar id: {identifier}")
        pillar_ids.add(identifier)
        for field in ("claim", "metric", "expectation", "kill_criterion"):
            if not str(pillar.get(field) or "").strip():
                errors.append(
                    f"pillar {identifier}: missing {field} (a thesis must be falsifiable)"
                )
        if pillar.get("trend") not in TRENDS:
            errors.append(f"pillar {identifier}: trend must be one of {sorted(TRENDS)}")
        unknown = [ref for ref in pillar.get("evidence") or [] if ref not in evidence_ids]
        if unknown:
            errors.append(f"pillar {identifier}: unknown evidence {unknown}")
        if pillar.get("trend") not in (None, "pending") and not pillar.get("evidence"):
            warnings.append(f"pillar {identifier}: status '{pillar.get('trend')}' without evidence")

    if pillars and len(record.get("risks") or []) < 3:
        warnings.append("fewer than 3 invalidating risks listed")

    # --- catalysts
    for index, catalyst in enumerate(record.get("catalysts") or []):
        if _iso(catalyst.get("date")) is None:
            errors.append(f"catalysts[{index}]: date must be ISO")
        unknown = [ref for ref in catalyst.get("tests_pillars") or [] if ref not in pillar_ids]
        if unknown:
            errors.append(f"catalysts[{index}]: unknown pillars {unknown}")

    # --- append-only dated update log
    previous: date | None = None
    disconfirming = 0
    for index, update in enumerate(record.get("updates") or []):
        when = _iso(update.get("date"))
        if when is None:
            errors.append(f"updates[{index}]: date must be ISO")
            continue
        if when > today:
            errors.append(f"updates[{index}]: dated in the future")
        if previous and when < previous:
            errors.append(f"updates[{index}]: log must be chronological (append only)")
        previous = when
        if not str(update.get("data_point") or "").strip():
            errors.append(f"updates[{index}]: missing data_point")
        if update.get("evidence_strength") not in STRENGTHS:
            errors.append(f"updates[{index}]: evidence_strength must be one of {sorted(STRENGTHS)}")
        for impact in update.get("pillar_impacts") or []:
            if impact.get("pillar") not in pillar_ids:
                errors.append(f"updates[{index}]: unknown pillar {impact.get('pillar')}")
            if impact.get("effect") not in {"strengthens", "weakens", "neutral", "falsifies"}:
                errors.append(
                    f"updates[{index}]: effect must be strengthens/weakens/neutral/falsifies"
                )
        if update.get("disconfirming"):
            disconfirming += 1
    if len(record.get("updates") or []) >= 4 and disconfirming == 0:
        warnings.append("no disconfirming evidence logged in 4+ updates; look for it deliberately")

    # --- consistency and review cadence
    broken = [p.get("id") for p in pillars if p.get("trend") == "broken"]
    if broken and record.get("status") == "active":
        errors.append(f"pillars {broken} are broken but status is still 'active'")
    reviewed = _iso(record.get("last_reviewed_at"))
    if reviewed is None:
        errors.append("last_reviewed_at must be an ISO date")
    elif record.get("status") in {"active", "weakened", "watching"}:
        age = (today - reviewed).days
        if age > REVIEW_MAX_AGE_DAYS:
            warnings.append(f"last reviewed {age} days ago; review at least quarterly")

    anchor = record.get("valuation_anchor") or {}
    if anchor and not anchor.get("method"):
        errors.append("valuation_anchor needs a method (numbers come from deterministic code)")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--today", help="Override today's date (YYYY-MM-DD) for cadence checks")
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else None
    worst = 0
    for path in args.files:
        if not path.is_file():
            print(f"{path}: not found", file=sys.stderr)
            worst = max(worst, 2)
            continue
        try:
            record = load(path)
        except Exception as exc:
            print(f"{path}: cannot parse: {exc}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        errors, warnings = validate(record, today)
        state = "INVALID" if errors else "OK"
        print(f"{path}: {state} ({len(errors)} errors, {len(warnings)} warnings)")
        for message in errors:
            print(f"  ERROR   {message}")
        for message in warnings:
            print(f"  WARNING {message}")
        if errors:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
