"""RequirementSeeker Agent: versioned offline contracts and deterministic rules."""

__version__ = "0.2.0"

from .contracts import (
    AnalysisRequest,
    AnalysisResult,
    ConsensusDecision,
    ContextDecision,
    InferenceStep,
    ModelInvocationAudit,
    NeedCluster,
    NeedSignal,
    OpportunityCandidate,
    OpportunityMergeDecision,
    TokenUsage,
)
from .rules import RULES_VERSION, evaluate_consensus

__all__ = [
    "AnalysisRequest",
    "AnalysisResult",
    "ConsensusDecision",
    "ContextDecision",
    "InferenceStep",
    "ModelInvocationAudit",
    "NeedCluster",
    "NeedSignal",
    "OpportunityCandidate",
    "OpportunityMergeDecision",
    "RULES_VERSION",
    "TokenUsage",
    "evaluate_consensus",
]
