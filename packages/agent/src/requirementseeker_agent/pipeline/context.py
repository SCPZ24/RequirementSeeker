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


def eligible_frames(request: AnalysisRequest, context: ContextDecision) -> list[MediaContext]:
    frames = [item for item in request.media_context if item.kind == "frame"]
    if context.context_refs:
        allowed = set(context.context_refs)
        frames = [item for item in frames if item.context_id in allowed]
        if allowed != {item.context_id for item in frames}:
            return []
    return frames


def recheck(
    request: AnalysisRequest, cluster: NeedCluster, context: ContextDecision, review: FrameReview
) -> ConsensusDecision:
    members = review.supported_comment_ids
    if (
        not request.model.supports_images
        or context.status != "media_required"
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
