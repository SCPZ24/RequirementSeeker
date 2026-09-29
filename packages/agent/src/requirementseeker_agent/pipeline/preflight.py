"""真实评测付费调用前的模型身份与能力准入。"""

from typing import Literal

from ..contracts.requests import AnalysisRequest
from ..model import FrozenModelIdentity, VersionedModelGateway

PreflightErrorCode = Literal[
    "requested_revision_required",
    "gateway_revision_required",
    "gateway_model_mismatch",
    "gateway_revision_mismatch",
    "gateway_capability_unsupported",
]


class RealEvaluationPreflightError(RuntimeError):
    def __init__(self, code: PreflightErrorCode) -> None:
        super().__init__(code)
        self.code = code


def preflight_real_evaluation(
    request: AnalysisRequest,
    gateway: VersionedModelGateway,
) -> FrozenModelIdentity:
    requested_revision = request.model.revision
    if requested_revision is None:
        raise RealEvaluationPreflightError("requested_revision_required")
    identity = gateway.identity
    if identity.model_revision is None:
        raise RealEvaluationPreflightError("gateway_revision_required")
    if identity.model_name != request.model.model_name:
        raise RealEvaluationPreflightError("gateway_model_mismatch")
    if identity.model_revision != requested_revision:
        raise RealEvaluationPreflightError("gateway_revision_mismatch")
    capabilities = gateway.capabilities
    if (
        not request.model.supports_text
        or not capabilities.supports_text
        or not capabilities.supports_structured_output
    ):
        raise RealEvaluationPreflightError("gateway_capability_unsupported")
    return FrozenModelIdentity(
        model_name=identity.model_name,
        model_revision=identity.model_revision,
    )
