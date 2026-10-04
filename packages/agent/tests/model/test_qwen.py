import hashlib
import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request

import pytest

import requirementseeker_agent as public
import requirementseeker_agent.model as model
from requirementseeker_agent.model import ModelGatewayError

from .test_gateway_contract import make_call

MODEL = "qwen-plus-2025-12-01"
REVISION = "2025-12-01"
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def fixture():
    return {
        "id": "chatcmpl-offline",
        "object": "chat.completion",
        "created": 1,
        "model": MODEL,
        "system_fingerprint": None,
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
            "model_name": MODEL,
            "model_revision": REVISION,
            "scenario_id": None,
            **updates,
        }
    )


def gateway(transport=None, **updates):
    assert hasattr(model, "QwenModelGateway"), "Qwen gateway is missing"
    return model.QwenModelGateway(
        api_key="offline-secret", max_input_tokens_per_call=4096, transport=transport, **updates
    )


def test_wire_identity_capabilities_and_trust_boundaries():
    captured = []
    raw = json.dumps(fixture()).encode()

    def transport(request, timeout):
        captured.append((request, timeout))
        return raw

    instance = gateway(transport)
    assert public.QwenModelGateway is model.QwenModelGateway
    assert captured == []
    identity = instance.identity
    identity.model_revision = "mutated"
    assert instance.identity.model_dump() == {"model_name": MODEL, "model_revision": REVISION}
    blocks = call().content_blocks
    blocks += [
        blocks[0].model_copy(update={"content": "second system"}),
        blocks[1].model_copy(update={"kind": "repair", "content": "repair JSON"}),
    ]
    result = instance.invoke(call(content_blocks=blocks, max_output_tokens=32768))
    request, timeout = captured[0]
    data = json.loads(request.data)
    assert request.full_url == URL
    assert request.get_header("Authorization") == "Bearer offline-secret"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_method() == "POST" and timeout == 30
    assert data["model"] == MODEL and data["max_tokens"] == 32768
    assert data["enable_thinking"] is data["enable_search"] is data["stream"] is False
    assert data["response_format"] == {"type": "json_object"}
    assert [item["role"] for item in data["messages"]] == ["system", "user", "user"]
    assert data["messages"][0]["content"].startswith(blocks[0].content + "\n\nsecond system")
    assert "JSON" in data["messages"][0]["content"]
    assert [item["content"] for item in data["messages"][1:]] == [blocks[1].content, "repair JSON"]
    assert "offline-secret" not in request.data.decode() + repr(instance)
    assert result.payload == '{"signals":[]}'
    assert result.model_name == MODEL and result.model_revision == REVISION
    assert result.response_fingerprint == hashlib.sha256(raw).hexdigest()
    assert result.usage.model_dump() == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert instance.capabilities.supports_text and instance.capabilities.supports_structured_output
    assert not instance.capabilities.supports_images
    assert instance.capabilities.max_input_tokens_per_call == 4096 and len(captured) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"model_name": "other"},
        {"model_revision": None},
        {"model_revision": "other"},
        {"scenario_id": "fake"},
        {"max_output_tokens": 0},
        {"max_output_tokens": 32769},
    ],
)
def test_invalid_request_zero_transport(updates):
    with pytest.raises(ModelGatewayError) as error:
        gateway(lambda *_: pytest.fail("transport must not run")).invoke(call(**updates))
    assert (error.value.code, error.value.retryable) == ("invalid_configuration", False)


@pytest.mark.parametrize(
    "key,limit",
    [
        ("", 1),
        (" ", 1),
        (None, 1),
        (42, 1),
        ("中文", 1),
        ("secret\r\nheader", 1),
        ("ok", 0),
        ("ok", -1),
        ("ok", True),
        ("ok", 1.5),
        ("ok", "1"),
    ],
)
def test_constructor_rejects_invalid_configuration(key, limit):
    assert hasattr(model, "QwenModelGateway"), "Qwen gateway is missing"
    with pytest.raises(ModelGatewayError) as error:
        model.QwenModelGateway(api_key=key, max_input_tokens_per_call=limit)
    assert str(error.value) == "invalid_configuration" and not error.value.retryable
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "name,revision",
    [(MODEL, REVISION), ("qwen-plus", None), ("qwen-plus-2026-01-01", None), ("other-model", None)],
)
def test_provider_identity_comes_only_from_model(name, revision):
    body = fixture()
    body.update(model=name, model_revision=REVISION, revision=REVISION, system_fingerprint="fp_1")
    result = gateway(lambda *_: json.dumps(body).encode()).invoke(call())
    assert result.model_name == name and result.model_revision == revision
    assert result.provider_system_fingerprint == "fp_1"


@pytest.mark.parametrize("value", [None, "", " ", "bad name", 42, False, {}, "x" * 257])
def test_unverifiable_model_is_distinct_nonretryable_error(value):
    body = fixture()
    body["model"] = value
    with pytest.raises(ModelGatewayError) as error:
        gateway(lambda *_: json.dumps(body).encode()).invoke(call())
    assert (error.value.code, error.value.retryable) == ("model_identity_unverifiable", False)
    assert error.value.__suppress_context__


def test_missing_model_is_unverifiable():
    body = fixture()
    del body["model"]
    with pytest.raises(ModelGatewayError, match="model_identity_unverifiable"):
        gateway(lambda *_: json.dumps(body).encode()).invoke(call())


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication_failed", False),
        (403, "authentication_failed", False),
        (429, "rate_limited", True),
        (408, "timeout", True),
        (504, "timeout", True),
        (500, "transport_error", True),
        (599, "transport_error", True),
        (400, "invalid_configuration", False),
        (404, "invalid_configuration", False),
        (302, "invalid_configuration", False),
    ],
)
def test_http_errors_sanitized_single_attempt(status, code, retryable):
    calls = []

    def transport(request, timeout):
        calls.append(request)
        raise HTTPError(URL, status, "offline-secret prompt", {}, None)

    with pytest.raises(ModelGatewayError) as error:
        gateway(transport).invoke(call())
    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert str(error.value) == code and error.value.__suppress_context__ and len(calls) == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        (TimeoutError("secret"), "timeout"),
        (URLError(TimeoutError("secret")), "timeout"),
        (URLError("secret"), "transport_error"),
        (OSError("secret"), "transport_error"),
        (HTTPException("secret"), "transport_error"),
    ],
)
def test_transport_errors_sanitized(failure, code):
    attempts = []

    def transport(*args):
        attempts.append(args)
        raise failure

    with pytest.raises(ModelGatewayError) as error:
        gateway(transport).invoke(call())
    assert (error.value.code, error.value.retryable) == (code, True)
    assert str(error.value) == code and error.value.__suppress_context__ and len(attempts) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("choices", []),
        ("choices", [{}, {}]),
        ("choices", {}),
        ("choices", [None]),
        ("choices", [{}]),
        ("choices", [{"message": {"role": "user", "content": "x"}, "finish_reason": "stop"}]),
        ("choices", [{"message": {"role": "assistant", "content": {}}, "finish_reason": "stop"}]),
        ("choices", [{"message": {"role": "assistant", "content": "x"}, "finish_reason": None}]),
        ("system_fingerprint", 42),
        ("system_fingerprint", "bad fingerprint"),
        ("usage", []),
        ("usage", {}),
        ("usage", {"prompt_tokens": True, "completion_tokens": 5, "total_tokens": 6}),
        ("usage", {"prompt_tokens": -1, "completion_tokens": 5, "total_tokens": 4}),
        ("usage", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 14}),
    ],
)
def test_invalid_response_fields_fail_closed(field, value):
    body = fixture()
    body[field] = value
    with pytest.raises(ModelGatewayError) as error:
        gateway(lambda *_: json.dumps(body).encode()).invoke(call())
    assert (error.value.code, error.value.retryable) == ("invalid_configuration", False)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"[]",
        b"null",
        b"42",
        b"\xff",
        b'{"ignored":' + b"[" * 5000 + b"0" + b"]" * 5000 + b"}",
    ],
)
def test_invalid_wire_body(raw):
    with pytest.raises(ModelGatewayError) as error:
        gateway(lambda *_: raw).invoke(call())
    assert (error.value.code, error.value.retryable) == ("invalid_configuration", False)


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
def test_normalization_and_optional_fields(reason, expected, optional):
    body = fixture()
    body["choices"][0]["finish_reason"] = reason
    for field in ("usage", "system_fingerprint"):
        if optional == "missing":
            del body[field]
        else:
            body[field] = None
    result = gateway(lambda *_: json.dumps(body).encode()).invoke(call())
    assert result.finish_reason == expected
    assert result.usage is None and result.provider_system_fingerprint is None


def test_invalid_unicode_request_zero_transport():
    blocks = call().content_blocks
    blocks[0] = blocks[0].model_copy(update={"content": "secret\ud800"})
    with pytest.raises(ModelGatewayError, match="invalid_configuration"):
        gateway(lambda *_: pytest.fail("transport must not run")).invoke(
            call(content_blocks=blocks)
        )


@pytest.mark.parametrize(
    "failure", [None, URLError(TimeoutError("secret")), HTTPError(URL, 302, "secret", {}, None)]
)
def test_default_opener_redirect_timeout_and_sanitization(monkeypatch, failure):
    gateway()  # Missing public gateway produces assertion RED before module import.
    from requirementseeker_agent.model import qwen

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
            if failure is not None:
                raise failure
            return Response()

    def build_opener(handler):
        assert isinstance(handler, HTTPRedirectHandler)
        assert (
            handler.redirect_request(
                Request(URL), None, 302, "redirect", {}, "https://evil.invalid"
            )
            is None
        )
        return Opener()

    monkeypatch.setattr(qwen, "build_opener", build_opener)
    if failure is None:
        gateway().invoke(call(timeout_seconds=7))
    else:
        with pytest.raises(ModelGatewayError) as error:
            gateway().invoke(call(timeout_seconds=7))
        assert (error.value.code, error.value.retryable) == (
            ("timeout", True)
            if isinstance(failure, URLError) and not isinstance(failure, HTTPError)
            else ("invalid_configuration", False)
        )
        assert error.value.__suppress_context__
    assert len(captured) == 1 and captured[0][1] == 7
