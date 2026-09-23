"""RequirementSeeker Agent: versioned offline contracts and deterministic rules."""

__version__ = "0.3.0"

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
    SamplingManifest,
    TokenUsage,
)
from .model import ScenarioModelGateway
from .pipeline import M2AnalysisResult, analyze_m2
from .rules import RULES_VERSION, evaluate_consensus
from .sampling import SAMPLING_POLICY_VERSION, SamplingPlan

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
    "SAMPLING_POLICY_VERSION",
    "SamplingManifest",
    "SamplingPlan",
    "ScenarioModelGateway",
    "M2AnalysisResult",
    "TokenUsage",
    "analyze_m2",
    "evaluate_consensus",
]
