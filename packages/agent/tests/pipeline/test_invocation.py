from collections import deque
from collections.abc import Sequence
from pathlib import Path

import pytest

from requirementseeker_agent.contracts.analysis import TokenUsage
from requirementseeker_agent.contracts.requests import AnalysisRequest
from requirementseeker_agent.model import (
    ControlledContentBlock,
    GatewayErrorCode,
    ModelCallRequest,
    ModelCallResponse,
    ModelCapabilities,
    ModelGatewayError,
)
from requirementseeker_agent.model.types import FrozenModelIdentity
from requirementseeker_agent.pipeline.invocation import (
    InvocationCancelled,
    InvocationFailure,
    invoke_model,
)
from requirementseeker_agent.runtime import BudgetLedger, BudgetLimitExceeded

FIXTURES = Path(__file__).parents[1] / "fixtures" / "valid"


def analysis_request() -> AnalysisRequest:
    return AnalysisRequest.model_validate_json((FIXTURES / "request.json").read_text("utf-8"))


def model_call() -> ModelCallRequest:
    request = analysis_request()
    return ModelCallRequest(
        invocation_id="signal-batch-1",
        stage="signals",
        model_config_ref=request.model.config_ref,
        model_name=request.model.model_name,
        model_revision=request.model.revision,
        prompt_version="signal-v1",
        content_blocks=[
            ControlledContentBlock(block_id="system", kind="system", content="rules"),
            ControlledContentBlock(block_id="data", kind="untrusted_data", content="[]"),
        ],
        expected_schema_name="need-signals",
        expected_schema_version="1.0",
        max_output_tokens=50,
        timeout_seconds=30,
        attempt=1,
    )


DEFAULT_USAGE = TokenUsage(input_tokens=80, output_tokens=10, total_tokens=90)


def response(payload: object, *, usage: TokenUsage | None = DEFAULT_USAGE) -> ModelCallResponse:
    return ModelCallResponse(
        payload=payload,
        model_name="scenario-model",
        model_revision="m2-fixture-1",
        finish_reason="stop",
        usage=usage,
        response_fingerprint="0" * 64,
    )


class ScriptedGateway:
    """按顺序返回结果或错误，并保留真实收到的调用供边界断言。"""

    def __init__(
        self,
        actions: Sequence[ModelCallResponse | ModelGatewayError],
        *,
        max_input_tokens_per_call: int = 4096,
    ) -> None:
        self._actions = deque(actions)
        self.calls: list[ModelCallRequest] = []
        self.max_input_tokens_per_call = max_input_tokens_per_call

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_text=True,
            supports_structured_output=True,
            supports_images=False,
            max_input_tokens_per_call=self.max_input_tokens_per_call,
        )

    def invoke(self, request: ModelCallRequest) -> ModelCallResponse:
        self.calls.append(request)
        action = self._actions.popleft()
        if isinstance(action, ModelGatewayError):
            raise action
        return action


def ledger() -> BudgetLedger:
    return BudgetLedger.from_analysis_budget(analysis_request().budget)


def test_transport_error_is_retried_and_every_attempt_is_audited() -> None:
    gateway = ScriptedGateway(
        [
            ModelGatewayError("timeout", retryable=True),
            response(
                {"signals": []},
                usage=TokenUsage(input_tokens=80, output_tokens=10, total_tokens=90),
            ),
        ]
    )

    result = invoke_model(analysis_request(), model_call(), gateway, ledger(), input_tokens=100)

    assert [call.attempt for call in gateway.calls] == [1, 2]
    assert [audit.status for audit in result.audits] == ["error", "success"]
    assert [audit.attempt for audit in result.audits] == [1, 2]
    assert result.next_attempt == 3
    failed, succeeded = result.audits
    assert failed.model_name == "synthetic-model"
    assert failed.requested_model_name == "synthetic-model"
    assert failed.requested_revision is None
    assert failed.actual_model_name is None
    assert failed.actual_revision is None
    assert succeeded.model_name == "scenario-model"
    assert succeeded.requested_model_name == "synthetic-model"
    assert succeeded.requested_revision is None
    assert succeeded.actual_model_name == "scenario-model"
    assert succeeded.actual_revision == "m2-fixture-1"


def test_success_audit_keeps_missing_actual_revision_without_requested_fallback() -> None:
    call = model_call().model_copy(update={"model_revision": "requested-revision"})
    reply = response({"signals": []}).model_copy(update={"model_revision": None})
    gateway = ScriptedGateway([reply])

    result = invoke_model(analysis_request(), call, gateway, ledger(), input_tokens=100)

    audit = result.audits[0]
    assert audit.requested_model_name == call.model_name
    assert audit.requested_revision == "requested-revision"
    assert audit.actual_model_name == reply.model_name
    assert audit.actual_revision is None


@pytest.mark.parametrize(
    ("actual_name", "actual_revision", "error_code"),
    [
        ("scenario-model", "m2-fixture-1", None),
        ("other-model", "m2-fixture-1", "model_identity_mismatch"),
        ("scenario-model", "other-revision", "model_identity_mismatch"),
        ("scenario-model", None, "model_identity_unverifiable"),
    ],
)
def test_frozen_identity_checks_actual_response_after_settlement(
    actual_name: str, actual_revision: str | None, error_code: str | None
) -> None:
    frozen = FrozenModelIdentity(model_name="scenario-model", model_revision="m2-fixture-1")
    call = model_call().model_copy(update=frozen.model_dump())
    reply = response({"signals": []}).model_copy(
        update={"model_name": actual_name, "model_revision": actual_revision}
    )
    gateway = ScriptedGateway([reply])
    budget = ledger()

    if error_code is None:
        result = invoke_model(
            analysis_request(), call, gateway, budget, input_tokens=100, frozen_identity=frozen
        )
        assert result.response == reply
        audits = result.audits
        assert audits[0].status == "success"
    else:
        with pytest.raises(InvocationFailure) as raised:
            invoke_model(
                analysis_request(), call, gateway, budget, input_tokens=100, frozen_identity=frozen
            )
        assert raised.value.code == error_code
        assert raised.value.retryable is False
        audits = raised.value.audits
        assert audits[0].status == "error"
        assert audits[0].error_code == error_code

    assert len(gateway.calls) == 1
    assert len(audits) == 1
    assert audits[0].requested_model_name == call.model_name
    assert audits[0].requested_revision == call.model_revision
    assert audits[0].actual_model_name == actual_name
    assert audits[0].actual_revision == actual_revision
    assert audits[0].usage == DEFAULT_USAGE
    snapshot = budget.snapshot()
    assert snapshot.model_calls_consumed == 1
    assert snapshot.input_tokens_consumed == 80
    assert snapshot.output_tokens_consumed == 10
    assert snapshot.model_calls_reserved == 0


@pytest.mark.parametrize(
    ("requested_name", "requested_revision"),
    [
        ("other-model", "m2-fixture-1"),
        ("scenario-model", "other-revision"),
        ("scenario-model", None),
    ],
)
def test_frozen_identity_rejects_inconsistent_call_before_gateway(
    requested_name: str, requested_revision: str | None
) -> None:
    frozen = FrozenModelIdentity(model_name="scenario-model", model_revision="m2-fixture-1")
    call = model_call().model_copy(
        update={"model_name": requested_name, "model_revision": requested_revision}
    )
    gateway = ScriptedGateway([response({"signals": []})])
    budget = ledger()

    with pytest.raises(InvocationFailure) as raised:
        invoke_model(
            analysis_request(), call, gateway, budget, input_tokens=100, frozen_identity=frozen
        )

    assert raised.value.code == "invalid_configuration"
    assert raised.value.retryable is False
    assert raised.value.audits == ()
    assert gateway.calls == []
    assert budget.snapshot().model_calls_consumed == 0
    assert budget.snapshot().model_calls_reserved == 0


def test_budget_exceeded_retains_drifted_identity_before_identity_failure() -> None:
    frozen = FrozenModelIdentity(model_name="scenario-model", model_revision="m2-fixture-1")
    call = model_call().model_copy(update={**frozen.model_dump(), "max_output_tokens": 5})
    usage = TokenUsage(input_tokens=20, output_tokens=20, total_tokens=40)
    reply = response({}, usage=usage).model_copy(update={"model_name": "other-model"})
    gateway = ScriptedGateway([reply])
    budget = ledger()

    with pytest.raises(BudgetLimitExceeded) as raised:
        invoke_model(
            analysis_request(), call, gateway, budget, input_tokens=5, frozen_identity=frozen
        )

    assert raised.value.resource == "input_tokens"
    assert len(gateway.calls) == 1
    audit = raised.value.audits[0]
    assert audit.requested_model_name == frozen.model_name
    assert audit.requested_revision == frozen.model_revision
    assert audit.actual_model_name == "other-model"
    assert audit.actual_revision == reply.model_revision
    assert audit.usage == usage
    assert budget.snapshot().input_tokens_consumed == 20


@pytest.mark.parametrize("code", ["authentication_failed", "capability_unsupported"])
def test_fatal_error_is_not_retried(code: GatewayErrorCode) -> None:
    gateway = ScriptedGateway([ModelGatewayError(code, retryable=False)])

    with pytest.raises(InvocationFailure) as raised:
        invoke_model(analysis_request(), model_call(), gateway, ledger(), input_tokens=100)

    assert raised.value.code == code
    assert raised.value.retryable is False
    assert len(raised.value.audits) == 1
    assert len(gateway.calls) == 1


def test_transport_retry_limit_is_enforced() -> None:
    gateway = ScriptedGateway(
        [ModelGatewayError("transport_error", retryable=True) for _ in range(3)]
    )

    with pytest.raises(InvocationFailure) as raised:
        invoke_model(analysis_request(), model_call(), gateway, ledger(), input_tokens=100)

    assert raised.value.code == "transport_error"
    assert raised.value.retryable is True
    assert len(raised.value.audits) == 3
    assert len(gateway.calls) == 3


def test_missing_usage_consumes_the_full_reservation() -> None:
    gateway = ScriptedGateway([response({"signals": []}, usage=None)])
    budget = ledger()

    result = invoke_model(analysis_request(), model_call(), gateway, budget, input_tokens=120)

    snapshot = budget.snapshot()
    assert result.audits[0].usage is None
    assert snapshot.input_tokens_consumed == 120
    assert snapshot.output_tokens_consumed == 50


def test_cancellation_before_call_creates_no_audit() -> None:
    request = analysis_request().model_copy(update={"cancellation_requested": True})
    gateway = ScriptedGateway([response({"signals": []})])

    with pytest.raises(InvocationCancelled) as raised:
        invoke_model(request, model_call(), gateway, ledger(), input_tokens=100)

    assert raised.value.audits == ()
    assert gateway.calls == []


def test_budget_failure_happens_before_gateway_call() -> None:
    request = analysis_request()
    limits = request.budget.model_copy(update={"max_model_calls": 0})
    request = request.model_copy(update={"budget": limits})
    gateway = ScriptedGateway([response({"signals": []})])

    with pytest.raises(BudgetLimitExceeded, match="model_calls"):
        invoke_model(
            request,
            model_call(),
            gateway,
            BudgetLedger.from_analysis_budget(limits),
            input_tokens=100,
        )

    assert gateway.calls == []


def test_retry_reservation_failure_preserves_prior_attempt_audit() -> None:
    request = analysis_request()
    limits = request.budget.model_copy(update={"max_model_calls": 1})
    request = request.model_copy(update={"budget": limits})
    budget = BudgetLedger.from_analysis_budget(limits)
    gateway = ScriptedGateway([ModelGatewayError("timeout", retryable=True)])

    with pytest.raises(BudgetLimitExceeded) as raised:
        invoke_model(request, model_call(), gateway, budget, input_tokens=100)

    assert raised.value.resource == "model_calls"
    assert [audit.status for audit in raised.value.audits] == ["error"]
    assert [audit.error_code for audit in raised.value.audits] == ["timeout"]
    assert len(gateway.calls) == 1


def test_overreported_usage_preserves_successful_call_audit() -> None:
    request = analysis_request()
    limits = request.budget.model_copy(
        update={"max_model_calls": 1, "max_input_tokens": 10, "max_output_tokens": 10}
    )
    request = request.model_copy(update={"budget": limits})
    budget = BudgetLedger.from_analysis_budget(limits)
    usage = TokenUsage(input_tokens=20, output_tokens=20, total_tokens=40)
    gateway = ScriptedGateway([response({"signals": []}, usage=usage)])
    call = model_call().model_copy(update={"max_output_tokens": 5})

    with pytest.raises(BudgetLimitExceeded) as raised:
        invoke_model(request, call, gateway, budget, input_tokens=5)

    assert raised.value.resource == "input_tokens"
    assert [audit.status for audit in raised.value.audits] == ["success"]
    assert raised.value.audits[0].usage == usage
    assert budget.snapshot().input_tokens_consumed == 20


def test_cancellation_after_response_creates_cancelled_audit() -> None:
    states = iter((False, True))

    def probe() -> bool:
        return next(states)

    gateway = ScriptedGateway(
        [
            response(
                {"signals": []},
                usage=TokenUsage(input_tokens=80, output_tokens=10, total_tokens=90),
            )
        ]
    )

    with pytest.raises(InvocationCancelled) as raised:
        invoke_model(
            analysis_request(),
            model_call(),
            gateway,
            ledger(),
            input_tokens=100,
            cancellation_probe=probe,
        )

    assert len(raised.value.audits) == 1
    assert raised.value.audits[0].status == "cancelled"
    assert raised.value.audits[0].usage is not None
    audit = raised.value.audits[0]
    assert audit.model_name == "scenario-model"
    assert audit.requested_model_name == "synthetic-model"
    assert audit.requested_revision is None
    assert audit.actual_model_name == "scenario-model"
    assert audit.actual_revision == "m2-fixture-1"
