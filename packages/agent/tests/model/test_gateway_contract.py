import pytest
from pydantic import ValidationError

from requirementseeker_agent.model import (
    ControlledContentBlock,
    ModelCallRequest,
    ModelGateway,
    ModelGatewayError,
    ScenarioModelGateway,
)


def make_call(scenario_id: str, *, attempt: int = 1) -> ModelCallRequest:
    return ModelCallRequest(
        invocation_id=f"invoke-{scenario_id}-{attempt}",
        stage="signals",
        model_config_ref="model-config-1",
        model_name="fake-model",
        model_revision="fixture-1",
        prompt_version="signal-v1",
        content_blocks=[
            ControlledContentBlock(
                block_id="system-1",
                kind="system",
                content="Return the requested schema.",
            ),
            ControlledContentBlock(
                block_id="comments-1",
                kind="untrusted_data",
                content='[{"comment_id":"c1","text":"需要批量导出"}]',
            ),
        ],
        expected_schema_name="need-signals",
        expected_schema_version="1.0",
        max_output_tokens=200,
        timeout_seconds=30,
        attempt=attempt,
        scenario_id=scenario_id,
    )


def assert_gateway_conformance(gateway: ModelGateway) -> None:
    assert gateway.capabilities.supports_text is True
    assert gateway.capabilities.supports_structured_output is True
    response = gateway.invoke(make_call("valid_signals"))
    assert response.payload == {
        "signals": [{"comment_id": "c1", "kind": "need", "summary": "批量导出"}]
    }
    assert response.finish_reason == "stop"
    assert response.usage is not None

    with pytest.raises(ModelGatewayError) as raised:
        gateway.invoke(make_call("timeout"))
    assert raised.value.code == "timeout"
    assert raised.value.retryable is True


def test_scenario_gateway_satisfies_shared_contract() -> None:
    assert_gateway_conformance(ScenarioModelGateway())


def test_call_request_rejects_secret_fields() -> None:
    data = make_call("valid_signals").model_dump(mode="json")
    data["api_key"] = "secret"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ModelCallRequest.model_validate(data)
