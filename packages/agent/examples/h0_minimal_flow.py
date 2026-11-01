"""运行合成 M2 → M3 → M4 场景，输出内存 sink 的实际回执。"""

import json
from pathlib import Path

from requirementseeker_agent.contracts import (
    AnalysisRequest,
    ConsensusDecision,
    ContextDecision,
    NeedCluster,
    OpportunityCandidate,
)
from requirementseeker_agent.pipeline.h0 import Assessment, MemoryIntentStore, run_h0
from requirementseeker_agent.pipeline.types import M2AnalysisResult
from requirementseeker_agent.rules import evaluate_consensus

EXAMPLES = Path(__file__).parent
FIXTURES = EXAMPLES.parent / "tests" / "fixtures" / "valid"


class FixtureJudge:
    """以固定合成材料代替机会模型，管线负责校验证据、执行门槛与提交。"""

    def __init__(self, candidate: OpportunityCandidate, text_sufficient: bool = True) -> None:
        self.candidate = candidate
        self.text_sufficient = text_sufficient

    def assess_context(self, request: AnalysisRequest, cluster: NeedCluster) -> ContextDecision:
        return ContextDecision(
            cluster_id=cluster.cluster_id,
            status="text_sufficient" if self.text_sufficient else "insufficient",
            reason="已给出明确的文本操作对象" if self.text_sufficient else "操作对象未知",
            context_refs=[],
        )

    def assess_opportunity(
        self, request: AnalysisRequest, cluster: NeedCluster, consensus: ConsensusDecision
    ) -> Assessment:
        return Assessment(self.candidate, True, True, True)


def main() -> None:
    request = AnalysisRequest.model_validate_json((FIXTURES / "request.json").read_bytes())
    result = M2AnalysisResult.model_validate_json((EXAMPLES / "h0-m2.json").read_bytes())
    data = json.loads((FIXTURES / "result-new.json").read_bytes())
    candidate = OpportunityCandidate.model_validate(data["outcomes"][0]["candidate"])
    judge = FixtureJudge(candidate)
    store = MemoryIntentStore()

    def show(label: str, m2: M2AnalysisResult, model: FixtureJudge) -> None:
        outcome = run_h0(request, m2, model, store)
        print(
            json.dumps(
                dict(
                    case=label,
                    status=outcome.status,
                    reason=outcome.reason,
                    intent_key=outcome.intent.idempotency_key if outcome.intent else None,
                    intent_count=len(store.intents),
                ),
                ensure_ascii=False,
            )
        )

    show("insufficient_context", result, FixtureJudge(candidate, text_sufficient=False))
    fewer = result.model_copy(deep=True)
    fewer.clusters[0].comment_ids.pop()
    fewer.signals.pop()
    fewer.decisions[0] = evaluate_consensus(request, fewer.clusters[0])
    show("no_consensus", fewer, judge)
    show("first", result, judge)
    request.run_id = result.run_id = "synthetic-replay"
    show("replay", result, judge)
    stopped = result.model_dump()
    stopped.update(
        status="cancelled",
        error_code="cancelled",
        completed_steps=["preprocess"],
        signals=[],
        clusters=[],
        decisions=[],
    )
    show("upstream_cancelled", M2AnalysisResult.model_validate(stopped), judge)


if __name__ == "__main__":
    main()
