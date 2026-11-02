"""本地合成交接格式。宿主提供可信运行记录；结果使用原 AnalysisResult v1。"""

from typing import Literal

from pydantic import TypeAdapter

from requirementseeker_agent.contracts.analysis import ContextDecision, OpportunityCandidate
from requirementseeker_agent.contracts.common import Contract, Identifier, Step
from requirementseeker_agent.contracts.requests import AnalysisRequest
from requirementseeker_agent.contracts.results import (
    AnalysisError,
    AnalysisResult,
    BudgetExhausted,
    Cancelled,
    FatalError,
    MergeEvidence,
    NewCandidate,
    Outcome,
    Rejected,
    RetryableError,
)
from requirementseeker_agent.pipeline.h0 import FlowResult, _ordered_evidence
from requirementseeker_agent.pipeline.types import M2AnalysisResult
from requirementseeker_agent.rules import evaluate_consensus


class RunSource(Contract):
    request: AnalysisRequest
    m2: M2AnalysisResult
    context: ContextDecision | None
    candidate: OpportunityCandidate | None
    target_ids: list[Identifier]
    history_complete: bool
    receipt: FlowResult
    previous: list[NewCandidate | MergeEvidence]
    current: list[NewCandidate | MergeEvidence]


class Handoff(Contract):
    run_id: Identifier
    status: Literal["committed", "already_committed", "no_opportunity", "incomplete"]
    reason: Identifier
    result: AnalysisResult | None


def project(source: RunSource) -> Handoff:
    request, m2, receipt = source.request, source.m2, source.receipt
    if (m2.run_id, m2.platform, m2.video_id) != (
        request.run_id,
        request.video.platform,
        request.video.video_id,
    ):
        raise ValueError("run_source_mismatch")
    steps: list[Step] = list(m2.completed_steps)
    contexts = [source.context] if source.context else []
    if contexts:
        steps.append("context")
    if source.candidate:
        steps.append("opportunity")
    decision = m2.decisions[0] if m2.decisions else None
    if decision and (
        len(m2.clusters) != 1 or decision != evaluate_consensus(request, m2.clusters[0])
    ):
        raise ValueError("consensus_source_mismatch")
    if source.candidate and (
        not decision
        or not decision.passed
        or source.candidate.cluster_id != decision.cluster_id
        or source.candidate.evidence != decision.evidence
        or not source.context
        or source.context.status != "text_sufficient"
        or source.context.cluster_id != decision.cluster_id
    ):
        raise ValueError("candidate_source_mismatch")
    intent = receipt.intent
    status = receipt.status
    outcome: Outcome
    if status in ("committed", "already_committed"):
        if (
            not source.history_complete
            or not source.candidate
            or not intent
            or m2.status != "completed"
        ):
            raise ValueError("missing_success_source")
        if status == "committed":
            if (
                receipt.reason != "intent_recorded"
                or source.current != [*source.previous, intent]
                or any(i.idempotency_key == intent.idempotency_key for i in source.previous)
            ):
                raise ValueError("commit_receipt_mismatch")
        elif (
            receipt.reason != "evidence_replayed"
            or source.current != source.previous
            or intent not in source.previous
        ):
            raise ValueError("replay_receipt_mismatch")
        if isinstance(intent, MergeEvidence):
            merge = intent.merge
            if (
                source.target_ids.count(merge.target_opportunity_id) != 1
                or merge.candidate_id != source.candidate.candidate_id
                or _ordered_evidence(merge.evidence) != _ordered_evidence(source.candidate.evidence)
            ):
                raise ValueError("merge_source_mismatch")
        elif intent.candidate.model_dump(exclude={"evidence"}) != source.candidate.model_dump(
            exclude={"evidence"}
        ) or _ordered_evidence(intent.candidate.evidence) != _ordered_evidence(
            source.candidate.evidence
        ):
            raise ValueError("new_candidate_mismatch")
        outcome = intent
        steps.append("dedup")
    else:
        if intent or source.previous != source.current:
            raise ValueError("noncommit_wrote_intent")
        if status == "incomplete" and receipt.reason == "history_incomplete":
            if source.history_complete or not source.candidate or m2.status != "completed":
                raise ValueError("history_state_mismatch")
            # v1 没有“未判定可重试性”的 outcome；保留真实停止原因。
            return Handoff(run_id=m2.run_id, status=status, reason=receipt.reason, result=None)
        if m2.clusters or contexts or source.candidate:
            raise ValueError("unsupported_handoff_state")
        if status == "no_opportunity" and receipt.reason == m2.status == "no_signal":
            outcome = Rejected(status="rejected", cluster_id=None, reason_code=receipt.reason)
        elif status == "incomplete" and receipt.reason == (m2.error_code or m2.status):
            stage: Step
            if m2.completed_steps == []:
                stage = "preprocess"
            elif m2.completed_steps == ["preprocess"]:
                stage = "signals"
            elif m2.completed_steps == ["preprocess", "signals"]:
                stage = "cluster"
            else:
                raise ValueError("unknown_stop_boundary")
            if m2.status == "budget_exhausted" and m2.exhausted_resource:
                outcome = BudgetExhausted(
                    status=m2.status, step=stage, resource=m2.exhausted_resource
                )
            elif m2.status == "cancelled":
                outcome = Cancelled(status=m2.status, step=stage)
            elif m2.status in ("fatal_error", "retryable_error") and m2.error_code:
                error = AnalysisError(step=stage, code=m2.error_code)
                outcome = (
                    FatalError(status="fatal_error", error=error)
                    if m2.status == "fatal_error"
                    else RetryableError(status="retryable_error", error=error)
                )
            else:
                raise ValueError("unsupported_failure")
        else:
            raise ValueError("unsupported_handoff_state")

    result = AnalysisResult(
        schema_version="1.0",
        run_id=m2.run_id,
        analysis_id=f"analysis-{m2.run_id}",
        platform=m2.platform,
        video_id=m2.video_id,
        completed_steps=steps,
        signals=m2.signals,
        clusters=m2.clusters,
        consensus_decisions=m2.decisions,
        context_decisions=contexts,
        inference_steps=[],
        audits=m2.audits,
        outcomes=[outcome],
    )
    return Handoff(run_id=m2.run_id, status=status, reason=receipt.reason, result=result)


sources_type = TypeAdapter(list[RunSource])
results_type = TypeAdapter(list[Handoff])
