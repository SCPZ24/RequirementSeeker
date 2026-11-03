"""冻结合成种子的离线行为核验，语义答案来自替身，不评价模型质量。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from requirementseeker_agent import AnalysisRequest, ScenarioModelGateway, analyze_m2
from requirementseeker_agent.contracts import ContextDecision, NeedCluster
from requirementseeker_agent.pipeline.h0 import MemoryIntentStore, run_h0
from requirementseeker_agent.rules import evaluate_consensus
from requirementseeker_agent.runtime import InMemorySemanticCache
from requirementseeker_agent.sampling import plan_sampling

ROOT = Path(__file__).parents[2]
CASES = json.loads((ROOT / "evals/gold.json").read_bytes())["cases"]


@pytest.mark.parametrize("seed", CASES, ids=lambda seed: seed["id"])
def test_frozen_synthetic_seed(seed, monkeypatch):
    name = seed["id"]
    payload = (ROOT / "evals" / seed["request_path"]).read_bytes()
    if name == "cross-video":
        with pytest.raises(ValidationError, match="snapshot_must_contain_one_platform_video"):
            AnalysisRequest.model_validate_json(payload)
        return
    request = AnalysisRequest.model_validate_json(payload)
    ids = [c.comment_id for c in request.comments]
    if name in ("three-authors", "two-authors", "repeated-author", "unknown-author"):
        decision = evaluate_consensus(
            request, NeedCluster(cluster_id="seed", comment_ids=ids, summary="明确簇")
        )
        assert decision.passed == (name == "three-authors")
        return

    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    from m2_fake_demo import demo_manifest
    from m2_h0_demo import ExportJudge

    class SeedGateway(ScenarioModelGateway):
        def invoke(self, call):
            response = super().invoke(call)
            if name not in ("no-need", "prompt-injection"):
                response.payload = (
                    {
                        "signals": [
                            {"comment_id": cid, "kind": "need", "summary": "合成需求"}
                            for cid in ids
                        ]
                    }
                    if call.stage == "signals"
                    else {"clusters": [{"comment_ids": ids, "summary": "合成需求"}]}
                )
            return response

    class SeedJudge(ExportJudge):
        def assess_context(self, req, cluster):
            if name == "unclear-object":
                return ContextDecision(
                    cluster_id=cluster.cluster_id,
                    status="media_required",
                    context_refs=[],
                    reason="对象不明",
                )
            return super().assess_context(req, cluster)

        def assess_opportunity(self, *args):
            assessment = super().assess_opportunity(*args)
            return replace(assessment, light_operations=name != "heavy-operations")

    manifest = demo_manifest(request)
    gateway = SeedGateway(
        "no_signal" if name in ("no-need", "prompt-injection") else "valid_pipeline"
    )
    m2 = analyze_m2(
        request,
        manifest,
        plan_sampling(manifest, request.comments, request.budget),
        gateway,
        InMemorySemanticCache(),
    )
    store = MemoryIntentStore()
    outcome = run_h0(request, m2, SeedJudge(True), store)
    expected = {
        "unclear-object": "context_insufficient",
        "heavy-operations": "no_opportunity",
        "product-defect": "committed",
        "no-need": "no_opportunity",
        "prompt-injection": "no_opportunity",
    }
    assert outcome.status == expected[name]
    assert len(store.intents) == int(name == "product-defect")
