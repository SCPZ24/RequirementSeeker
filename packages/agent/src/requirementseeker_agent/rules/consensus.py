"""Verify explicit clusters against trusted comments; semantic clustering belongs to M2."""

from ..contracts.analysis import ConsensusDecision, Evidence, NeedCluster
from ..contracts.requests import AnalysisRequest, Comment

RULES_VERSION = "m1.0"


def _failed(cluster: NeedCluster, reason_code: str) -> ConsensusDecision:
    return ConsensusDecision(
        cluster_id=cluster.cluster_id,
        passed=False,
        reason_code=reason_code,
        evidence=[],
    )


def _evidence(comment: Comment) -> Evidence:
    # Only eligible comments reach this helper; keep the guard as an internal trust boundary.
    if comment.author_id is None:
        raise ValueError("eligible_evidence_requires_author")
    return Evidence(
        platform=comment.platform,
        video_id=comment.video_id,
        comment_id=comment.comment_id,
        author_id=comment.author_id,
        text=comment.text,
    )


def evaluate_consensus(request: AnalysisRequest, cluster: NeedCluster) -> ConsensusDecision:
    """Return a reproducible decision without trusting generated evidence payloads."""
    comments = {comment.comment_id: comment for comment in request.comments}
    if any(comment_id not in comments for comment_id in cluster.comment_ids):
        return _failed(cluster, "cluster_references_unknown_comment")
    if request.video.author_id is None:
        return _failed(cluster, "video_author_unknown")

    representatives: dict[str, Comment] = {}
    ordered = sorted(
        (comments[comment_id] for comment_id in cluster.comment_ids),
        key=lambda item: (item.first_collected_at, item.comment_id),
    )
    for comment in ordered:
        if comment.author_id is None or comment.author_id == request.video.author_id:
            continue
        representatives.setdefault(comment.author_id, comment)

    if len(representatives) < 3:
        return _failed(cluster, "insufficient_independent_authors")
    return ConsensusDecision(
        cluster_id=cluster.cluster_id,
        passed=True,
        reason_code="independent_author_threshold_met",
        evidence=[_evidence(comment) for comment in representatives.values()],
    )
