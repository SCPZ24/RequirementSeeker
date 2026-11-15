"""本地帧替身仅返回上下文和评论引用，不提供可信证据原文。"""

from collections.abc import Callable

from ..contracts.analysis import ConsensusDecision, ContextDecision, NeedCluster
from ..contracts.common import Contract, Identifier
from ..contracts.requests import AnalysisRequest, MediaContext
from ..rules import evaluate_consensus


class FrameReview(Contract):
    cluster_id: Identifier
    frame_id: Identifier
    supported_comment_ids: list[Identifier]


FrameReviewer = Callable[[AnalysisRequest, NeedCluster], FrameReview | None]


def valid_context_refs(request: AnalysisRequest, context: ContextDecision) -> bool:
    # 文本判断没有媒体输入。media_required 可指定要读取的已提供帧，但不能发明引用。
    refs = set(context.context_refs)
    return not refs or (
        context.status == "media_required"
        and len(refs) == len(context.context_refs)
        and refs.issubset(f.context_id for f in request.media_context if f.kind == "frame")
    )


def eligible_frames(request: AnalysisRequest, context: ContextDecision) -> list[MediaContext]:
    frames = [item for item in request.media_context if item.kind == "frame"]
    if context.context_refs:
        allowed = set(context.context_refs)
        frames = [item for item in frames if item.context_id in allowed]
    return frames


def recheck(
    request: AnalysisRequest, cluster: NeedCluster, context: ContextDecision, review: FrameReview
) -> ConsensusDecision:
    members = review.supported_comment_ids
    if (
        not request.model.supports_images
        or context.status != "media_required"
        or not valid_context_refs(request, context)
        or review.cluster_id != context.cluster_id
        or context.cluster_id != cluster.cluster_id
        or review.frame_id not in {f.context_id for f in eligible_frames(request, context)}
        or len(set(members)) != len(members)
        or not set(members).issubset(cluster.comment_ids)
    ):
        raise ValueError("invalid_frame_review")
    if not members:
        return ConsensusDecision(
            cluster_id=cluster.cluster_id,
            passed=False,
            reason_code="insufficient_independent_authors",
            evidence=[],
        )
    # 语义替身只选择仍支持原需求的成员；作者、原文与三人门槛取自可信输入。
    return evaluate_consensus(request, cluster.model_copy(update={"comment_ids": members}))
