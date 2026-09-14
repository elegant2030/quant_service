from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "sentiment": {"type": "string", "enum": ["利好", "中性", "利空", "信息不足"]},
        "score": {"type": "number", "minimum": -1, "maximum": 1},
        "time_horizon": {"type": "string", "enum": ["日内", "短期", "中期", "长期", "不确定"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "counter_evidence": {"type": "array", "items": {"type": "string"}},
        "catalysts": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "sentiment",
        "score",
        "time_horizon",
        "confidence",
        "evidence",
        "counter_evidence",
        "catalysts",
        "risks",
        "data_gaps",
    ],
}


@dataclass(frozen=True, slots=True)
class ResearchAnalysis:
    summary: str
    sentiment: str
    score: float
    time_horizon: str
    confidence: float
    evidence: tuple[str, ...]
    counter_evidence: tuple[str, ...]
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    data_gaps: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResearchAnalysis":
        return cls(
            summary=str(value["summary"]),
            sentiment=str(value["sentiment"]),
            score=float(value["score"]),
            time_horizon=str(value["time_horizon"]),
            confidence=float(value["confidence"]),
            evidence=tuple(map(str, value["evidence"])),
            counter_evidence=tuple(map(str, value["counter_evidence"])),
            catalysts=tuple(map(str, value["catalysts"])),
            risks=tuple(map(str, value["risks"])),
            data_gaps=tuple(map(str, value["data_gaps"])),
        )

