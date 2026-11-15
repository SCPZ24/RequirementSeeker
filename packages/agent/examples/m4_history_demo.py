"""实际 M2 → M3 → 基于合成历史生成 M4 意图，历史记录保持只读。"""

import json
from copy import deepcopy
from dataclasses import replace

from m2_fake_demo import demo_manifest, demo_request
from m2_h0_demo import ExportJudge, RecordingGateway

from requirementseeker_agent import analyze_m2
from requirementseeker_agent.contracts.analysis import Evidence, OpportunityMergeDecision
from requirementseeker_agent.pipeline.h0 import HistoricalOpportunity, MemoryIntentStore, run_h0
from requirementseeker_agent.runtime import InMemorySemanticCache
from requirementseeker_agent.sampling import plan_sampling


class FixtureRouter:
    """固定场景判断结果；生产语义判定由注入端口承担。"""

    def __init__(self):
        self.answer = "different"
        self.calls = 0

    def __call__(self, candidate, records):
        self.calls += 1
        if self.answer == "failure":
            raise OSError("模拟历史判断失败")
        if self.answer == "different":
            return None
        if self.answer == "bad_evidence":
            candidate.evidence[0].text = "判断器改写的引文"
        return OpportunityMergeDecision(
            candidate_id="foreign" if self.answer == "bad_candidate" else candidate.candidate_id,
            target_opportunity_id="foreign"
            if self.answer == "bad_target"
            else records[0].opportunity_id,
            reason="合成案例指定相同用户、问题与解决方向",
            evidence=candidate.evidence,
        )


def scenarios():
    revision = "history-demo-v1"
    historic = HistoricalOpportunity(
        "history-export",
        "需导出多条记录的人",
        "整理记录时导出文件",
        "逐条导出耗时",
        "本地导出工具",
        (
            Evidence(
                platform="bilibili",
                video_id="old-video",
                comment_id="old-comment",
                author_id="old-author",
                text="希望导出已有记录",
            ),
        ),
        "人工跟进",
        "保留原备注",
    )

    def execute(label, store, router, record):
        request = demo_request()
        request.run_id = label
        request.history_revision = revision
        manifest = demo_manifest(request)
        gateway = RecordingGateway("valid_pipeline")
        m2 = analyze_m2(
            request,
            manifest,
            plan_sampling(manifest, request.comments, request.budget),
            gateway,
            InMemorySemanticCache(),
        )
        previous = deepcopy(store.intents)
        outcome = run_h0(request, m2, ExportJudge(True), store)
        return {
            "case": label,
            "run_id": request.run_id,
            "m2_status": m2.status,
            "gateway_stages": gateway.stages,
            "route_calls": router.calls,
            "status": outcome.status,
            "reason": outcome.reason,
            "intent_count": len(store.intents),
            "previous_intents_preserved": all(store.intents[k] == v for k, v in previous.items()),
            "historical_record": record.opportunity_id,
            "intent": outcome.intent.model_dump(mode="json") if outcome.intent else None,
            "current_consensus": [item.model_dump() for item in m2.decisions[0].evidence],
        }

    for answer in (
        "same",
        "different",
        "bad_target",
        "bad_candidate",
        "bad_evidence",
        "failure",
        "incomplete",
    ):
        record = (
            replace(historic, target_user="审计员", scenario="在审计归档时导出签名凭据")
            if answer == "different"
            else historic
        )
        router = FixtureRouter()
        router.answer = answer
        store = MemoryIntentStore(
            history=(record,),
            history_revision=revision,
            history_judge=router,
            history_complete=answer != "incomplete",
        )
        yield execute(answer, store, router, record)
        if answer in ("same", "different"):
            yield execute(answer + "-replay", store, router, record)
        if answer == "same":
            store.history_complete = False
            yield execute("incomplete-with-prior-receipt", store, router, record)


if __name__ == "__main__":
    for row in scenarios():
        print(json.dumps(row, ensure_ascii=False))
