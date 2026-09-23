"""不携带密钥的同步单次模型调用端口与数据类型。"""

from typing import Literal, Protocol

from pydantic import Field, StrictBool

from ..contracts.analysis import TokenUsage
from ..contracts.common import (
    Contract,
    Hash,
    Identifier,
    NonNegativeInt,
    PositiveInt,
    Text,
)

GatewayStage = Literal["signals", "cluster"]
GatewayErrorCode = Literal[
    "timeout",
    "rate_limited",
    "transport_error",
    "capability_unsupported",
    "authentication_failed",
    "cancelled",
    "invalid_configuration",
]


class ModelCapabilities(Contract):
    """适配器必须显式报告的模型能力与单次输入限制。"""

    supports_text: StrictBool
    supports_structured_output: StrictBool
    supports_images: StrictBool
    max_input_tokens_per_call: PositiveInt


class ControlledContentBlock(Contract):
    """标记提示词、外部不可信数据和修复指令之间的信任边界。"""

    block_id: Identifier
    kind: Literal["system", "untrusted_data", "repair"]
    content: Text


class ModelCallRequest(Contract):
    """一次可审计模型调用所需的全部非敏感参数。"""

    invocation_id: Identifier
    stage: GatewayStage
    model_config_ref: Identifier
    model_name: Identifier
    model_revision: Identifier | None
    prompt_version: Identifier
    content_blocks: list[ControlledContentBlock] = Field(min_length=1)
    expected_schema_name: Identifier
    expected_schema_version: Identifier
    max_output_tokens: NonNegativeInt
    timeout_seconds: PositiveInt
    attempt: PositiveInt
    scenario_id: Identifier | None = None


class ModelCallResponse(Contract):
    """适配器的原始响应；返回并不代表载荷已经可信。"""

    # payload 跨越模型信任边界，管线必须用阶段 Schema 和可信引用重新验证。
    payload: object
    model_name: Identifier
    model_revision: Identifier | None
    finish_reason: Literal["stop", "length", "content_filter", "unknown"]
    usage: TokenUsage | None
    response_fingerprint: Hash


class ModelGatewayError(RuntimeError):
    """统一适配器错误，并明确上层是否允许重试。"""

    def __init__(self, code: GatewayErrorCode, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class ModelGateway(Protocol):
    """真假模型共同实现的最小同步端口。"""

    @property
    def capabilities(self) -> ModelCapabilities: ...

    def invoke(self, request: ModelCallRequest) -> ModelCallResponse: ...
