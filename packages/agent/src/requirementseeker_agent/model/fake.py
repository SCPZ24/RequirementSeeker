"""Deterministic model scenarios for offline control-flow and boundary tests."""

import hashlib
import json
from copy import deepcopy

from ..contracts.analysis import TokenUsage
from .types import (
    GatewayErrorCode,
    ModelCallRequest,
    ModelCallResponse,
    ModelCapabilities,
    ModelGatewayError,
)

_ERRORS: dict[str, tuple[GatewayErrorCode, bool]] = {
    "timeout": ("timeout", True),
    "rate_limited": ("rate_limited", True),
    "transport_error": ("transport_error", True),
    "authentication_failed": ("authentication_failed", False),
    "capability_unsupported": ("capability_unsupported", False),
    "cancelled": ("cancelled", False),
}

_PAYLOADS: dict[str, object] = {
    "valid_signals": {
        "signals": [{"comment_id": "c1", "kind": "need", "summary": "批量导出"}]
    },
    "usage_missing": {
        "signals": [{"comment_id": "c1", "kind": "need", "summary": "批量导出"}]
    },
    "no_signal": {"signals": []},
    "disputed_signal": {
        "signals": [
            {"comment_id": "c1", "kind": "need", "summary": "可能需要批量导出", "disputed": True}
        ]
    },
    "valid_clusters": {
        "clusters": [{"comment_ids": ["c1", "c2", "c3"], "summary": "批量导出"}]
    },
    "bad_json": "{",
    "missing_fields": {"signals": [{"comment_id": "c1"}]},
    "extra_fields": {"signals": [], "unexpected": True},
    "wrong_type": {"signals": "not-a-list"},
    "oversized_output": {"signals": [{"summary": "x" * 50000}]},
    "unknown_comment": {
        "signals": [{"comment_id": "missing", "kind": "need", "summary": "批量导出"}]
    },
    "cross_video_comment": {
        "signals": [{"comment_id": "foreign-video-c1", "kind": "need", "summary": "批量导出"}]
    },
    "altered_quote": {
        "signals": [
            {"comment_id": "c1", "kind": "need", "summary": "批量导出", "quote": "被改写的原文"}
        ]
    },
    "duplicate_cluster_member": {
        "clusters": [{"comment_ids": ["c1", "c1"], "summary": "批量导出"}]
    },
    "repair_failure": {"signals": [{"comment_id": "missing"}]},
}


def _fingerprint(request: ModelCallRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class ScenarioModelGateway:
    """Return explicit fixture outcomes while implementing the production gateway port."""

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_text=True,
            supports_structured_output=True,
            supports_images=False,
            max_input_tokens_per_call=4096,
        )

    def invoke(self, request: ModelCallRequest) -> ModelCallResponse:
        scenario = request.scenario_id
        if scenario is None:
            raise ModelGatewayError("invalid_configuration", retryable=False)
        if scenario in _ERRORS:
            code, retryable = _ERRORS[scenario]
            raise ModelGatewayError(code, retryable=retryable)
        if scenario == "repair_success":
            payload: object = (
                _PAYLOADS["missing_fields"] if request.attempt == 1 else _PAYLOADS["valid_signals"]
            )
        elif scenario in _PAYLOADS:
            payload = _PAYLOADS[scenario]
        else:
            raise ModelGatewayError("invalid_configuration", retryable=False)

        usage = None
        if scenario != "usage_missing":
            usage = TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120)
        return ModelCallResponse(
            payload=deepcopy(payload),
            model_name="scenario-model",
            model_revision="m2-fixture-1",
            finish_reason="stop",
            usage=usage,
            response_fingerprint=_fingerprint(request),
        )
