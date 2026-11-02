"""生成 A29 的可信合成运行记录与可移交结果。"""

import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from m2_fake_demo import demo_manifest, demo_request
from m2_h0_demo import ExportJudge, RecordingGateway
from m4_history_demo import FixtureRouter
from m4_result_format import RunSource, project, results_type, sources_type

from requirementseeker_agent import analyze_m2
from requirementseeker_agent.pipeline.h0 import HistoricalOpportunity, MemoryIntentStore, run_h0
from requirementseeker_agent.runtime import InMemorySemanticCache
from requirementseeker_agent.sampling import plan_sampling


class ObservedJudge(ExportJudge):
    context = None
    candidate = None

    def assess_context(self, *args):
        self.context = super().assess_context(*args)
        return self.context

    def assess_opportunity(self, *args):
        assessment = super().assess_opportunity(*args)
        self.candidate = assessment.candidate
        return assessment


def runs():
    for answer, cases in (
        ("same", ("append", "append-replay", "history-incomplete")),
        (
            "different",
            ("new", "new-replay", "no-signal", "cancelled", "budget", "fatal", "retryable"),
        ),
    ):
        history = HistoricalOpportunity(
            "past-export",
            "记录员" if answer == "same" else "审计员",
            "导出个人记录" if answer == "same" else "归档审计签名",
            "手工复制",
            "本地导出工具",
            manual_note="人工备注",
            manual_stage="评估中",
        )
        router = FixtureRouter()
        router.answer = answer
        store = MemoryIntentStore(
            history=(history,), history_revision="history-a29", history_judge=router
        )
        for label in cases:
            request = demo_request()
            request.run_id = label
            request.history_revision = "history-a29"
            if label.endswith("-replay"):
                # 时间轻微变化使共识证据重排，来源与原文保持相同。
                first = request.comments[0]
                request.comments[0] = type(first).model_validate(
                    {
                        **first.model_dump(),
                        "first_collected_at": first.first_collected_at + timedelta(seconds=1),
                        "expires_at": first.expires_at + timedelta(seconds=1),
                    }
                )
            request.cancellation_requested = label == "cancelled"
            if label == "budget":
                request.budget.max_model_calls = 1
            manifest = demo_manifest(request)
            gateway = RecordingGateway(
                {
                    "no-signal": "no_signal",
                    "fatal": "authentication_failed",
                    "retryable": "timeout",
                }.get(label, "valid_pipeline")
            )
            m2 = analyze_m2(
                request,
                manifest,
                plan_sampling(manifest, request.comments, request.budget),
                gateway,
                InMemorySemanticCache(),
            )
            store.history_complete = label != "history-incomplete"
            judge = ObservedJudge(True)
            before = deepcopy(list(store.intents.values()))
            receipt = run_h0(request, m2, judge, store)
            yield RunSource(
                request=request,
                m2=m2,
                context=judge.context,
                candidate=judge.candidate,
                target_ids=[history.opportunity_id],
                history_complete=store.history_complete,
                receipt=receipt,
                previous=before,
                current=deepcopy(list(store.intents.values())),
            )


def main(directory):
    directory.mkdir(parents=True, exist_ok=True)
    sources = list(runs())
    rows = [project(s) for s in sources]
    (directory / "sources.json").write_bytes(sources_type.dump_json(sources, indent=2))
    (directory / "results.json").write_bytes(results_type.dump_json(rows, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
