"""RequirementSeeker Agent: versioned offline contracts."""

__version__ = "0.1.0"

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
    "TokenUsage",
]
