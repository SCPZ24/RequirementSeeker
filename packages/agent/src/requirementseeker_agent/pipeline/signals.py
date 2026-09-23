"""严格校验模型候选载荷，并从当前批次重建可信需求信号。"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import StrictBool, ValidationError, model_validator

from ..contracts.analysis import ModelInvocationAudit, NeedSignal
from ..contracts.common import Contract, Identifier, Text
from ..contracts.requests import AnalysisRequest, Comment
from ..model.types import ControlledContentBlock, ModelCallRequest, ModelGateway
from ..runtime.budget import BudgetLedger
from .batching import CommentBatch, estimate_text_tokens
from .invocation import (
    CancellationProbe,
    InvocationBudgetExceeded,
    InvocationCancelled,
    InvocationFailure,
    invoke_model,
)

SIGNAL_PROMPT_VERSION = "signal-v1"
_PROMPT_PATH = Path(__file__).parents[1] / "prompts" / "signal-v1.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text("utf-8").strip()


class ProposedSignal(Contract):
    """模型可提出的字段；signal_id 仅用于兼容，绝不作为可信 ID。"""

    signal_id: Identifier | None = None
    comment_id: Identifier
    kind: Literal["pain", "need", "alternative", "product_defect"]
    summary: Text
    disputed: StrictBool = False


class SignalPayload(Contract):
    """信号阶段唯一允许的顶层载荷。"""

    signals: list[ProposedSignal]

    @model_validator(mode="after")
    def unique_comments(self) -> Self:
        ids = [signal.comment_id for signal in self.signals]
        if len(ids) != len(set(ids)):
            raise ValueError("signal_comment_ids_must_be_unique")
        return self


@dataclass(frozen=True, slots=True)
class SignalExtractionResult:
    """已从可信请求重建的信号，以及所有真实模型调用审计。"""

    signals: tuple[NeedSignal, ...]
    audits: tuple[ModelInvocationAudit, ...]


class InvalidModelOutput(RuntimeError):
    """结构或引用修复仍失败，禁止暴露任何部分信号。"""

    def __init__(self, audits: tuple[ModelInvocationAudit, ...]) -> None:
        super().__init__("model_output_invalid")
        self.audits = audits


def normalize_summary(summary: str) -> str:
    """只统一 Unicode 与空白，不翻译或改写模型摘要。"""

    return " ".join(unicodedata.normalize("NFC", summary).split())


def stable_signal_id(comment_id: str, kind: str, summary: str, prompt_version: str) -> str:
    """由可信评论引用和版本化语义内容生成稳定信号 ID。"""

    value = "\x1f".join((comment_id, kind, normalize_summary(summary), prompt_version))
    return f"sig_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def signal_fixed_input_tokens(request: AnalysisRequest) -> int:
    """估算每个信号批次固定携带的 Prompt、视频与 Schema 开销。"""

    source = {
        "video": {
            "platform": request.video.platform,
            "video_id": request.video.video_id,
            "title": request.video.title,
            "description": request.video.description,
        },
        "comments": [],
    }
    untrusted = (
        "UNTRUSTED_VIDEO_DATA\n"
        + json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\nEND_UNTRUSTED_VIDEO_DATA"
    )
    return estimate_text_tokens(f"{_SYSTEM_PROMPT}\n{untrusted}") + 32


def _batch_comments(request: AnalysisRequest, batch: CommentBatch) -> tuple[Comment, ...]:
    """验证批次边界，并按规划顺序取回可信评论。"""

    if (batch.platform, batch.video_id) != (request.video.platform, request.video.video_id):
        raise ValueError("signal_batch_must_match_request_video")
    if len(batch.comment_ids) != len(set(batch.comment_ids)):
        raise ValueError("signal_batch_comment_ids_must_be_unique")
    comments = {comment.comment_id: comment for comment in request.comments}
    if any(comment_id not in comments for comment_id in batch.comment_ids):
        raise ValueError("signal_batch_comment_not_in_request")
    return tuple(comments[comment_id] for comment_id in batch.comment_ids)


def _call_request(
    request: AnalysisRequest,
    batch: CommentBatch,
    comments: tuple[Comment, ...],
    *,
    max_output_tokens: int,
    timeout_seconds: int,
    scenario_id: str | None,
) -> ModelCallRequest:
    # 只给模型语义判断所需的最小视频与评论内容，不暴露作者统计或信任结论。
    source = {
        "video": {
            "platform": request.video.platform,
            "video_id": request.video.video_id,
            "title": request.video.title,
            "description": request.video.description,
        },
        "comments": [
            {"comment_id": comment.comment_id, "text": comment.text} for comment in comments
        ],
    }
    untrusted = (
        "UNTRUSTED_VIDEO_DATA\n"
        + json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\nEND_UNTRUSTED_VIDEO_DATA"
    )
    return ModelCallRequest(
        invocation_id=f"signals-{batch.batch_id}",
        stage="signals",
        model_config_ref=request.model.config_ref,
        model_name=request.model.model_name,
        model_revision=request.model.revision,
        prompt_version=SIGNAL_PROMPT_VERSION,
        content_blocks=[
            ControlledContentBlock(
                block_id="signal-system-v1",
                kind="system",
                content=_SYSTEM_PROMPT,
            ),
            ControlledContentBlock(
                block_id=f"signal-data-{batch.batch_id}",
                kind="untrusted_data",
                content=untrusted,
            ),
        ],
        expected_schema_name="need-signals",
        expected_schema_version="1.0",
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        attempt=1,
        scenario_id=scenario_id,
    )


def _validate_payload(payload: object, allowed_ids: frozenset[str]) -> tuple[NeedSignal, ...]:
    parsed = SignalPayload.model_validate(payload)
    if any(signal.comment_id not in allowed_ids for signal in parsed.signals):
        raise ValueError("signal_comment_not_in_current_batch")

    signals: list[NeedSignal] = []
    for proposed in parsed.signals:
        # 争议信号保守不准入；模型生成的 signal_id 也在这里被明确忽略。
        if proposed.disputed:
            continue
        summary = normalize_summary(proposed.summary)
        signals.append(
            NeedSignal(
                signal_id=stable_signal_id(
                    proposed.comment_id,
                    proposed.kind,
                    summary,
                    SIGNAL_PROMPT_VERSION,
                ),
                comment_id=proposed.comment_id,
                kind=proposed.kind,
                summary=summary,
            )
        )
    return tuple(signals)


def _prepend_audits(
    error: InvocationFailure | InvocationCancelled | InvocationBudgetExceeded,
    prior: tuple[ModelInvocationAudit, ...],
) -> InvocationFailure | InvocationCancelled | InvocationBudgetExceeded:
    combined = prior + error.audits
    if isinstance(error, InvocationCancelled):
        return InvocationCancelled(combined)
    if isinstance(error, InvocationBudgetExceeded):
        return InvocationBudgetExceeded(error.resource, combined)
    return InvocationFailure(error.code, retryable=error.retryable, audits=combined)


def extract_signals(
    request: AnalysisRequest,
    batch: CommentBatch,
    gateway: ModelGateway,
    ledger: BudgetLedger,
    *,
    max_output_tokens: int,
    cancellation_probe: CancellationProbe = lambda: False,
    timeout_seconds: int = 30,
    scenario_id: str | None = None,
) -> SignalExtractionResult:
    """调用模型，最多修复一次输出，并只返回当前批次内的可信信号。"""

    comments = _batch_comments(request, batch)
    call = _call_request(
        request,
        batch,
        comments,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        scenario_id=scenario_id,
    )
    allowed_ids = frozenset(batch.comment_ids)
    audits: tuple[ModelInvocationAudit, ...] = ()
    next_attempt = 1
    input_tokens = batch.estimated_input_tokens
    repairs = min(request.retry_policy.max_output_repairs, 1)

    for repair_index in range(repairs + 1):
        if repair_index:
            # 不回显可能带注入内容的旧响应，只说明必须重新满足既定 Schema 与引用边界。
            repair = ControlledContentBlock(
                block_id=f"signal-repair-{repair_index}",
                kind="repair",
                content=(
                    "The previous response was invalid. Return the schema again using only "
                    "comment_id values listed in UNTRUSTED_VIDEO_DATA."
                ),
            )
            call = call.model_copy(update={"content_blocks": [*call.content_blocks, repair]})
            input_tokens += estimate_text_tokens(repair.content)

        if input_tokens > gateway.capabilities.max_input_tokens_per_call:
            raise InvocationBudgetExceeded("input_tokens", audits)

        try:
            invocation = invoke_model(
                request,
                call,
                gateway,
                ledger,
                input_tokens=input_tokens,
                start_attempt=next_attempt,
                cancellation_probe=cancellation_probe,
            )
        except (InvocationFailure, InvocationCancelled, InvocationBudgetExceeded) as error:
            raise _prepend_audits(error, audits) from error

        audits += invocation.audits
        next_attempt = invocation.next_attempt
        try:
            if invocation.response.finish_reason != "stop":
                raise ValueError("signal_response_did_not_finish")
            signals = _validate_payload(invocation.response.payload, allowed_ids)
        except (ValidationError, ValueError):
            if repair_index == repairs:
                raise InvalidModelOutput(audits) from None
            continue
        return SignalExtractionResult(signals, audits)

    raise AssertionError("unreachable")
