from __future__ import annotations

from dataclasses import asdict
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install the API extras: pip install -e '.[api]'") from exc

from quant_workbench.ai.openai_client import OpenAIResearchClient
from quant_workbench.derivatives.options import OptionType, black_scholes

app = FastAPI(title="Quant Workbench API", version="0.1.0")


class OptionRequest(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    time_to_expiry: float = Field(gt=0)
    risk_free_rate: float = 0.03
    volatility: float = Field(default=0.25, gt=0)
    option_type: OptionType
    dividend_yield: float = 0.0


class Material(BaseModel):
    source: str
    content: str


class AnalysisRequest(BaseModel):
    subject: str
    task: str
    materials: list[Material]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/options/analytics")
def option_analytics(request: OptionRequest) -> dict[str, float]:
    return asdict(black_scholes(**request.model_dump()))


@app.post("/v1/research/analyze")
def research_analyze(request: AnalysisRequest) -> dict[str, Any]:
    try:
        result = OpenAIResearchClient().analyze(
            subject=request.subject,
            task=request.task,
            materials=[material.model_dump() for material in request.materials],
        )
        return asdict(result)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

