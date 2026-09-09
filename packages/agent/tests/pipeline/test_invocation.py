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


def response(
    payload: object, *, usage: TokenUsage | None = DEFAULT_USAGE
) -> ModelCallResponse:
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

    def __init__(self, actions: Sequence[ModelCallResponse | ModelGatewayError]) -> None:
        self._actions = deque(actions)
        self.calls: list[ModelCallRequest] = []

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_text=True,
            supports_structured_output=True,
            supports_images=False,
            max_input_tokens_per_call=4096,
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

    result = invoke_model(
        analysis_request(), model_call(), gateway, budget, input_tokens=120
    )

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
