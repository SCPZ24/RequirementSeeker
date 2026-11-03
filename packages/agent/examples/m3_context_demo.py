"""运行 A30 单帧合成场景；按需生成可由 A29 读取端核验的交接文件。"""

import json
import sys
from copy import deepcopy
from pathlib import Path

from generate_m4_results import ObservedJudge
from m2_fake_demo import demo_manifest, demo_request
from m2_h0_demo import RecordingGateway
from m4_result_format import RunSource, write_handoff

from requirementseeker_agent import analyze_m2
from requirementseeker_agent.contracts.requests import MediaContext
from requirementseeker_agent.pipeline.context import FrameReview
from requirementseeker_agent.pipeline.h0 import MemoryIntentStore, run_h0
from requirementseeker_agent.runtime import InMemorySemanticCache
from requirementseeker_agent.sampling import plan_sampling


class FrameJudge(ObservedJudge):
    def assess_context(self, request, cluster):
        context = super().assess_context(request, cluster)
        if not self.sufficient:
            context.status = "media_required"
        return context


class LocalFrames:
    """合成帧内容已预判，只给出仍指向同一软件对象的评论成员。"""

    material = {"export-tool": ["c1", "c2", "c3"], "two-export-one-paper": ["c1", "c2"]}

    def __init__(self):
        self.calls = []
        self.review = None

    def __call__(self, request, cluster):
        frame = request.media_context[0]
        self.calls.append(frame.asset_ref)
        if frame.asset_ref == "read-error":
            raise OSError("本地帧替身失败")
        if frame.asset_ref == "wrong-reference":
            self.review = FrameReview(
                cluster_id=cluster.cluster_id,
                frame_id="unknown",
                supported_comment_ids=cluster.comment_ids,
            )
        elif frame.asset_ref in self.material:
            self.review = FrameReview(
                cluster_id=cluster.cluster_id,
                frame_id=frame.context_id,
                supported_comment_ids=self.material[frame.asset_ref],
            )
        return self.review


def runs():
    store = MemoryIntentStore()
    for case in (
        "no-vision",
        "missing",
        "revoked",
        "failure",
        "bad-ref",
        "supported",
        "text-replay",
    ):
        request = demo_request()
        request.run_id = case
        request.model.supports_images = case != "no-vision"
        first = request.comments[0]
        request.media_context = [
            MediaContext(
                context_id="export-frame",
                kind="frame",
                source_ref=request.video.video_id,
                text=None,
                asset_ref={
                    "missing": "absent",
                    "revoked": "two-export-one-paper",
                    "failure": "read-error",
                    "bad-ref": "wrong-reference",
                }.get(case, "export-tool"),
                first_collected_at=first.first_collected_at,
                expires_at=first.expires_at,
            )
        ]
        manifest = demo_manifest(request)
        gateway = RecordingGateway("valid_pipeline")
        m2 = analyze_m2(
            request,
            manifest,
            plan_sampling(manifest, request.comments, request.budget),
            gateway,
            InMemorySemanticCache(),
        )
        judge = FrameJudge(case == "text-replay")
        frames = LocalFrames()

        before = deepcopy(list(store.intents.values()))
        receipt = run_h0(request, m2, judge, store, frame_reviewer=frames)
        source = RunSource(
            request=request,
            m2=m2,
            context=judge.context,
            candidate=judge.candidate,
            frame_review=frames.review,
            target_ids=[],
            history_complete=True,
            receipt=receipt,
            previous=before,
            current=deepcopy(list(store.intents.values())),
        )
        yield (
            source,
            {
                "case": case,
                "gateway": gateway.stages,
                "frame_calls": len(frames.calls),
                "judge": judge.stages,
                "status": receipt.status,
                "reason": receipt.reason,
                "intents": len(store.intents),
            },
        )


def main(directory):
    sources = []
    for source, observation in runs():
        sources.append(source)
        print(json.dumps(observation))
    write_handoff(directory, sources)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
