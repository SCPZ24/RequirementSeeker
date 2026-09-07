"""Schema export from the same models used at the Python boundary."""

from typing import Any, Literal

from pydantic import BaseModel

from .contracts import AnalysisRequest, AnalysisResult

ContractKind = Literal["request", "result"]


def contract_model(kind: ContractKind) -> type[BaseModel]:
    return AnalysisRequest if kind == "request" else AnalysisResult


def export_schema(kind: ContractKind) -> dict[str, Any]:
    return contract_model(kind).model_json_schema()
