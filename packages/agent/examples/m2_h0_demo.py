"""从合成请求运行生产 M2，将返回对象直接交给单簇 M3/M4。"""

import json

from m2_fake_demo import demo_manifest, demo_request

from requirementseeker_agent import ScenarioModelGateway, analyze_m2
from requirementseeker_agent.contracts import ContextDecision, OpportunityCandidate
from requirementseeker_agent.pipeline.h0 import Assessment, MemoryIntentStore, run_h0
from requirementseeker_agent.runtime import InMemorySemanticCache
from requirementseeker_agent.sampling import plan_sampling


class RecordingGateway(ScenarioModelGateway):
    def __init__(self, scenario):
        super().__init__(scenario)
        self.stages = []

    def invoke(self, request):
        self.stages.append(request.stage)
        return super().invoke(request)


class ExportJudge:
    """仅为合成的批量导出场景给出固定判断，从本次共识取原文证据。"""

    def __init__(self, sufficient):
        self.sufficient = sufficient
        self.stages = []

    def assess_context(self, request, cluster):
        self.stages.append("context")
        return ContextDecision(
            cluster_id=cluster.cluster_id,
            status="text_sufficient" if self.sufficient else "insufficient",
            reason="合成文本上下文判定",
            context_refs=[],
        )

    def assess_opportunity(self, request, cluster, consensus):
        self.stages.append("opportunity")
        candidate = OpportunityCandidate(
            candidate_id="synthetic-export",
            cluster_id=cluster.cluster_id,
            title="批量导出记录",
            summary="将选中的记录一次导出到文件",
            target_user="需导出多条记录的人",
            scenario="整理记录时导出文件",
            problem="逐条导出耗时",
            current_solution="手动逐条复制",
            software_shape="本地导出工具",
            core_value="一次导出多条记录",
            minimal_features=["选择记录", "导出文件"],
            non_heavy_operation_reason="在本机处理文件",
            acquisition_path="向讨论导出方法的人演示",
            risks=["文件格式兼容性"],
            evidence=consensus.evidence,
        )
        return Assessment(candidate, True, True, True)


def scenarios():
    store = MemoryIntentStore()
    for case, scenario in (
        ("no_signal", "no_signal"),
        ("no_consensus", "valid_pipeline"),
        ("context_insufficient", "valid_pipeline"),
        ("gateway_failure", "authentication_failed"),
        ("cancelled", "valid_pipeline"),
        ("first", "valid_pipeline"),
        ("replay", "valid_pipeline"),
    ):
        request = demo_request()
        request.run_id = f"synthetic-{case}"
        if case == "no_consensus":
            # 样本仍有三位作者，但视频作者不能计入独立共识。
            request.comments[-1].author_id = request.video.author_id
        request.cancellation_requested = case == "cancelled"
        manifest = demo_manifest(request)
        plan = plan_sampling(manifest, request.comments, request.budget)
        gateway = RecordingGateway(scenario)
        judge = ExportJudge(case != "context_insufficient")
        # 每次从空语义缓存重新执行 M2，只有意图存储跨运行保留。
        m2 = analyze_m2(request, manifest, plan, gateway, InMemorySemanticCache())
        outcome = run_h0(request, m2, judge, store)
        yield {
            "case": case,
            "run_id": m2.run_id,
            "m2_status": m2.status,
            "steps": m2.completed_steps,
            "gateway_stages": gateway.stages,
            "judge_stages": judge.stages,
            "status": outcome.status,
            "reason": outcome.reason,
            "intent_key": outcome.intent.idempotency_key if outcome.intent else None,
            "intent_count": len(store.intents),
        }


if __name__ == "__main__":
    for row in scenarios():
        print(json.dumps(row, ensure_ascii=False))
