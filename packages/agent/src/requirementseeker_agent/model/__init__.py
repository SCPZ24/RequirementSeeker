"""Model gateway port and deterministic offline adapter."""

from .fake import ScenarioModelGateway
from .types import (
    ControlledContentBlock,
    FrozenModelIdentity,
    GatewayErrorCode,
    GatewayStage,
    ModelCallRequest,
    ModelCallResponse,
    ModelCapabilities,
    ModelGateway,
    ModelGatewayError,
    ModelRuntimeIdentity,
    VersionedModelGateway,
)

__all__ = [
    "ControlledContentBlock",
    "FrozenModelIdentity",
    "GatewayErrorCode",
    "GatewayStage",
    "ModelCallRequest",
    "ModelCallResponse",
    "ModelCapabilities",
    "ModelGateway",
    "ModelGatewayError",
    "ModelRuntimeIdentity",
    "ScenarioModelGateway",
    "VersionedModelGateway",
]
