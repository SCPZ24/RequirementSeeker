import hashlib
import json
from http.client import BadStatusLine, IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from pipeline.test_invocation import analysis_request

import requirementseeker_agent.model as model
from requirementseeker_agent.model import ModelGatewayError

from .test_gateway_contract import make_call


def fixture() -> dict:
    return {
        "id": "chatcmpl-offline",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-flash",
        "system_fingerprint": "fp_backend_1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"signals":[]}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def call(**updates):
    return make_call("valid_signals").model_copy(
        update={
            "model_name": "deepseek-flash",
            "model_revision": None,
            "scenario_id": None,
            **updates,
        }
    )


def gateway(transport):
    assert hasattr(model, "DeepSeekModelGateway"), "DeepSeek gateway is missing"
    return model.DeepSeekModelGateway(
        api_key="offline-secret", max_input_tokens_per_call=4096, transport=transport
    )


def test_request_preserves_trust_boundaries_and_response_identity():
    captured = []
    body = fixture()
    body["model"] = "provider-drift"
    raw = json.dumps(body, ensure_ascii=False).encode()

    def transport(request, timeout):
        captured.append((request, timeout))
        return raw

    instance = gateway(transport)
    blocks = call().content_blocks
    blocks += [
        blocks[0].model_copy(update={"content": "second system"}),
        blocks[1].model_copy(update={"kind": "repair", "content": "repair JSON"}),
    ]
    result = instance.invoke(call(content_blocks=blocks))
    request, timeout = captured[0]
    data = json.loads(request.data)
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    assert request.get_header("Authorization") == "Bearer offline-secret"
    assert timeout == 30
    assert data["model"] == "deepseek-flash"
    assert data["stream"] is False
    assert data["thinking"] == {"type": "disabled"}
    assert data["response_format"] == {"type": "json_object"}
    assert data["max_tokens"] == 200
    assert [item["role"] for item in data["messages"]] == ["system", "user", "user"]
    assert data["messages"][0]["content"].startswith(blocks[0].content + "\n\nsecond system")
    assert "JSON" in data["messages"][0]["content"]
    assert [item["content"] for item in data["messages"][1:]] == [blocks[1].content, "repair JSON"]
    assert result.payload == '{"signals":[]}'
    assert result.model_name == "provider-drift"
    assert result.model_revision is None
    assert result.provider_system_fingerprint == "fp_backend_1"
    assert result.response_fingerprint == hashlib.sha256(raw).hexdigest()
    assert result.usage.total_tokens == 15
    assert instance.identity.model_revision is None
    assert instance.identity.model_name == "deepseek-flash"
    assert instance.capabilities.max_input_tokens_per_call == 4096
    assert instance.capabilities.supports_text and instance.capabilities.supports_structured_output
    assert not instance.capabilities.supports_images
    assert "offline-secret" not in repr(instance)
    assert len(captured) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"model_name": "other"},
        {"model_revision": "invented"},
        {"scenario_id": "fake"},
        {"max_output_tokens": 0},
        {"max_output_tokens": 393217},
    ],
)
def test_invalid_configuration_never_calls_transport(updates):
    def transport(request, timeout):
        pytest.fail("transport must not run")

    with pytest.raises(ModelGatewayError) as error:
        gateway(transport).invoke(call(**updates))
    assert (error.value.code, error.value.retryable) == ("invalid_configuration", False)


@pytest.mark.parametrize(
    "key,limit",
    [("", 1), ("bad\r\nheader", 1), ("bad\x00header", 1), ("中文", 1), ("ok", 0), ("ok", True)],
)
def test_constructor_rejects_unsafe_key_and_limit(key, limit):
    assert hasattr(model, "DeepSeekModelGateway"), "DeepSeek gateway is missing"
    with pytest.raises(ModelGatewayError) as error:
        model.DeepSeekModelGateway(api_key=key, max_input_tokens_per_call=limit)
    assert str(error.value) == "invalid_configuration"


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication_failed", False),
        (403, "authentication_failed", False),
        (429, "rate_limited", True),
        (408, "timeout", True),
        (504, "timeout", True),
        (500, "transport_error", True),
        (502, "transport_error", True),
        (400, "invalid_configuration", False),
        (302, "invalid_configuration", False),
    ],
)
def test_http_error_is_sanitized_and_not_retried(status, code, retryable):
    attempts = []

    def transport(request, timeout):
        attempts.append(request)
        raise HTTPError(request.full_url, status, "offline-secret body prompt", {}, None)

    with pytest.raises(ModelGatewayError) as error:
        gateway(transport).invoke(call())
    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert error.value.__suppress_context__
    assert str(error.value) == code
    assert len(attempts) == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        (TimeoutError("secret"), "timeout"),
        (URLError(TimeoutError("secret")), "timeout"),
        (URLError("secret"), "transport_error"),
        (ConnectionError("secret"), "transport_error"),
    ],
)
def test_connection_errors(failure, code):
    def transport(request, timeout):
        raise failure

    with pytest.raises(ModelGatewayError) as error:
        gateway(transport).invoke(call())
    assert (error.value.code, error.value.retryable) == (code, True)
    assert str(error.value) == code
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", None),
        ("model", " "),
        ("model", 42),
        ("choices", []),
        ("choices", [{}, {}]),
        ("choices", [{"message": {"role": "user", "content": "x"}, "finish_reason": "stop"}]),
        ("choices", [{"message": {"role": "assistant", "content": {}}, "finish_reason": "stop"}]),
        ("choices", [{"message": {"role": "assistant", "content": "x"}, "finish_reason": None}]),
        ("system_fingerprint", 42),
        ("system_fingerprint", "bad fingerprint"),
        ("usage", {}),
        ("usage", {"prompt_tokens": True, "completion_tokens": 5, "total_tokens": 6}),
        ("usage", {"prompt_tokens": -1, "completion_tokens": 5, "total_tokens": 4}),
        ("usage", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 14}),
    ],
)
def test_malformed_provider_fields_fail_closed(field, value):
    body = fixture()
    body[field] = value
    with pytest.raises(ModelGatewayError) as error:
        gateway(lambda request, timeout: json.dumps(body).encode()).invoke(call())
    assert (error.value.code, error.value.retryable) == ("invalid_configuration", False)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("raw", [b"{", b"[]", b"\xff", b"null"])
def test_invalid_wire_body(raw):
    with pytest.raises(ModelGatewayError, match="invalid_configuration"):
        gateway(lambda request, timeout: raw).invoke(call())


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("stop", "stop"),
        ("length", "length"),
        ("content_filter", "content_filter"),
        ("tool_calls", "unknown"),
    ],
)
@pytest.mark.parametrize("optional", ["missing", "null"])
def test_finish_and_absent_optional_fields(reason, expected, optional):
    body = fixture()
    body["choices"][0]["finish_reason"] = reason
    for field in ("usage", "system_fingerprint"):
        if optional == "missing":
            del body[field]
        else:
            body[field] = None
    result = gateway(lambda request, timeout: json.dumps(body).encode()).invoke(
        call(max_output_tokens=393216)
    )
    assert result.finish_reason == expected
    assert result.usage is None and result.provider_system_fingerprint is None


def test_default_opener_blocks_redirects_and_passes_timeout(monkeypatch):
    from urllib.request import HTTPRedirectHandler

    from requirementseeker_agent.model import deepseek

    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(fixture()).encode()

    class Opener:
        def open(self, request, timeout):
            captured.append((request, timeout))
            return Response()

    def build_opener(handler):
        assert isinstance(handler, HTTPRedirectHandler)
        assert (
            handler.redirect_request(
                Request(
                    "https://api.deepseek.com", headers={"Authorization": "Bearer offline-secret"}
                ),
                None,
                302,
                "redirect",
                {},
                "https://evil.invalid",
            )
            is None
        )
        return Opener()

    monkeypatch.setattr(deepseek, "build_opener", build_opener)
    model.DeepSeekModelGateway(api_key="offline-secret", max_input_tokens_per_call=4096).invoke(
        call(timeout_seconds=7)
    )
    assert len(captured) == 1 and captured[0][1] == 7


@pytest.mark.parametrize(
    "failure,code,retryable",
    [
        (URLError(TimeoutError("secret timeout")), "timeout", True),
        (
            HTTPError("https://evil.invalid", 302, "secret redirect", {}, None),
            "invalid_configuration",
            False,
        ),
    ],
)
def test_default_opener_errors_are_sanitized(monkeypatch, failure, code, retryable):
    from requirementseeker_agent.model import deepseek

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            raise failure

    monkeypatch.setattr(deepseek, "build_opener", lambda handler: Opener())
    with pytest.raises(ModelGatewayError) as error:
        model.DeepSeekModelGateway(api_key="offline-secret", max_input_tokens_per_call=4096).invoke(
            call(timeout_seconds=9)
        )
    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert error.value.__suppress_context__
    assert len(calls) == 1 and calls[0][1] == 9


@pytest.mark.parametrize(
    "revision,code",
    [(None, "requested_revision_required"), ("self-filled", "gateway_revision_required")],
)
def test_real_evaluation_preflight_stops_before_transport(revision, code):
    from requirementseeker_agent.pipeline import (
        RealEvaluationPreflightError,
        preflight_real_evaluation,
    )

    request = analysis_request()
    request = request.model_copy(
        update={
            "model": request.model.model_copy(
                update={"model_name": "deepseek-flash", "revision": revision}
            )
        }
    )

    def transport(request, timeout):
        pytest.fail("preflight must not invoke")

    with pytest.raises(RealEvaluationPreflightError) as error:
        preflight_real_evaluation(request, gateway(transport))
    assert error.value.code == code


def test_ordinary_pipeline_preserves_unversioned_provider_audit():
    from pipeline.test_m2 import inputs

    from requirementseeker_agent.pipeline import analyze_m2
    from requirementseeker_agent.runtime import InMemorySemanticCache

    request, manifest, plan = inputs()
    request = request.model_copy(
        update={
            "model": request.model.model_copy(
                update={"model_name": "deepseek-flash", "revision": None}
            )
        }
    )
    captured = []

    def transport(wire_request, timeout):
        captured.append(wire_request)
        body = fixture()
        messages = json.loads(wire_request.data)["messages"]
        untrusted = messages[1]["content"]
        source = json.loads(untrusted.split("\n", 1)[1].rsplit("\n", 1)[0])
        content = (
            {
                "signals": [
                    {"comment_id": identifier, "kind": "need", "summary": "批量导出"}
                    for identifier in [comment["comment_id"] for comment in source["comments"]]
                ]
            }
            if "comments" in source
            else {"clusters": [{"comment_ids": ["c1", "c2", "c3"], "summary": "批量导出"}]}
        )
        body["choices"][0]["message"]["content"] = json.dumps(content, ensure_ascii=False)
        return json.dumps(body, ensure_ascii=False).encode()

    result = analyze_m2(request, manifest, plan, gateway(transport), InMemorySemanticCache())
    assert result.status == "completed", (
        result.error_code,
        [a.model_dump() for a in result.audits],
    )
    assert len(captured) == 2
    assert len(result.audits) == 2
    for audit in result.audits:
        assert audit.actual_model_name == "deepseek-flash"
        assert audit.actual_revision is None
        assert audit.provider_system_fingerprint == "fp_backend_1"


def test_missing_model_fails_closed():
    body = fixture()
    del body["model"]
    with pytest.raises(ModelGatewayError, match="invalid_configuration"):
        gateway(lambda request, timeout: json.dumps(body).encode()).invoke(call())


def test_invalid_unicode_request_is_sanitized_before_transport():
    def transport(request, timeout):
        pytest.fail("invalid request must not invoke")

    blocks = call().content_blocks
    blocks[0] = blocks[0].model_copy(update={"content": "secret\ud800"})
    with pytest.raises(ModelGatewayError) as error:
        gateway(transport).invoke(call(content_blocks=blocks))
    assert str(error.value) == "invalid_configuration"
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "failure", [IncompleteRead(b"secret partial"), BadStatusLine("secret status")]
)
def test_http_protocol_errors_do_not_expose_partial_response(failure):
    def transport(request, timeout):
        raise failure

    with pytest.raises(ModelGatewayError) as error:
        gateway(transport).invoke(call())
    assert (error.value.code, error.value.retryable) == ("transport_error", True)
    assert str(error.value) == "transport_error"
    assert error.value.__suppress_context__


@pytest.mark.parametrize("stage", ["signals", "cluster"])
def test_stage_parser_accepts_raw_json_and_retains_reference_validation(stage):
    from requirementseeker_agent.pipeline.clusters import _MergeNode
    from requirementseeker_agent.pipeline.clusters import _validate_payload as parse_clusters
    from requirementseeker_agent.pipeline.signals import _validate_payload as parse_signals

    if stage == "signals":
        valid = {"signals": [{"comment_id": "c1", "kind": "need", "summary": "need"}]}
        invalid = {"signals": [{"comment_id": "foreign", "kind": "need", "summary": "need"}]}

        def parse(raw):
            return parse_signals(raw, frozenset({"c1"}))
    else:
        valid = {"clusters": [{"comment_ids": ["c1"], "summary": "need"}]}
        invalid = {"clusters": [{"comment_ids": ["foreign"], "summary": "need"}]}

        def parse(raw):
            return parse_clusters(raw, (_MergeNode("node-1", ("c1",), "need", "need"),))

    assert len(parse(json.dumps(valid))) == 1
    for malformed in ("{", json.dumps(invalid)):
        with pytest.raises(ValueError):
            parse(malformed)


@pytest.mark.parametrize(
    "content", ["{", '{"signals":[{"comment_id":"foreign","kind":"need","summary":"need"}]}']
)
def test_raw_invalid_output_retains_single_pipeline_repair(content):
    from pipeline.test_m2 import inputs

    from requirementseeker_agent.pipeline import analyze_m2
    from requirementseeker_agent.runtime import InMemorySemanticCache

    request, manifest, plan = inputs()
    request = request.model_copy(
        update={
            "model": request.model.model_copy(
                update={"model_name": "deepseek-flash", "revision": None}
            )
        }
    )
    captured = []

    def transport(wire_request, timeout):
        captured.append(wire_request)
        body = fixture()
        body["choices"][0]["message"]["content"] = content
        return json.dumps(body).encode()

    result = analyze_m2(request, manifest, plan, gateway(transport), InMemorySemanticCache())
    assert result.status == "retryable_error"
    assert result.error_code == "model_output_invalid"
    assert len(captured) == 2 and len(result.audits) == 2


def deeply_nested_envelope():
    # 直接构造供应商字节，避免测试构造阶段先触发 json.dumps 的递归限制。
    return b'{"ignored":' + b"[" * 5000 + b"0" + b"]" * 5000 + b"}"


def test_deeply_nested_provider_envelope_is_sanitized():
    with pytest.raises(ModelGatewayError) as error:
        gateway(lambda request, timeout: deeply_nested_envelope()).invoke(call())
    assert (error.value.code, error.value.retryable) == ("invalid_configuration", False)
    assert str(error.value) == "invalid_configuration"
    assert error.value.__suppress_context__


def test_nested_envelope_failure_preserves_invocation_audit_and_settlement():
    from pipeline.test_invocation import ledger

    from requirementseeker_agent.pipeline.invocation import InvocationFailure, invoke_model

    captured = []

    def transport(request, timeout):
        captured.append(request)
        return deeply_nested_envelope()

    budget = ledger()
    with pytest.raises(InvocationFailure) as error:
        invoke_model(analysis_request(), call(), gateway(transport), budget, input_tokens=100)
    assert error.value.code == "invalid_configuration"
    assert error.value.retryable is False
    assert len(captured) == 1 and len(error.value.audits) == 1
    assert error.value.audits[0].status == "error"
    assert error.value.audits[0].error_code == "invalid_configuration"
    snapshot = budget.snapshot()
    assert snapshot.model_calls_consumed == 1
    assert snapshot.input_tokens_consumed == 100
    assert snapshot.output_tokens_consumed == call().max_output_tokens
    assert snapshot.model_calls_reserved == 0
    assert snapshot.input_tokens_reserved == 0
    assert snapshot.output_tokens_reserved == 0
