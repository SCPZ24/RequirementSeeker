import pytest

from requirementseeker_agent.model import ModelGatewayError, ScenarioModelGateway

from .test_gateway_contract import make_call


@pytest.mark.parametrize(
    "scenario_id",
    [
        "valid_signals",
        "no_signal",
        "disputed_signal",
        "valid_clusters",
        "bad_json",
        "missing_fields",
        "extra_fields",
        "wrong_type",
        "oversized_output",
        "unknown_comment",
        "cross_video_comment",
        "altered_quote",
        "duplicate_cluster_member",
        "repair_success",
        "repair_failure",
    ],
)
def test_payload_scenarios_are_deterministic(scenario_id: str) -> None:
    gateway = ScenarioModelGateway()

    first = gateway.invoke(make_call(scenario_id))
    second = gateway.invoke(make_call(scenario_id))

    assert first == second
    assert first.response_fingerprint == second.response_fingerprint


@pytest.mark.parametrize(
    ("scenario_id", "retryable"),
    [
        ("timeout", True),
        ("rate_limited", True),
        ("transport_error", True),
        ("authentication_failed", False),
        ("capability_unsupported", False),
        ("cancelled", False),
    ],
)
def test_error_scenarios_use_normalized_codes(scenario_id: str, retryable: bool) -> None:
    with pytest.raises(ModelGatewayError) as raised:
        ScenarioModelGateway().invoke(make_call(scenario_id))

    assert raised.value.code == scenario_id
    assert raised.value.retryable is retryable


def test_missing_usage_is_explicit() -> None:
    response = ScenarioModelGateway().invoke(make_call("usage_missing"))

    assert response.usage is None


def test_request_hash_changes_the_response_fingerprint() -> None:
    gateway = ScenarioModelGateway()

    first = gateway.invoke(make_call("valid_signals", attempt=1))
    second = gateway.invoke(make_call("valid_signals", attempt=2))

    assert first.response_fingerprint != second.response_fingerprint


def test_unknown_scenario_fails_as_invalid_configuration() -> None:
    with pytest.raises(ModelGatewayError) as raised:
        ScenarioModelGateway().invoke(make_call("unknown-scenario"))

    assert raised.value.code == "invalid_configuration"
    assert raised.value.retryable is False
