"""Synchronous single-call model gateway types with no secret-bearing fields."""

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
    supports_text: StrictBool
    supports_structured_output: StrictBool
    supports_images: StrictBool


class ControlledContentBlock(Contract):
    block_id: Identifier
    kind: Literal["system", "untrusted_data", "repair"]
    content: Text


class ModelCallRequest(Contract):
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
    # The pipeline validates this value against its stage schema and trusted references.
    payload: object
    model_name: Identifier
    model_revision: Identifier | None
    finish_reason: Literal["stop", "length", "content_filter", "unknown"]
    usage: TokenUsage | None
    response_fingerprint: Hash


class ModelGatewayError(RuntimeError):
    def __init__(self, code: GatewayErrorCode, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class ModelGateway(Protocol):
    @property
    def capabilities(self) -> ModelCapabilities: ...

    def invoke(self, request: ModelCallRequest) -> ModelCallResponse: ...
