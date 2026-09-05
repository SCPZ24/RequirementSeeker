"""Analysis records declare evidence; M1 must verify it against trusted inputs."""

from typing import Literal, Self

from pydantic import Field, StrictBool, model_validator

from .common import (
    Contract,
    Hash,
    Identifier,
    NonNegativeInt,
    Platform,
    PositiveInt,
    Step,
    Text,
    Versions,
)


class Evidence(Contract):
    platform: Platform
    video_id: Identifier
    comment_id: Identifier
    author_id: Identifier
    text: Text


def check_evidence(evidence: list[Evidence]) -> None:
    if len({(item.platform, item.video_id) for item in evidence}) != 1:
        raise ValueError("evidence_must_reference_one_platform_video")
    if len({item.author_id for item in evidence}) != len(evidence):
        raise ValueError("representative_evidence_requires_distinct_authors")
    if len({item.comment_id for item in evidence}) != len(evidence):
        raise ValueError("representative_evidence_requires_distinct_comments")


class NeedSignal(Contract):
    signal_id: Identifier
    comment_id: Identifier
    kind: Literal["pain", "need", "alternative", "product_defect"]
    summary: Text


class NeedCluster(Contract):
    cluster_id: Identifier
    comment_ids: list[Identifier] = Field(min_length=1)
    summary: Text

    @model_validator(mode="after")
    def unique_members(self) -> Self:
        if len(self.comment_ids) != len(set(self.comment_ids)):
            raise ValueError("cluster_members_must_be_unique")
        return self


class ConsensusDecision(Contract):
    cluster_id: Identifier
    passed: StrictBool
    reason_code: Identifier
    evidence: list[Evidence]

    @model_validator(mode="after")
    def evidence_shape(self) -> Self:
        if self.passed:
            if len(self.evidence) < 3:
                raise ValueError("passing_consensus_requires_three_representatives")
            check_evidence(self.evidence)
        elif self.evidence:
            raise ValueError("failed_consensus_must_not_declare_approved_evidence")
        return self


class ContextDecision(Contract):
    cluster_id: Identifier
    status: Literal["text_sufficient", "media_required", "context_sufficient", "insufficient"]
    reason: Text
    context_refs: list[Identifier]


class OpportunityCandidate(Contract):
    candidate_id: Identifier
    cluster_id: Identifier
    title: Text
    summary: Text
    target_user: Text
    scenario: Text
    problem: Text
    current_solution: Text
    software_shape: Text
    core_value: Text
    minimal_features: list[Text] = Field(min_length=1)
    non_heavy_operation_reason: Text
    acquisition_path: Text
    risks: list[Text]
    evidence: list[Evidence] = Field(min_length=3)

    @model_validator(mode="after")
    def representative_evidence(self) -> Self:
        check_evidence(self.evidence)
        return self


class OpportunityMergeDecision(Contract):
    candidate_id: Identifier
    target_opportunity_id: Identifier
    reason: Text
    evidence: list[Evidence] = Field(min_length=3)

    @model_validator(mode="after")
    def representative_evidence(self) -> Self:
        check_evidence(self.evidence)
        return self


class InferenceStep(Contract):
    step_id: Identifier
    kind: Literal["fact", "inference"]
    title: Text
    body: Text
    evidence_refs: list[Identifier]

    @model_validator(mode="after")
    def facts_need_sources(self) -> Self:
        if self.kind == "fact" and not self.evidence_refs:
            raise ValueError("fact_requires_evidence_reference")
        return self


class TokenUsage(Contract):
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    total_tokens: NonNegativeInt

    @model_validator(mode="after")
    def total_matches(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("token_total_must_match_components")
        return self


class ModelInvocationAudit(Contract):
    invocation_id: Identifier
    model_config_ref: Identifier
    model_name: Identifier
    versions: Versions
    input_hash: Hash
    step: Step
    attempt: PositiveInt
    status: Literal["success", "error", "cancelled"]
    usage: TokenUsage | None
    error_code: Identifier | None

    @model_validator(mode="after")
    def error_status(self) -> Self:
        if (self.status == "error") != (self.error_code is not None):
            raise ValueError("audit_error_code_must_match_error_status")
        return self
