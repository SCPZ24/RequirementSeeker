"""单视频、单簇的离线 M3/M4 交接。提交成功只表示内存 sink 已接收意图。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

from ..contracts.analysis import (
    ConsensusDecision,
    ContextDecision,
    NeedCluster,
    OpportunityCandidate,
)
from ..contracts.requests import AnalysisRequest
from ..contracts.results import NewCandidate
from ..rules import evaluate_consensus
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


@dataclass(frozen=True)
class FlowResult:
    status: Literal[
        "committed", "already_committed", "context_insufficient", "no_opportunity", "incomplete"
    ]
    reason: str
    intent: NewCandidate | None = None


class MemoryIntentStore:
    """演示用的串行、进程内历史与提交替身。"""

    def __init__(self, *, history_complete: bool = True) -> None:
        self.history_complete = history_complete
        self.intents: dict[str, NewCandidate] = {}

    def commit_once(self, intent: NewCandidate) -> FlowResult:
        key = intent.idempotency_key
        previous = self.intents.get(key)
        if previous is not None:
            if previous.candidate.evidence != intent.candidate.evidence:
                return FlowResult("incomplete", "evidence_conflict")
            return FlowResult(
                "already_committed", "evidence_replayed", previous.model_copy(deep=True)
            )
        if not self.history_complete:
            return FlowResult("incomplete", "history_incomplete")
        self.intents[key] = intent.model_copy(deep=True)
        return FlowResult("committed", "intent_recorded", intent)


def run_h0(
    request: AnalysisRequest,
    m2: M2AnalysisResult,
    judge: OpportunityJudge,
    store: MemoryIntentStore,
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
    if m2.status == "no_signal" and "signals" in m2.completed_steps and not m2.clusters:
        return FlowResult("no_opportunity", "no_signal")
    if m2.status != "completed" or "consensus" not in m2.completed_steps:
        return FlowResult("incomplete", m2.error_code or m2.status)
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
        context = judge.assess_context(request.model_copy(deep=True), cluster.model_copy(deep=True))
        if context.cluster_id != cluster.cluster_id:
            return FlowResult("incomplete", "context_cluster_mismatch")
        if context.status == "context_sufficient":
            return FlowResult("incomplete", "unsupported_media_context")
        if context.status != "text_sufficient":
            return FlowResult("context_insufficient", context.status)
        assessment = judge.assess_opportunity(
            request.model_copy(deep=True),
            cluster.model_copy(deep=True),
            consensus.model_copy(deep=True),
        )
    except (ValueError, OSError):
        return FlowResult("incomplete", "judge_failed")
    if not all(
        (
            assessment.software_deliverable,
            assessment.light_operations,
            assessment.specific_acquisition,
        )
    ):
        return FlowResult("no_opportunity", "opportunity_filtered")
    candidate = assessment.candidate
    if candidate.cluster_id != cluster.cluster_id or candidate.evidence != consensus.evidence:
        return FlowResult("incomplete", "candidate_evidence_mismatch")
    # 嵌入稳定视频范围与排序后的证据 ID，独立于运行、簇名称和候选措辞。
    identity = [m2.platform, m2.video_id, sorted(e.comment_id for e in consensus.evidence)]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    intent = NewCandidate(status="new_candidate", candidate=candidate, idempotency_key=key)
    return store.commit_once(intent)
