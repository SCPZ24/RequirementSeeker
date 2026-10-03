from typing import NoReturn

import pytest

from requirementseeker_agent.contracts.requests import AnalysisRequest
from requirementseeker_agent.model import (
    ModelCallRequest,
    ModelCapabilities,
    ModelRuntimeIdentity,
    ScenarioModelGateway,
)
from requirementseeker_agent.pipeline.preflight import (
    RealEvaluationPreflightError,
    preflight_real_evaluation,
)

from .test_invocation import analysis_request


class PreflightGateway(ScenarioModelGateway):
    def __init__(
        self,
        identity: ModelRuntimeIdentity,
        *,
        supports_structured_output: bool = True,
    ) -> None:
        super().__init__("valid_pipeline")
        self._identity = identity
        self._supports_structured_output = supports_structured_output

    @property
    def identity(self) -> ModelRuntimeIdentity:
        return self._identity

    @property
    def capabilities(self) -> ModelCapabilities:
        return super().capabilities.model_copy(
            update={"supports_structured_output": self._supports_structured_output}
        )

    def invoke(self, request: ModelCallRequest) -> NoReturn:
        raise AssertionError("preflight_must_not_invoke_model")


class IdentityMutatingCapabilitiesGateway(PreflightGateway):
    @property
    def capabilities(self) -> ModelCapabilities:
        self._identity.model_revision = "new-revision"
        return super().capabilities


def frozen_request() -> AnalysisRequest:
    request = analysis_request()
    model = request.model.model_copy(
        update={"model_name": "scenario-model", "revision": "m2-fixture-1"}
    )
    return request.model_copy(update={"model": model})


def test_preflight_freezes_matching_non_null_revision_without_invoking_model() -> None:
    frozen = preflight_real_evaluation(
        frozen_request(),
        PreflightGateway(
            ModelRuntimeIdentity(
                model_name="scenario-model",
                model_revision="m2-fixture-1",
            )
        ),
    )

    assert frozen.model_name == "scenario-model"
    assert frozen.model_revision == "m2-fixture-1"


def test_preflight_freezes_identity_before_capability_side_effects() -> None:
    frozen = preflight_real_evaluation(
        frozen_request(),
        IdentityMutatingCapabilitiesGateway(
            ModelRuntimeIdentity(
                model_name="scenario-model",
                model_revision="m2-fixture-1",
            )
        ),
    )

    assert frozen.model_name == "scenario-model"
    assert frozen.model_revision == "m2-fixture-1"


@pytest.mark.parametrize(
    ("request_revision", "gateway_name", "gateway_revision", "code"),
    [
        (None, "scenario-model", "m2-fixture-1", "requested_revision_required"),
        ("m2-fixture-1", "scenario-model", None, "gateway_revision_required"),
        ("m2-fixture-1", "other-model", "m2-fixture-1", "gateway_model_mismatch"),
        ("m2-fixture-1", "scenario-model", "other-revision", "gateway_revision_mismatch"),
    ],
)
def test_preflight_fails_closed_on_unfrozen_identity(
    request_revision: str | None,
    gateway_name: str,
    gateway_revision: str | None,
    code: str,
) -> None:
    request = frozen_request()
    request = request.model_copy(
        update={"model": request.model.model_copy(update={"revision": request_revision})}
    )
    gateway = PreflightGateway(
        ModelRuntimeIdentity(model_name=gateway_name, model_revision=gateway_revision)
    )

    with pytest.raises(RealEvaluationPreflightError) as raised:
        preflight_real_evaluation(request, gateway)

    assert raised.value.code == code


def test_preflight_rejects_gateway_without_structured_output() -> None:
    gateway = PreflightGateway(
        ModelRuntimeIdentity(
            model_name="scenario-model",
            model_revision="m2-fixture-1",
        ),
        supports_structured_output=False,
    )

    with pytest.raises(RealEvaluationPreflightError) as raised:
        preflight_real_evaluation(frozen_request(), gateway)

    assert raised.value.code == "gateway_capability_unsupported"
