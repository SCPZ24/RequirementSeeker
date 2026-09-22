"""M2 包内结果类型；在正式线协议升级前不对外承诺兼容。"""

from typing import Literal, Self

from pydantic import model_validator

from ..contracts.analysis import (
    ConsensusDecision,
    ModelInvocationAudit,
    NeedCluster,
    NeedSignal,
)
from ..contracts.common import Contract, Identifier, Platform
from ..runtime import BudgetResource, BudgetSnapshot, CacheEvent
from ..sampling import SamplingPlan

M2Status = Literal[
    "completed",
    "no_signal",
    "budget_exhausted",
    "cancelled",
    "retryable_error",
    "fatal_error",
]
CompletedStep = Literal["preprocess", "signals", "cluster", "consensus"]


class M2AnalysisResult(Contract):
    """一次 M2 运行的可信内部结果与停止原因。"""

    run_id: Identifier
    platform: Platform
    video_id: Identifier
    sampling_plan: SamplingPlan
    signals: list[NeedSignal]
    clusters: list[NeedCluster]
    decisions: list[ConsensusDecision]
    audits: list[ModelInvocationAudit]
    cache_events: list[CacheEvent]
    budget: BudgetSnapshot
    completed_steps: list[CompletedStep]
    status: M2Status
    error_code: Identifier | None
    exhausted_resource: BudgetResource | None

    @model_validator(mode="after")
    def result_state(self) -> Self:
        if len(self.completed_steps) != len(set(self.completed_steps)):
            raise ValueError("completed_steps_must_be_unique")
        if self.status in {"completed", "no_signal"}:
            if self.error_code is not None or self.exhausted_resource is not None:
                raise ValueError("successful_result_cannot_have_error")
        elif self.clusters or self.decisions:
            raise ValueError("non_success_result_cannot_expose_clusters")

        if self.status == "no_signal" and self.signals:
            raise ValueError("no_signal_result_cannot_expose_signals")
        if self.status == "completed" and (
            not self.signals or not self.clusters or not self.decisions
        ):
            raise ValueError("completed_result_requires_clusters")
        if self.status == "budget_exhausted":
            if self.exhausted_resource is None or self.error_code is not None:
                raise ValueError("budget_result_requires_only_exhausted_resource")
        elif self.exhausted_resource is not None:
            raise ValueError("non_budget_result_cannot_have_exhausted_resource")
        if self.status in {"retryable_error", "fatal_error", "cancelled"}:
            if self.error_code is None:
                raise ValueError("failed_result_requires_error_code")

        if len(self.clusters) != len(self.decisions):
            raise ValueError("clusters_and_decisions_must_align")
        if any(
            cluster.cluster_id != decision.cluster_id
            for cluster, decision in zip(self.clusters, self.decisions, strict=True)
        ):
            raise ValueError("cluster_and_decision_ids_must_match")
        return self
