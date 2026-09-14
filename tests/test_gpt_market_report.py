from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from quant_workbench.ai.market_report import (
    MARKET_REPORT_JSON_SCHEMA,
    CodexCLIReportClient,
    MarketReportGPTClient,
    build_material_pack,
    generate_gpt_analysis,
    load_openai_report_config,
    material_sha256,
    render_gpt_markdown,
)


def completed_analysis() -> dict[str, object]:
    sector_reports = [
        {
            "rank": rank,
            "sector": f"板块{rank}",
            "research_priority": "高" if rank == 1 else "中",
            "thesis": "由输入趋势与广度支持的研究假设",
            "evidence": ["确定性板块排名证据"],
            "catalysts": ["输入材料未给出明确催化"],
            "risks": ["趋势反转"],
            "invalidation_conditions": ["相对强度失效"],
        }
        for rank in range(1, 6)
    ]
    stock_reports = [
        {
            "rank": rank,
            "symbol": f"S{rank:02d}",
            "name": f"股票{rank}",
            "sector": f"板块{(rank - 1) % 5 + 1}",
            "research_priority": "高" if rank <= 3 else "观察",
            "thesis": "由输入指标支持的研究假设",
            "evidence": ["确定性股票排名证据"],
            "catalysts": ["输入材料未给出明确催化"],
            "risks": ["波动风险"],
            "invalidation_conditions": ["量价关系失效"],
            "horizon": "季度",
            "confidence": 0.6,
        }
        for rank in range(1, 16)
    ]
    return {
        "executive_summary": "本报告只解释输入中的确定性排名。",
        "market_view": {
            "regime": "结构性强势",
            "stance": "优先复核高排名候选",
            "confidence": 0.6,
            "evidence": ["趋势与广度"],
            "risks": ["数据源无 SLA"],
        },
        "sector_reports": sector_reports,
        "stock_reports": stock_reports,
        "options_context": {
            "summary": "仅作市场背景",
            "evidence": [],
            "limitations": ["不是个股信号"],
        },
        "portfolio_risks": ["板块集中"],
        "data_gaps": ["免费数据存在缺口"],
        "disclaimer": "研究用途，不构成投资建议。",
    }


def fixture_material() -> dict[str, object]:
    analysis = completed_analysis()
    return {
        "market": "cn",
        "sectors": [
            {"sector": item["sector"]} for item in analysis["sector_reports"]  # type: ignore[index]
        ],
        "stocks": [
            {"symbol": item["symbol"]} for item in analysis["stock_reports"]  # type: ignore[index]
        ],
    }


class GPTMarketReportTests(unittest.TestCase):
    def test_material_is_strict_json_and_hash_is_stable(self) -> None:
        report = {
            "market": "us",
            "sectors": [{"sector": "Technology", "score": float("nan")}],
            "stocks": [{"symbol": "AAPL", "value": pd.NA}],
            "delivery": {"sent": True},
            "gpt_analysis": {"status": "old"},
        }
        material = build_material_pack(report)
        encoded = json.dumps(material, allow_nan=False, sort_keys=True)
        self.assertIn('"score": null', encoded)
        self.assertNotIn("delivery", material)
        self.assertNotIn("gpt_analysis", material)
        self.assertEqual(material_sha256(material), material_sha256(material))

    def test_client_requests_strict_schema_and_records_audit_fields(self) -> None:
        captured: dict[str, object] = {}

        def fake_post(payload: dict[str, object]) -> dict[str, object]:
            captured.update(payload)
            return {
                "id": "resp_fixture",
                "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(completed_analysis(), ensure_ascii=False),
                            }
                        ]
                    }
                ],
            }

        result = MarketReportGPTClient("test-key", post=fake_post).analyze(
            {"market": "us", "sectors": [], "stocks": []}
        )
        text_format = captured["text"]["format"]  # type: ignore[index]
        self.assertTrue(text_format["strict"])
        self.assertEqual(text_format["schema"], MARKET_REPORT_JSON_SCHEMA)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["response_id"], "resp_fixture")
        self.assertEqual(result["usage"]["total_tokens"], 300)
        self.assertEqual(len(result["analysis"]["stock_reports"]), 15)

    def test_missing_key_is_explicit_and_never_calls_gpt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.mkdir()
            (config / "openai.env").write_text(
                "QW_GPT_PROVIDER=openai_api\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {}, clear=True):
                result = generate_gpt_analysis(
                    {"market": "cn"}, Path(directory) / "data"
                )
        self.assertEqual(result["status"], "disabled")
        self.assertIn("not configured", result["reason"])
        self.assertIsNone(result["analysis"])

    def test_runtime_config_file_and_complete_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            config = Path(directory) / "config"
            config.mkdir()
            (config / "openai.env").write_text(
                "QW_GPT_PROVIDER=openai_api\n"
                "OPENAI_API_KEY=file-key\nOPENAI_REPORT_MODEL=gpt-fixture\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_openai_report_config(data_root)
        self.assertEqual(
            loaded,
            {
                "api_key": "file-key",
                "provider": "openai_api",
                "model": "gpt-fixture",
                "codex_bin": "",
                "codex_model": "",
            },
        )
        envelope = {
            "status": "completed",
            "model": "gpt-fixture",
            "prompt_version": "market_report_v1",
            "material_sha256": "abc",
            "analysis": completed_analysis(),
        }
        markdown = render_gpt_markdown(
            envelope,
            {"market_name": "美股", "stage_name": "盘中"},
        )
        self.assertIn("板块完整解读", markdown)
        self.assertIn("股票完整解读", markdown)
        self.assertIn("S15", markdown)

    def test_codex_cli_uses_ephemeral_read_only_structured_run(self) -> None:
        captured: dict[str, object] = {}

        def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["command"] = command
            captured.update(kwargs)
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(
                json.dumps(completed_analysis(), ensure_ascii=False), encoding="utf-8"
            )
            stdout = json.dumps(
                {
                    "type": "thread.started",
                    "thread_id": "thread_fixture",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                }
            )
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        result = CodexCLIReportClient(
            "/fixture/codex", runner=fake_runner
        ).analyze(fixture_material())
        command = captured["command"]
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("read-only", command)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], "codex_cli_chatgpt")
        self.assertEqual(result["response_id"], "thread_fixture")
        self.assertEqual(result["usage"]["input_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
