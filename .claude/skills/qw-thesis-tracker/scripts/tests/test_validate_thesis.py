"""Tests for the thesis record validator."""

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_thesis as vt  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[2] / "references" / "thesis_template.yaml"
TODAY = date(2026, 9, 21)


@pytest.fixture()
def record():
    return vt.load(TEMPLATE)


def test_template_is_valid_and_only_warns_about_placeholder_hashes(record):
    errors, warnings = vt.validate(record, TODAY)
    assert errors == []
    assert all("material_sha256" in message for message in warnings)


def test_pillars_must_be_falsifiable(record):
    record["pillars"][0]["kill_criterion"] = ""
    record["pillars"][1].pop("metric")
    errors, _ = vt.validate(record, TODAY)
    assert "pillar P1: missing kill_criterion (a thesis must be falsifiable)" in errors
    assert "pillar P2: missing metric (a thesis must be falsifiable)" in errors


@pytest.mark.parametrize("key", ["target_price", "stop_loss", "position_size", "action", "rating"])
def test_instruction_fields_are_rejected_anywhere(record, key):
    polluted = copy.deepcopy(record)
    polluted["valuation_anchor"][key] = "150"
    errors, _ = vt.validate(polluted, TODAY)
    assert any("forbidden field (red line 1)" in message and key in message for message in errors)


def test_instruction_wording_is_rejected_but_negation_is_allowed(record):
    record["updates"].append(
        {
            "date": "2026-09-21",
            "data_point": "Beat",
            "evidence_strength": "high",
            "note": "建议买入，目标价 150",
        }
    )
    errors, _ = vt.validate(record, TODAY)
    assert any("trading-instruction wording" in message for message in errors)
    record["updates"][-1]["note"] = "context only; not a target price"
    assert vt.validate(record, TODAY)[0] == []


def test_update_log_is_append_only_and_references_known_pillars(record):
    record["updates"].append(
        {
            "date": "2026-09-01",
            "data_point": "Older item",
            "evidence_strength": "low",
            "pillar_impacts": [{"pillar": "P9", "effect": "boosts"}],
        }
    )
    errors, _ = vt.validate(record, TODAY)
    assert any("chronological" in message for message in errors)
    assert any("unknown pillar P9" in message for message in errors)
    assert any("effect must be" in message for message in errors)


def test_broken_pillar_with_active_status_is_inconsistent(record):
    record["pillars"][0]["trend"] = "broken"
    errors, _ = vt.validate(record, TODAY)
    assert "pillars ['P1'] are broken but status is still 'active'" in errors
    record["status"] = "falsified"
    assert vt.validate(record, TODAY)[0] == []


def test_review_cadence_and_missing_disconfirming_evidence_warn(record):
    for day in ("2026-09-22", "2026-10-30", "2026-11-15"):
        record["updates"].append(
            {
                "date": day,
                "data_point": "Confirming",
                "evidence_strength": "medium",
                "disconfirming": False,
            }
        )
    _, warnings = vt.validate(record, date(2027, 2, 1))
    assert any("review at least quarterly" in message for message in warnings)
    assert any("no disconfirming evidence" in message for message in warnings)


def test_evidence_needs_published_at_and_known_ids(record):
    record["evidence"][0].pop("published_at")
    record["pillars"][0]["evidence"] = ["E404"]
    errors, _ = vt.validate(record, TODAY)
    assert "evidence E1: missing published_at (needed for PIT)" in errors
    assert "pillar P1: unknown evidence ['E404']" in errors


def test_cli_exit_codes(tmp_path, capsys):
    assert vt.main([str(TEMPLATE), "--today", "2026-09-21"]) == 0
    assert "OK" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text('{"instrument": {"symbol": "X"}}', encoding="utf-8")
    assert vt.main([str(bad)]) == 1
    assert vt.main([str(tmp_path / "missing.yaml")]) == 2
