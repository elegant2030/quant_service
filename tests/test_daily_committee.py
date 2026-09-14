from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quant_workbench.reports.daily_committee import (
    EXPERT_ROLES,
    _canonical_coverage,
    build_daily_committee,
    load_skill_pack,
)
from quant_workbench.store import DatasetStore


class FakeCommitteeClient:
    def analyze(self, material: dict[str, object]) -> dict[str, object]:
        skills = material["required_skill_names"]
        roles = material["required_expert_roles"]
        analysis = {
            "executive_summary": "双市场材料完成全技能审计，结论仍属研究候选。",
            "market_macro_assessment": {
                "facts": ["材料内事实已区分"],
                "expectations": ["加息预期未写成决定"],
                "cross_asset_implications": ["仅作风险背景"],
                "uncertainties": ["缺少利率概率曲线"],
            },
            "expert_panel": [
                {
                    "role": role,
                    "conclusion": "需继续验证",
                    "evidence": ["输入报告"],
                    "objections": ["样本短"],
                    "invalidation": ["排名失效"],
                }
                for role in roles
            ],
            "disagreements": ["趋势与宏观风险存在冲突"],
            "strategy_review": {
                "status": "描述性研究",
                "basis": ["存在确定性排名"],
                "missing_tests": ["缺少完整样本外检验"],
            },
            "options_review": {
                "conclusion": "只能作为市场背景",
                "evidence": ["输入含期权快照"],
                "liquidity_limits": ["中间价不是成交价"],
            },
            "skill_audit": [
                {
                    "skill": skill,
                    "status": "applied",
                    "evidence": "已按指令检查",
                    "impact": "降低结论强度",
                }
                for skill in skills
            ],
            "risk_register": ["宏观事件风险"],
            "data_gaps": ["PIT股票池缺失"],
            "disclaimer": "研究用途，不构成投资建议。",
        }
        return {
            "status": "completed",
            "provider": "fixture",
            "model": "fixture",
            "prompt_version": "fixture",
            "material_sha256": "abc",
            "analysis": analysis,
        }


class DailyCommitteeTests(unittest.TestCase):
    def test_coverage_accepts_legacy_bars_without_adjustment_factor_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(directory)
            store.write_rows(
                "daily_bars",
                "us",
                "fixture",
                date(2026, 9, 14),
                [
                    {
                        "source": "fixture",
                        "source_symbol": "AAPL",
                        "symbol": "AAPL",
                        "market": "us",
                        "session_date": "2026-09-11",
                        "effective_at": "2026-09-11",
                        "retrieved_at": "2026-09-14T00:00:00+00:00",
                        "open": 100.0,
                        "high": 102.0,
                        "low": 99.0,
                        "close": 101.0,
                        "volume": 1_000_000,
                        "adjustment": "provider_adjusted",
                        "schema_version": 1,
                    }
                ],
                "legacy",
            )
            coverage = _canonical_coverage(store)
        self.assertEqual(coverage["daily_bars"][0]["symbols"], 1)
        self.assertEqual(coverage["daily_bars"][0]["factor_rows"], 0)

    def test_skill_pack_reads_every_directory_and_hashes_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alpha").mkdir()
            (root / "beta").mkdir()
            (root / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\nA")
            (root / "beta" / "SKILL.md").write_text("---\nname: beta\n---\nB")
            skills = load_skill_pack(root / "data", root)
        self.assertEqual([item["skill"] for item in skills], ["alpha", "beta"])
        self.assertTrue(all(len(item["sha256"]) == 64 for item in skills))

    def test_daily_report_audits_all_skills_and_experts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DatasetStore(root / "data")
            skills = root / "skills"
            for name in ("alpha", "beta"):
                (skills / name).mkdir(parents=True)
                (skills / name / "SKILL.md").write_text(
                    f"---\nname: {name}\n---\n# {name}\n", encoding="utf-8"
                )
            for market in ("us", "cn"):
                store.write_json(
                    f"reports/market/{market}/2026-09-14/postmarket.json",
                    {
                        "market": market,
                        "market_name": market,
                        "stage": "postmarket",
                        "stage_name": "盘后",
                        "report_date": "2026-09-14",
                        "data_as_of": "2026-09-14",
                        "sectors": [],
                        "stocks": [],
                    },
                )
            report, artifacts = build_daily_committee(
                store,
                datetime(2026, 9, 14, 22, tzinfo=timezone.utc),
                report_date=date(2026, 9, 14),
                send=False,
                skill_root=skills,
                client=FakeCommitteeClient(),
            )
        self.assertEqual(len(report["skills"]), 2)
        self.assertEqual(len(report["expert_roles"]), len(EXPERT_ROLES))
        self.assertEqual(
            [item["skill"] for item in report["analysis"]["skill_audit"]],
            ["alpha", "beta"],
        )
        self.assertTrue(artifacts.json_path.name.endswith(".json"))

    def test_material_converts_nan_from_prior_reports_to_null(self) -> None:
        from quant_workbench.reports.daily_committee import build_committee_material

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DatasetStore(root / "data")
            skills = root / "skills" / "alpha"
            skills.mkdir(parents=True)
            (skills / "SKILL.md").write_text("---\nname: alpha\n---\nA")
            for market in ("us", "cn"):
                store.write_json(
                    f"reports/market/{market}/2026-09-14/postmarket.json",
                    {
                        "market": market,
                        "stage": "postmarket",
                        "report_date": "2026-09-14",
                        "data_as_of": "2026-09-14",
                        "sectors": [],
                        "stocks": [{"symbol": "X", "news_score": math.nan}],
                    },
                )
            material = build_committee_material(
                store, date(2026, 9, 14), root / "skills"
            )
        encoded = json.dumps(material, allow_nan=False)
        self.assertIn('"news_score": null', encoded)
        self.assertIn("canonical_coverage", material)
        self.assertIn("strategy_research", material)


if __name__ == "__main__":
    unittest.main()
