"""在统一预算、重试、取消与审计规则下执行一次逻辑模型调用。"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..contracts.analysis import ModelInvocationAudit
from ..contracts.requests import AnalysisRequest
from ..model.types import (
    GatewayErrorCode,
    ModelCallRequest,
    ModelCallResponse,
    ModelGateway,
    ModelGatewayError,
)
from ..runtime.budget import BudgetLedger

CancellationProbe = Callable[[], bool]
_TRANSPORT_ERRORS: frozenset[GatewayErrorCode] = frozenset(
    {"timeout", "rate_limited", "transport_error"}
)


@dataclass(frozen=True, slots=True)
class InvocationResult:
    """一次逻辑调用的可信传输结果及全部实际尝试。"""

    response: ModelCallResponse
    audits: tuple[ModelInvocationAudit, ...]
    next_attempt: int


class InvocationFailure(RuntimeError):
    """模型调用已失败，并携带失败前产生的完整审计。"""

    def __init__(
        self,
        code: GatewayErrorCode,
        *,
        retryable: bool,
        audits: tuple[ModelInvocationAudit, ...],
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.audits = audits


class InvocationCancelled(RuntimeError):
    """调用前或调用响应后检测到取消。"""

    def __init__(self, audits: tuple[ModelInvocationAudit, ...]) -> None:
        super().__init__("cancelled")
        self.audits = audits


def _never_cancelled() -> bool:
    return False


def _attempt_call(base: ModelCallRequest, attempt: int) -> ModelCallRequest:
    """为每次真实调用生成唯一 ID，保留其余决策输入。"""

    return base.model_copy(
        update={
            "invocation_id": f"{base.invocation_id}-attempt-{attempt}",
            "attempt": attempt,
        }
    )


def _input_hash(call: ModelCallRequest) -> str:
    canonical = json.dumps(
        call.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _audit(
    request: AnalysisRequest,
    call: ModelCallRequest,
    *,
    status: Literal["success", "error", "cancelled"],
    response: ModelCallResponse | None = None,
    error_code: GatewayErrorCode | None = None,
) -> ModelInvocationAudit:
    """只从已知调用与传输结果创建线协议审计。"""

    return ModelInvocationAudit(
        invocation_id=call.invocation_id,
        model_config_ref=call.model_config_ref,
        model_name=response.model_name if response is not None else call.model_name,
        versions=request.versions,
        input_hash=_input_hash(call),
        step=call.stage,
        attempt=call.attempt,
        status=status,
        usage=None if response is None else response.usage,
        error_code=error_code,
    )


def invoke_model(
    request: AnalysisRequest,
    call: ModelCallRequest,
    gateway: ModelGateway,
    ledger: BudgetLedger,
    *,
    input_tokens: int,
    start_attempt: int = 1,
    cancellation_probe: CancellationProbe = _never_cancelled,
) -> InvocationResult:
    """执行调用并仅重试明确的传输错误；每次尝试独立预留预算。"""

    capabilities = gateway.capabilities
    if (
        not request.model.supports_text
        or not capabilities.supports_text
        or not capabilities.supports_structured_output
    ):
        raise InvocationFailure("capability_unsupported", retryable=False, audits=())

    audits: list[ModelInvocationAudit] = []
    attempt = start_attempt
    transport_retries = 0
    while True:
        # 请求字段是启动快照，探针用于同一进程内在调用边界观察后续取消。
        if request.cancellation_requested or cancellation_probe():
            raise InvocationCancelled(tuple(audits))

        attempted_call = _attempt_call(call, attempt)
        reservation = ledger.reserve(
            input_tokens=input_tokens,
            output_tokens=attempted_call.max_output_tokens,
        )
        try:
            response = gateway.invoke(attempted_call)
        except ModelGatewayError as error:
            # 供应商错误没有可信 usage，按最坏预留量结算这次实际调用。
            ledger.settle(reservation, None)
            if error.code == "cancelled":
                audits.append(_audit(request, attempted_call, status="cancelled"))
                raise InvocationCancelled(tuple(audits)) from error

            audits.append(
                _audit(request, attempted_call, status="error", error_code=error.code)
            )
            may_retry = error.retryable and error.code in _TRANSPORT_ERRORS
            if may_retry and transport_retries < request.retry_policy.max_transport_retries:
                transport_retries += 1
                attempt += 1
                continue
            raise InvocationFailure(
                error.code,
                retryable=may_retry,
                audits=tuple(audits),
            ) from error

        ledger.settle(reservation, response.usage)
        if request.cancellation_requested or cancellation_probe():
            audits.append(_audit(request, attempted_call, status="cancelled", response=response))
            raise InvocationCancelled(tuple(audits))

        audits.append(_audit(request, attempted_call, status="success", response=response))
        return InvocationResult(response, tuple(audits), attempt + 1)
