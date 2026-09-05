"""Typed outcomes; a candidate is never an assertion of a successful DB commit."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .analysis import (
    ConsensusDecision,
    ContextDecision,
    InferenceStep,
    ModelInvocationAudit,
    NeedCluster,
    NeedSignal,
    OpportunityCandidate,
    OpportunityMergeDecision,
)
from .common import Contract, Hash, Identifier, Platform, Step


class NewCandidate(Contract):
    status: Literal["new_candidate"]
    candidate: OpportunityCandidate
    idempotency_key: Hash


class MergeEvidence(Contract):
    status: Literal["merge_evidence"]
    merge: OpportunityMergeDecision
    idempotency_key: Hash


class Rejected(Contract):
    status: Literal["rejected"]
    cluster_id: Identifier | None
    reason_code: Identifier


class InsufficientContext(Contract):
    status: Literal["context_insufficient"]
    cluster_id: Identifier
    reason_code: Identifier


class AnalysisError(Contract):
    code: Identifier
    step: Step


class RetryableError(Contract):
    status: Literal["retryable_error"]
    error: AnalysisError


class FatalError(Contract):
    status: Literal["fatal_error"]
    error: AnalysisError


class BudgetExhausted(Contract):
    status: Literal["budget_exhausted"]
    step: Step
    resource: Literal["comments", "model_calls", "input_tokens", "output_tokens"]


class Cancelled(Contract):
    status: Literal["cancelled"]
    step: Step


Outcome = Annotated[
    NewCandidate
    | MergeEvidence
    | Rejected
    | InsufficientContext
    | RetryableError
    | FatalError
    | BudgetExhausted
    | Cancelled,
    Field(discriminator="status"),
]


class AnalysisResult(Contract):
    schema_version: Literal["1.0"]
    run_id: Identifier
    analysis_id: Identifier
    platform: Platform
    video_id: Identifier
    completed_steps: list[Step]
    signals: list[NeedSignal]
    clusters: list[NeedCluster]
    consensus_decisions: list[ConsensusDecision]
    context_decisions: list[ContextDecision]
    inference_steps: list[InferenceStep]
    audits: list[ModelInvocationAudit]
    outcomes: list[Outcome] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_video(self) -> Self:
        evidence = [item for decision in self.consensus_decisions for item in decision.evidence]
        for outcome in self.outcomes:
            if isinstance(outcome, NewCandidate):
                evidence.extend(outcome.candidate.evidence)
            elif isinstance(outcome, MergeEvidence):
                evidence.extend(outcome.merge.evidence)
        if any(
            (item.platform, item.video_id) != (self.platform, self.video_id) for item in evidence
        ):
            raise ValueError("result_evidence_must_match_result_video")
        return self
