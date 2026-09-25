"""用于离线控制流与边界测试的确定性场景模型。"""

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

# 每个场景同时固定错误码和可重试性，避免测试依赖随机网络行为。
_ERRORS: dict[str, tuple[GatewayErrorCode, bool]] = {
    "timeout": ("timeout", True),
    "rate_limited": ("rate_limited", True),
    "transport_error": ("transport_error", True),
    "authentication_failed": ("authentication_failed", False),
    "capability_unsupported": ("capability_unsupported", False),
    "cancelled": ("cancelled", False),
}

# 这里故意保留多种非法载荷，用于验证真实模型接入前后的防线一致。
_PAYLOADS: dict[str, object] = {
    "valid_signals": {"signals": [{"comment_id": "c1", "kind": "need", "summary": "批量导出"}]},
    "usage_missing": {"signals": [{"comment_id": "c1", "kind": "need", "summary": "批量导出"}]},
    "no_signal": {"signals": []},
    "disputed_signal": {
        "signals": [
            {"comment_id": "c1", "kind": "need", "summary": "可能需要批量导出", "disputed": True}
        ]
    },
    "valid_clusters": {"clusters": [{"comment_ids": ["c1", "c2", "c3"], "summary": "批量导出"}]},
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
    """对规范化请求取摘要，让相同输入产生稳定的审计标识。"""

    payload = json.dumps(
        request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class ScenarioModelGateway:
    """按 scenario_id 返回固定结果，同时实现生产适配器使用的同一端口。"""

    def __init__(self, scenario_id: str | None = None) -> None:
        self._scenario_id = scenario_id

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_text=True,
            supports_structured_output=True,
            supports_images=False,
            max_input_tokens_per_call=4096,
        )

    def invoke(self, request: ModelCallRequest) -> ModelCallResponse:
        scenario = request.scenario_id or self._scenario_id
        if scenario is None:
            raise ModelGatewayError("invalid_configuration", retryable=False)
        if scenario in _ERRORS:
            code, retryable = _ERRORS[scenario]
            raise ModelGatewayError(code, retryable=retryable)
        payload: object
        if scenario in {"valid_pipeline", "repair_pipeline"}:
            payload = (
                {
                    "signals": [
                        {"comment_id": comment_id, "kind": "need", "summary": "批量导出"}
                        for comment_id in ("c1", "c2", "c3")
                    ]
                }
                if request.stage == "signals"
                else _PAYLOADS["valid_clusters"]
            )
            if (
                scenario == "repair_pipeline"
                and request.stage == "signals"
                and request.attempt == 1
            ):
                payload = _PAYLOADS["missing_fields"]
        elif scenario == "repair_success":
            # 首次返回结构错误，第二次才成功，用来覆盖“最多一次修复”的控制流。
            payload = (
                _PAYLOADS["missing_fields"] if request.attempt == 1 else _PAYLOADS["valid_signals"]
            )
        elif scenario in _PAYLOADS:
            payload = _PAYLOADS[scenario]
        else:
            raise ModelGatewayError("invalid_configuration", retryable=False)

        # usage_missing 模拟供应商没有返回用量；预算账本必须按预留上限结算。
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
