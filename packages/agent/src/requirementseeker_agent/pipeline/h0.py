"""单视频、单簇的离线 M3/M4 交接。提交成功只表示内存 sink 已接收意图。"""

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import Field

from ..contracts.analysis import (
    ConsensusDecision,
    ContextDecision,
    Evidence,
    NeedCluster,
    OpportunityCandidate,
    OpportunityMergeDecision,
)
from ..contracts.requests import AnalysisRequest
from ..contracts.results import MergeEvidence, NewCandidate
from ..rules import evaluate_consensus
from .context import FrameReview, FrameReviewer, eligible_frames, recheck, valid_context_refs
from .types import M2AnalysisResult


@dataclass(frozen=True)
class Assessment:
    candidate: OpportunityCandidate
    software_deliverable: bool
    light_operations: bool
    specific_acquisition: bool


class OpportunityJudge(Protocol):
    def assess_context(self, request: AnalysisRequest, cluster: NeedCluster) -> ContextDecision: ...

    def assess_opportunity(
        self, request: AnalysisRequest, cluster: NeedCluster, consensus: ConsensusDecision
    ) -> Assessment: ...


BusinessGate = Literal["software_deliverable", "light_operations", "specific_acquisition"]


@dataclass(frozen=True)
class FlowResult:
    status: Literal[
        "committed", "already_committed", "context_insufficient", "no_opportunity", "incomplete"
    ]
    reason: str
    intent: NewCandidate | MergeEvidence | None = None
    failed_gates: Annotated[tuple[BusinessGate, ...], Field(exclude_if=lambda v: not v)] = ()


@dataclass(frozen=True)
class HistoricalOpportunity:
    opportunity_id: str
    target_user: str
    scenario: str
    problem: str
    software_shape: str
    evidence: tuple[Evidence, ...] = ()
    manual_stage: str = ""
    manual_note: str = ""


HistoryJudge = Callable[
    [OpportunityCandidate, tuple[HistoricalOpportunity, ...]], OpportunityMergeDecision | None
]


def _ordered_evidence(items: list[Evidence]) -> list[Evidence]:
    # 单视频内评论 ID 唯一。排序只消除排列差异，比较保留 Evidence 全部字段。
    return sorted(items, key=lambda item: item.comment_id)


class MemoryIntentStore:
    """演示用的串行、进程内历史与提交替身。"""

    def __init__(
        self,
        *,
        history_complete: bool = True,
        history_revision: str | None = None,
        history: tuple[HistoricalOpportunity, ...] = (),
        history_judge: HistoryJudge | None = None,
    ) -> None:
        self.history_complete = history_complete
        self._history = deepcopy(history)
        self._revision = history_revision
        self._judge = history_judge
        self.intents: dict[str, NewCandidate | MergeEvidence] = {}
        # 重放先按来源定位原回执；追加回执自身的键还绑定目标机会。
        self._receipt_keys: dict[str, str] = {}

    def commit_once(
        self, intent: NewCandidate, *, history_revision: str | None = None
    ) -> FlowResult:
        if not self.history_complete:
            return FlowResult("incomplete", "history_incomplete")
        if (self._history or self._revision is not None) and (
            not self._revision or self._revision != history_revision
        ):
            return FlowResult("incomplete", "history_revision_mismatch")
        targets = {row.opportunity_id for row in self._history}
        if len(targets) != len(self._history):
            return FlowResult("incomplete", "history_target_ambiguous")
        source_key = intent.idempotency_key
        previous = self.intents.get(self._receipt_keys.get(source_key, source_key))
        if previous is not None:
            prior_evidence = (
                previous.merge.evidence
                if isinstance(previous, MergeEvidence)
                else previous.candidate.evidence
            )
            if _ordered_evidence(prior_evidence) != _ordered_evidence(intent.candidate.evidence):
                return FlowResult("incomplete", "evidence_conflict")
            return FlowResult(
                "already_committed", "evidence_replayed", previous.model_copy(deep=True)
            )
        receipt: NewCandidate | MergeEvidence = intent
        if self._history:
            if self._judge is None:
                return FlowResult("incomplete", "history_judge_missing")
            try:
                proposal = self._judge(
                    intent.candidate.model_copy(deep=True), deepcopy(self._history)
                )
                if proposal is not None:
                    if not isinstance(proposal, OpportunityMergeDecision):
                        return FlowResult("incomplete", "invalid_merge_proposal")
                    proposal = OpportunityMergeDecision.model_validate(proposal.model_dump())
            except Exception:
                # 外部判断器失败只能中止此次路由，不能当作“未找到匹配”。
                return FlowResult("incomplete", "history_judge_failed")
            if proposal is not None:
                if (
                    proposal.target_opportunity_id not in targets
                    or proposal.candidate_id != intent.candidate.candidate_id
                    or _ordered_evidence(proposal.evidence)
                    != _ordered_evidence(intent.candidate.evidence)
                ):
                    return FlowResult("incomplete", "untrusted_merge_reference")
                proposal.evidence = deepcopy(intent.candidate.evidence)
                key = hashlib.sha256(
                    json.dumps(
                        ["merge_evidence", proposal.target_opportunity_id, source_key]
                    ).encode()
                ).hexdigest()
                receipt = MergeEvidence(
                    status="merge_evidence", merge=proposal, idempotency_key=key
                )
        self.intents[receipt.idempotency_key] = receipt.model_copy(deep=True)
        self._receipt_keys[source_key] = receipt.idempotency_key
        return FlowResult("committed", "intent_recorded", receipt)


def run_h0(
    request: AnalysisRequest,
    m2: M2AnalysisResult,
    judge: OpportunityJudge,
    store: MemoryIntentStore,
    *,
    frame_reviewer: FrameReviewer | None = None,
) -> FlowResult:
    """只消费一个已完成的合成 M2 簇，使用注入的判断器和内存历史。"""
    if (m2.run_id, m2.platform, m2.video_id) != (
        request.run_id,
        request.video.platform,
        request.video.video_id,
    ):
        return FlowResult("incomplete", "source_mismatch")
    if request.cancellation_requested:
        return FlowResult("incomplete", "cancelled")
    plan = m2.sampling_plan
    if (plan.platform, plan.video_id) != (m2.platform, m2.video_id) or (
        plan.selected_comment_ids != [comment.comment_id for comment in request.comments]
    ):
        return FlowResult("incomplete", "sampling_mismatch")
    if m2.status == "no_signal":
        if m2.completed_steps != ["preprocess", "signals"] or m2.clusters:
            return FlowResult("incomplete", "upstream_steps_mismatch")
        return FlowResult("no_opportunity", "no_signal")
    if m2.status != "completed":
        return FlowResult("incomplete", m2.error_code or m2.status)
    if m2.completed_steps != ["preprocess", "signals", "cluster", "consensus"]:
        return FlowResult("incomplete", "upstream_steps_mismatch")
    if len(m2.clusters) != 1:
        return FlowResult("incomplete", "single_cluster_required")
    cluster = m2.clusters[0]
    members = set(cluster.comment_ids)
    if members != {s.comment_id for s in m2.signals} or not members.issubset(
        m2.sampling_plan.selected_comment_ids
    ):
        return FlowResult("incomplete", "signal_members_mismatch")
    consensus = evaluate_consensus(request, cluster)
    if consensus != m2.decisions[0]:
        return FlowResult("incomplete", "consensus_mismatch")
    if not consensus.passed:
        return FlowResult("no_opportunity", consensus.reason_code)
    try:
        working = request.model_copy(deep=True)
        working.media_context = []
        context = judge.assess_context(working.model_copy(deep=True), cluster.model_copy(deep=True))
        if context.cluster_id != cluster.cluster_id:
            return FlowResult("incomplete", "context_cluster_mismatch")
        if not valid_context_refs(request, context):
            return FlowResult("incomplete", "invalid_context_refs")
        if context.status == "context_sufficient":
            return FlowResult("incomplete", "unsupported_media_context")
        if context.status == "media_required":
            if not request.model.supports_images:
                return FlowResult("context_insufficient", "image_capability_missing")
            frames = eligible_frames(request, context)
            if not frames or frame_reviewer is None:
                return FlowResult("context_insufficient", "frame_unavailable")
            working.media_context = deepcopy(frames)
            try:
                review = frame_reviewer(
                    working.model_copy(deep=True), cluster.model_copy(deep=True)
                )
            except Exception:
                return FlowResult("incomplete", "frame_review_failed")
            if review is None:
                return FlowResult("context_insufficient", "frame_unavailable")
            try:
                if not isinstance(review, FrameReview):
                    raise ValueError("invalid_frame_review")
                consensus = recheck(request, cluster, context, review)
            except ValueError:
                return FlowResult("incomplete", "invalid_frame_review")
            if not consensus.passed:
                return FlowResult("no_opportunity", "context_consensus_lost")
            working.media_context = [
                f for f in working.media_context if f.context_id == review.frame_id
            ]
            cluster = cluster.model_copy(update={"comment_ids": review.supported_comment_ids})
        elif context.status != "text_sufficient":
            return FlowResult("context_insufficient", context.status)
        assessment = judge.assess_opportunity(
            working,
            cluster.model_copy(deep=True),
            consensus.model_copy(deep=True),
        )
    except (ValueError, OSError):
        return FlowResult("incomplete", "judge_failed")
    gates: tuple[tuple[BusinessGate, bool], ...] = (
        ("software_deliverable", assessment.software_deliverable),
        ("light_operations", assessment.light_operations),
        ("specific_acquisition", assessment.specific_acquisition),
    )
    failed_gates = tuple(name for name, passed in gates if not passed)
    if failed_gates:
        return FlowResult("no_opportunity", "opportunity_filtered", failed_gates=failed_gates)
    candidate = assessment.candidate
    if candidate.cluster_id != cluster.cluster_id or candidate.evidence != consensus.evidence:
        return FlowResult("incomplete", "candidate_evidence_mismatch")
    # 嵌入稳定视频范围与排序后的证据 ID，独立于运行、簇名称和候选措辞。
    identity = [m2.platform, m2.video_id, sorted(e.comment_id for e in consensus.evidence)]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    intent = NewCandidate(status="new_candidate", candidate=candidate, idempotency_key=key)
    return store.commit_once(intent, history_revision=request.history_revision)
