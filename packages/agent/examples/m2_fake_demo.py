"""Run the deterministic M2 pipeline without a model service or platform login."""

import json
from dataclasses import asdict
from pathlib import Path

from requirementseeker_agent import (
    AnalysisRequest,
    SamplingManifest,
    ScenarioModelGateway,
    analyze_m2,
)
from requirementseeker_agent.runtime import InMemorySemanticCache
from requirementseeker_agent.sampling import plan_sampling


def main() -> None:
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "valid" / "request.json"
    source = json.loads(fixture.read_text(encoding="utf-8"))
    source["video"]["title"] = "批量导出记录的合成视频"
    texts = ("希望批量导出记录。", "需要一次导出全部记录。", "想把记录批量导出到文件。")
    for index, (comment, text) in enumerate(zip(source["comments"], texts, strict=True), start=1):
        comment["comment_id"] = f"c{index}"
        comment["text"] = text
    request = AnalysisRequest.model_validate(source)

    comment_ids = [comment.comment_id for comment in request.comments]
    manifest = SamplingManifest(
        sampling_schema_version="1.0",
        manifest_id="demo-manifest-1",
        platform=request.video.platform,
        video_id=request.video.video_id,
        captured_at=request.analysis_time,
        reported_total=len(comment_ids),
        collection_target=len(comment_ids),
        collected_total=len(comment_ids),
        pages_requested=1,
        pages_succeeded=1,
        available_strata={"top"},
        direction="software_tool",
        author_id_present=len(comment_ids),
        distinct_author_count=len(comment_ids),
        exact_duplicate_count=0,
        normalized_duplicate_count=0,
        video_metrics=None,
        candidate_comment_ids=comment_ids,
        stratum_comment_ids={"top": comment_ids},
    )
    plan = plan_sampling(manifest, request.comments, request.budget)
    result = analyze_m2(
        request,
        manifest,
        plan,
        ScenarioModelGateway("valid_pipeline"),
        InMemorySemanticCache(),
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "signal_ids": [signal.signal_id for signal in result.signals],
                "cluster_ids": [cluster.cluster_id for cluster in result.clusters],
                "decisions": [
                    {"cluster_id": decision.cluster_id, "passed": decision.passed}
                    for decision in result.decisions
                ],
                "budget": asdict(result.budget),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
