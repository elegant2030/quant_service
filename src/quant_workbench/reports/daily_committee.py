"""Once-daily, evidence-bound review using every installed project skill.

The skill text is embedded in the model material and hashed.  This avoids relying on
ambient Codex configuration while making the daily "all skills" claim auditable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from quant_workbench.ai.market_report import (
    _codex_metadata,
    _json_safe,
    _resolve_codex_bin,
    load_openai_report_config,
    material_sha256,
)
from quant_workbench.ops.alert import TelegramNotifier, load_alert_config
from quant_workbench.store import DatasetStore

NEW_YORK = ZoneInfo("America/New_York")
PROMPT_VERSION = "daily_all_skills_committee_v1"
EXPERT_ROLES = (
    "宏观与跨资产专家",
    "量价与多策略专家",
    "基本面与估值专家",
    "消息事件与产业链专家",
    "期权与波动率专家",
    "组合风险与压力测试专家",
    "策略红队与审计专家",
)


def _strings() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


DAILY_COMMITTEE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "executive_summary",
        "market_macro_assessment",
        "expert_panel",
        "disagreements",
        "strategy_review",
        "options_review",
        "skill_audit",
        "risk_register",
        "data_gaps",
        "disclaimer",
    ],
    "properties": {
        "executive_summary": {"type": "string"},
        "market_macro_assessment": {
            "type": "object",
            "additionalProperties": False,
            "required": ["facts", "expectations", "cross_asset_implications", "uncertainties"],
            "properties": {
                "facts": _strings(),
                "expectations": _strings(),
                "cross_asset_implications": _strings(),
                "uncertainties": _strings(),
            },
        },
        "expert_panel": {
            "type": "array",
            "minItems": len(EXPERT_ROLES),
            "maxItems": len(EXPERT_ROLES),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "conclusion", "evidence", "objections", "invalidation"],
                "properties": {
                    "role": {"type": "string"},
                    "conclusion": {"type": "string"},
                    "evidence": _strings(),
                    "objections": _strings(),
                    "invalidation": _strings(),
                },
            },
        },
        "disagreements": _strings(),
        "strategy_review": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "basis", "missing_tests"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["描述性研究", "样本外验证", "可交易候选", "证据不足"],
                },
                "basis": _strings(),
                "missing_tests": _strings(),
            },
        },
        "options_review": {
            "type": "object",
            "additionalProperties": False,
            "required": ["conclusion", "evidence", "liquidity_limits"],
            "properties": {
                "conclusion": {"type": "string"},
                "evidence": _strings(),
                "liquidity_limits": _strings(),
            },
        },
        "skill_audit": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["skill", "status", "evidence", "impact"],
                "properties": {
                    "skill": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["applied", "not_applicable", "blocked"],
                    },
                    "evidence": {"type": "string"},
                    "impact": {"type": "string"},
                },
            },
        },
        "risk_register": _strings(),
        "data_gaps": _strings(),
        "disclaimer": {"type": "string"},
    },
}


@dataclass(frozen=True, slots=True)
class CommitteeArtifacts:
    json_path: Path
    markdown_path: Path
    sent: bool
    send_error: str | None


def _skill_name(text: str, fallback: str) -> str:
    match = re.search(r"(?m)^name:\s*[\"']?([^\n\"']+)", text)
    return match.group(1).strip() if match else fallback


def resolve_skill_root(data_root: Path, configured: Path | None = None) -> Path:
    candidates = [
        configured,
        Path(os.environ["QW_SKILLS_ROOT"]) if os.environ.get("QW_SKILLS_ROOT") else None,
        data_root.parent / "skills",
        Path.cwd() / ".claude" / "skills",
        Path(__file__).resolve().parents[3] / ".claude" / "skills",
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir() and list(candidate.glob("*/SKILL.md")):
            return candidate
    raise RuntimeError("project skill directory was not found")


def load_skill_pack(data_root: Path, configured: Path | None = None) -> list[dict[str, str]]:
    root = resolve_skill_root(data_root, configured)
    result = []
    for path in sorted(root.glob("*/SKILL.md")):
        content = path.read_text(encoding="utf-8")
        result.append(
            {
                "skill": _skill_name(content, path.parent.name),
                "source": str(path),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "instructions": content,
            }
        )
    return result


def _latest_market_report(store: DatasetStore, market: str, as_of: date) -> dict[str, Any]:
    candidates: list[tuple[str, int, Path]] = []
    priority = {"premarket": 1, "midday": 2, "postmarket": 3}
    root = store.root / "reports" / "market" / market
    for path in root.glob("*/*.json"):
        if path.stem.endswith("-gpt") or path.parent.name > as_of.isoformat():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stage = str(payload.get("stage") or path.stem)
        candidates.append(
            (
                str(payload.get("report_date") or path.parent.name),
                priority.get(stage, 0),
                path,
            )
        )
    if not candidates:
        raise RuntimeError(f"no {market} market report is available on or before {as_of}")
    selected = max(candidates)[2]
    return json.loads(selected.read_text(encoding="utf-8"))


def _trim_report(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "market",
        "market_name",
        "stage",
        "stage_name",
        "report_date",
        "generated_at",
        "data_mode",
        "data_as_of",
        "symbols_analyzed",
        "scoring",
        "sectors",
        "stocks",
        "options",
        "macro_events",
        "evidence_coverage",
        "disclaimer",
    )
    return {key: report.get(key) for key in keys}


def build_committee_material(
    store: DatasetStore, as_of: date, skill_root: Path | None = None
) -> dict[str, Any]:
    skills = load_skill_pack(store.root, skill_root)
    return _json_safe({
        "as_of": as_of.isoformat(),
        "market_reports": {
            market: _trim_report(_latest_market_report(store, market, as_of))
            for market in ("us", "cn")
        },
        "required_expert_roles": list(EXPERT_ROLES),
        "required_skill_names": [item["skill"] for item in skills],
        "skills": skills,
        "hard_limits": [
            "LLM只解释确定性数字，不重算价格、收益、Greeks、仓位或限额",
            "每个skill必须输出一条审计；不适用也要给原因",
            "预期或预测不得表述为已经发生",
            "输出是研究候选，不是交易指令",
        ],
    })


def _validate_committee(material: dict[str, Any], analysis: dict[str, Any]) -> None:
    expected_skills = material["required_skill_names"]
    actual_skills = [item.get("skill") for item in analysis.get("skill_audit") or []]
    if actual_skills != expected_skills:
        raise ValueError("daily committee did not audit every skill in deterministic order")
    expected_roles = material["required_expert_roles"]
    actual_roles = [item.get("role") for item in analysis.get("expert_panel") or []]
    if actual_roles != expected_roles:
        raise ValueError("daily committee did not include every required expert role")


class CodexCLICommitteeClient:
    def __init__(
        self,
        codex_bin: str,
        model: str | None = None,
        timeout_seconds: int = 1800,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.model = model or "chatgpt_account_default"
        self.timeout_seconds = timeout_seconds
        self._runner = runner or subprocess.run

    def analyze(self, material: dict[str, Any]) -> dict[str, Any]:
        input_hash = material_sha256(material)
        prompt = "\n\n".join(
            [
                "你是Quant Workbench每日投委会。只使用<materials>；不要使用工具、网络或本地文件。",
                "依次执行skills中每份完整指令，由七类专家独立给结论，再披露争议。",
                "不得重算输入数字。预期/预测不等于事实。证据不足时标blocked或not_applicable。",
                "skill_audit必须与required_skill_names同名同序，expert_panel必须与required_expert_roles同名同序。",
                "不得给出买卖、目标价、仓位或保证收益；用中文并严格符合JSON Schema。",
                f"material_sha256: {input_hash}",
                "<materials>\n"
                + json.dumps(material, ensure_ascii=False, allow_nan=False)
                + "\n</materials>",
            ]
        )
        with tempfile.TemporaryDirectory(prefix="qw-daily-committee-") as directory:
            workdir = Path(directory)
            schema_path = workdir / "schema.json"
            output_path = workdir / "answer.json"
            schema_path.write_text(
                json.dumps(DAILY_COMMITTEE_SCHEMA, ensure_ascii=False), encoding="utf-8"
            )
            command = [
                self.codex_bin,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--cd",
                str(workdir),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--color",
                "never",
                "--json",
            ]
            if self.model != "chatgpt_account_default":
                command.extend(["--model", self.model])
            command.append("-")
            result = self._runner(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                env={**os.environ, "NO_COLOR": "1"},
            )
            if result.returncode != 0:
                error = (result.stderr or result.stdout or "unknown Codex CLI error")[-1600:]
                raise RuntimeError(f"Codex CLI exited {result.returncode}: {error.strip()}")
            if not output_path.is_file():
                raise RuntimeError("Codex CLI produced no daily committee output")
            analysis = json.loads(output_path.read_text(encoding="utf-8"))
            session_id, usage = _codex_metadata(result.stdout)
        _validate_committee(material, analysis)
        return {
            "status": "completed",
            "provider": "codex_cli_chatgpt",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "material_sha256": input_hash,
            "response_id": session_id,
            "usage": usage,
            "analysis": analysis,
        }


def generate_daily_committee(
    material: dict[str, Any], data_root: Path, model: str | None = None, client: Any = None
) -> dict[str, Any]:
    if client is not None:
        return client.analyze(material)
    config = load_openai_report_config(data_root)
    codex_bin = _resolve_codex_bin(config["codex_bin"])
    if not codex_bin:
        return {
            "status": "disabled",
            "provider": "codex_cli_chatgpt",
            "reason": "Codex CLI executable not found; all-skills committee was not run",
            "model": model or config["codex_model"] or "chatgpt_account_default",
            "prompt_version": PROMPT_VERSION,
            "material_sha256": material_sha256(material),
            "analysis": None,
        }
    try:
        return CodexCLICommitteeClient(
            codex_bin, model=model or config["codex_model"] or None
        ).analyze(material)
    except Exception as exc:
        return {
            "status": "failed",
            "provider": "codex_cli_chatgpt",
            "reason": f"{type(exc).__name__}: {exc}",
            "model": model or config["codex_model"] or "chatgpt_account_default",
            "prompt_version": PROMPT_VERSION,
            "material_sha256": material_sha256(material),
            "analysis": None,
        }


def render_committee_markdown(envelope: dict[str, Any], material: dict[str, Any]) -> str:
    lines = [
        f"# Quant Workbench 每日全技能投委会 {material['as_of']}",
        "",
        f"状态：{envelope['status']}；通道：{envelope.get('provider')}；模型：{envelope.get('model')}",
        f"提示词：{envelope.get('prompt_version')}；证据包 SHA-256："
        f"`{envelope.get('material_sha256')}`",
        f"Skills：{len(material['required_skill_names'])} 个；专家：{len(EXPERT_ROLES)} 位",
        "",
    ]
    analysis = envelope.get("analysis")
    if envelope["status"] != "completed" or not isinstance(analysis, dict):
        lines.append(f"> 未完成：{envelope.get('reason', 'unknown reason')}。没有补造分析。")
        return "\n".join(lines)
    macro = analysis["market_macro_assessment"]
    lines.extend(
        ["## 执行摘要", "", analysis["executive_summary"], "", "## 宏观与跨资产", ""]
    )
    macro_sections = (
        ("事实", "facts"),
        ("预期", "expectations"),
        ("传导", "cross_asset_implications"),
        ("不确定性", "uncertainties"),
    )
    for title, key in macro_sections:
        lines.append(f"### {title}")
        lines.extend(f"- {item}" for item in macro[key])
        lines.append("")
    lines.extend(["## 七专家会诊", ""])
    for item in analysis["expert_panel"]:
        lines.extend(
            [
                f"### {item['role']}",
                "",
                item["conclusion"],
                "",
                f"- 证据：{'；'.join(item['evidence'])}",
                f"- 反对意见：{'；'.join(item['objections'])}",
                f"- 失效条件：{'；'.join(item['invalidation'])}",
                "",
            ]
        )
    lines.extend(["## 主要分歧", "", *[f"- {item}" for item in analysis["disagreements"]], ""])
    strategy = analysis["strategy_review"]
    lines.extend(
        [
            "## 策略审计",
            "",
            f"状态：{strategy['status']}",
            *[f"- 依据：{item}" for item in strategy["basis"]],
            *[f"- 缺口：{item}" for item in strategy["missing_tests"]],
            "",
            "## 期权审查",
            "",
            analysis["options_review"]["conclusion"],
            *[f"- 证据：{item}" for item in analysis["options_review"]["evidence"]],
            *[f"- 限制：{item}" for item in analysis["options_review"]["liquidity_limits"]],
            "",
            "## Skill 使用审计",
            "",
            "| Skill | 状态 | 证据 | 对结论的影响 |",
            "|---|---|---|---|",
        ]
    )
    for item in analysis["skill_audit"]:
        lines.append(
            f"| {item['skill']} | {item['status']} | {item['evidence']} | {item['impact']} |"
        )
    lines.extend(
        [
            "",
            "## 风险登记",
            "",
            *[f"- {item}" for item in analysis["risk_register"]],
            "",
            "## 数据缺口",
            "",
            *[f"- {item}" for item in analysis["data_gaps"]],
            "",
            f"> {analysis['disclaimer']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_daily_committee(
    store: DatasetStore,
    now: datetime | None = None,
    *,
    report_date: date | None = None,
    send: bool = True,
    force: bool = False,
    model: str | None = None,
    skill_root: Path | None = None,
    client: Any = None,
) -> tuple[dict[str, Any], CommitteeArtifacts]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    day = report_date or current.astimezone(NEW_YORK).date()
    base = Path("reports") / "daily" / day.isoformat() / "all-skills-committee"
    json_path = store.root / base.with_suffix(".json")
    markdown_path = store.root / base.with_suffix(".md")
    if json_path.is_file() and not force:
        envelope = json.loads(json_path.read_text(encoding="utf-8"))
        delivery = envelope.get("delivery") or {}
        return envelope, CommitteeArtifacts(
            json_path, markdown_path, bool(delivery.get("sent")), delivery.get("error")
        )
    material = build_committee_material(store, day, skill_root)
    envelope = generate_daily_committee(material, store.root, model=model, client=client)
    markdown_path = store.write_text(
        base.with_suffix(".md"), render_committee_markdown(envelope, material)
    )
    delivery = {"attempted": False, "sent": False, "error": None}
    if send and envelope["status"] == "completed":
        notifier = TelegramNotifier(load_alert_config(store.root))
        analysis = envelope["analysis"]
        summary = (
            f"Quant Workbench 每日全技能投委会 | {day}\n"
            f"{len(material['required_skill_names'])}个Skills / {len(EXPERT_ROLES)}位专家\n"
            f"{analysis['executive_summary']}\n"
            "详版见附件；研究用途，不构成投资建议。"
        )
        summary_result = notifier.send(summary)
        document_result = notifier.send_document(markdown_path, f"每日全技能投委会 {day}")
        errors = [item for item in (summary_result.error, document_result.error) if item]
        delivery = {
            "attempted": True,
            "sent": summary_result.ok and document_result.ok,
            "error": " | ".join(errors) or None,
            "summary_message_id": summary_result.message_id,
            "document_message_id": document_result.message_id,
        }
    envelope["report_date"] = day.isoformat()
    envelope["skills"] = [
        {key: item[key] for key in ("skill", "source", "sha256")} for item in material["skills"]
    ]
    envelope["expert_roles"] = list(EXPERT_ROLES)
    envelope["source_reports"] = {
        market: {
            "report_date": report["report_date"],
            "stage": report["stage"],
            "data_as_of": report["data_as_of"],
        }
        for market, report in material["market_reports"].items()
    }
    envelope["delivery"] = delivery
    stored = store.write_json(base.with_suffix(".json"), envelope)
    return envelope, CommitteeArtifacts(
        stored, markdown_path, bool(delivery.get("sent")), delivery.get("error")
    )


def daily_committee_due(now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(NEW_YORK)
    return local.time().hour >= 17
