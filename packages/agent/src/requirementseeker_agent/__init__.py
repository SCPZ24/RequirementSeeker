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
from .model import DeepSeekModelGateway, FrozenModelIdentity, QwenModelGateway, ScenarioModelGateway
from .pipeline import (
    M2AnalysisResult,
    RealEvaluationPreflightError,
    analyze_m2,
    analyze_m2_real_evaluation,
    preflight_real_evaluation,
)
from .rules import RULES_VERSION, evaluate_consensus
from .sampling import SAMPLING_POLICY_VERSION, SamplingPlan

__all__ = [
    "AnalysisRequest",
    "AnalysisResult",
    "ConsensusDecision",
    "ContextDecision",
    "DeepSeekModelGateway",
    "InferenceStep",
    "ModelInvocationAudit",
    "NeedCluster",
    "NeedSignal",
    "OpportunityCandidate",
    "OpportunityMergeDecision",
    "QwenModelGateway",
    "RULES_VERSION",
    "SAMPLING_POLICY_VERSION",
    "SamplingManifest",
    "SamplingPlan",
    "ScenarioModelGateway",
    "M2AnalysisResult",
    "TokenUsage",
    "analyze_m2",
    "analyze_m2_real_evaluation",
    "FrozenModelIdentity",
    "RealEvaluationPreflightError",
    "preflight_real_evaluation",
    "evaluate_consensus",
]
