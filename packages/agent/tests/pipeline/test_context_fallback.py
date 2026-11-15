from dataclasses import replace
from pathlib import Path

import pytest

from requirementseeker_agent import analyze_m2
from requirementseeker_agent.contracts.analysis import ContextDecision
from requirementseeker_agent.contracts.requests import MediaContext
from requirementseeker_agent.pipeline.context import FrameReview
from requirementseeker_agent.pipeline.h0 import MemoryIntentStore, run_h0
from requirementseeker_agent.runtime import InMemorySemanticCache
from requirementseeker_agent.sampling import plan_sampling


@pytest.mark.parametrize(
    ("case", "status", "reason", "calls", "written"),
    [
        ("text", "committed", "intent_recorded", 0, 1),
        ("text-ref", "incomplete", "invalid_context_refs", 0, 0),
        ("media-ref", "incomplete", "invalid_context_refs", 0, 0),
        ("filtered", "no_opportunity", "opportunity_filtered", 1, 0),
        ("no-vision", "context_insufficient", "image_capability_missing", 0, 0),
        ("no-frame", "context_insufficient", "frame_unavailable", 0, 0),
        ("unavailable", "context_insufficient", "frame_unavailable", 1, 0),
        ("lost", "no_opportunity", "context_consensus_lost", 1, 0),
        ("empty", "no_opportunity", "context_consensus_lost", 1, 0),
        ("valid", "committed", "intent_recorded", 1, 1),
        ("forged", "incomplete", "invalid_frame_review", 1, 0),
        ("foreign-member", "incomplete", "invalid_frame_review", 1, 0),
        ("failure", "incomplete", "frame_review_failed", 1, 0),
    ],
)
def test_actual_context_gate(monkeypatch, case, status, reason, calls, written):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "examples"))
    from m2_fake_demo import demo_manifest, demo_request
    from m2_h0_demo import ExportJudge, RecordingGateway
    from m4_result_format import RunSource, project

    request = demo_request()
    request.model.supports_images = case != "no-vision"
    first = request.comments[0]
    if case != "no-frame":
        request.media_context = [
            MediaContext(
                context_id="frame",
                kind="frame",
                source_ref=request.video.video_id,
                text=None,
                asset_ref="fixture-frame",
                first_collected_at=first.first_collected_at,
                expires_at=first.expires_at,
            )
        ]
    manifest = demo_manifest(request)
    m2 = analyze_m2(
        request,
        manifest,
        plan_sampling(manifest, request.comments, request.budget),
        RecordingGateway("valid_pipeline"),
        InMemorySemanticCache(),
    )
    observed = []

    class Judge(ExportJudge):
        def assess_context(self, request, cluster):
            assert not request.media_context
            self.context = ContextDecision(
                cluster_id=cluster.cluster_id,
                status="text_sufficient" if case in ("text", "text-ref") else "media_required",
                reason="合成对象判断",
                context_refs=["absent"] if case.endswith("-ref") else [],
            )
            return self.context

        def assess_opportunity(self, request, cluster, consensus):
            assert bool(request.media_context) == (case in ("valid", "filtered"))
            assessment = super().assess_opportunity(request, cluster, consensus)
            self.candidate = assessment.candidate
            return replace(assessment, light_operations=case != "filtered")

    def review(request, cluster):
        observed.append(request.run_id)
        if case == "failure":
            raise OSError("synthetic")
        if case == "unavailable":
            return None
        return FrameReview(
            cluster_id=cluster.cluster_id,
            frame_id="other" if case == "forged" else "frame",
            supported_comment_ids={
                "lost": ["c1", "c2"],
                "empty": [],
                "foreign-member": ["absent"],
            }.get(case, cluster.comment_ids),
        )

    judge, store = Judge(True), MemoryIntentStore()
    result = run_h0(request, m2, judge, store, frame_reviewer=review)
    assert (result.status, result.reason, len(observed), len(store.intents)) == (
        status,
        reason,
        calls,
        written,
    )
    assert judge.stages.count("opportunity") == written + (case == "filtered")
    if case.endswith("-ref") or case == "filtered":
        source = RunSource(
            request=request,
            m2=m2,
            context=judge.context,
            candidate=getattr(judge, "candidate", None),
            target_ids=[],
            history_complete=True,
            receipt=result,
            previous=[],
            current=list(store.intents.values()),
            frame_review=FrameReview(
                cluster_id=m2.clusters[0].cluster_id,
                frame_id="frame",
                supported_comment_ids=m2.clusters[0].comment_ids,
            )
            if case == "filtered"
            else None,
        )
        row = project(source)
        if case == "filtered":
            assert result.failed_gates == ("light_operations",)
            assert row.result.outcomes[0].status == "rejected"
            assert row.result.consensus_decisions[0].evidence == source.candidate.evidence
        else:
            assert row.result is None


def test_serialized_results_keep_context_stops_distinct(tmp_path, monkeypatch):
    import json

    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "examples"))
    from m3_context_demo import main
    from read_m4_results import read

    main(tmp_path)
    rows = list(read(tmp_path))
    assert [r["outcome"] for r in rows] == [
        "context_insufficient",
        "context_insufficient",
        "rejected",
        None,
        None,
        "new_candidate",
        "new_candidate",
    ]
    assert all(r["intent_key"] is None for r in rows[:5])
    assert rows[-1]["intent_key"] == rows[-2]["intent_key"]
    path = tmp_path / "results.json"
    forged = json.loads(path.read_bytes())
    assert not forged[2]["result"]["consensus_decisions"][0]["passed"]
    forged[2]["result"]["outcomes"] = forged[-1]["result"]["outcomes"]
    path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError):
        list(read(tmp_path))
