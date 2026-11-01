"""合成 M2 交接的业务出口与重放。"""

import json
from pathlib import Path

import pytest

from requirementseeker_agent.contracts import AnalysisRequest, ContextDecision, OpportunityCandidate
from requirementseeker_agent.pipeline import h0
from requirementseeker_agent.pipeline.types import M2AnalysisResult
from requirementseeker_agent.rules import evaluate_consensus

PACKAGE = Path(__file__).resolve().parents[2]


@pytest.fixture
def inputs():
    request = AnalysisRequest.model_validate_json(
        (PACKAGE / "tests/fixtures/valid/request.json").read_bytes()
    )
    m2 = M2AnalysisResult.model_validate_json((PACKAGE / "examples/h0-m2.json").read_bytes())
    return request, m2


class Judge:
    def __init__(self, context="text_sufficient", light=True):
        self.context = context
        self.light = light
        self.calls = []

    def assess_context(self, request, cluster):
        self.calls.append("context")
        return ContextDecision(
            cluster_id=cluster.cluster_id, status=self.context, reason="合成上下文", context_refs=[]
        )

    def assess_opportunity(self, request, cluster, consensus):
        self.calls.append("opportunity")
        record = json.loads((PACKAGE / "tests/fixtures/valid/result-new.json").read_bytes())
        candidate = OpportunityCandidate.model_validate(record["outcomes"][0]["candidate"])
        return h0.Assessment(
            candidate,
            software_deliverable=True,
            light_operations=self.light,
            specific_acquisition=True,
        )


def test_commits_once_for_same_evidence_across_runs(inputs):
    request, m2 = inputs
    store = h0.MemoryIntentStore()
    first = h0.run_h0(request, m2, Judge(), store)
    assert first.status == "committed"
    assert first.intent.candidate.evidence == m2.decisions[0].evidence
    request.run_id = m2.run_id = "replay"
    m2.clusters[0].comment_ids.reverse()
    second = h0.run_h0(request, m2, Judge(), store)
    assert second.status == "already_committed"
    assert second.intent.idempotency_key == first.intent.idempotency_key
    assert len(store.intents) == 1


def test_failed_consensus_never_calls_judge_or_sink(inputs):
    request, m2 = inputs
    request.comments[2].author_id = request.comments[0].author_id
    m2.decisions[0] = evaluate_consensus(request, m2.clusters[0])
    judge, store = Judge(), h0.MemoryIntentStore()
    assert h0.run_h0(request, m2, judge, store).status == "no_opportunity"
    assert not judge.calls and not store.intents


def test_context_insufficient_does_not_construct_candidate(inputs):
    judge, store = Judge(context="insufficient"), h0.MemoryIntentStore()
    assert h0.run_h0(*inputs, judge, store).status == "context_insufficient"
    assert judge.calls == ["context"] and not store.intents


def test_heavy_operations_are_business_rejection(inputs):
    store = h0.MemoryIntentStore()
    result = h0.run_h0(*inputs, Judge(light=False), store)
    assert result.status == "no_opportunity" and not store.intents


def test_upstream_cancelled_and_empty_result_are_distinct(inputs):
    request, m2 = inputs
    judge, store = Judge(), h0.MemoryIntentStore()
    data = m2.model_dump()
    data.update(clusters=[], decisions=[], signals=[], completed_steps=["preprocess"])
    stopped = M2AnalysisResult.model_validate(
        dict(data, status="cancelled", error_code="cancelled")
    )
    empty = M2AnalysisResult.model_validate(
        dict(data, status="no_signal", error_code=None, completed_steps=["preprocess", "signals"])
    )
    assert h0.run_h0(request, stopped, judge, store).status == "incomplete"
    assert h0.run_h0(request, empty, judge, store).status == "no_opportunity"
    assert not judge.calls and not store.intents


def test_forged_m2_evidence_is_not_business_rejection(inputs):
    request, m2 = inputs
    m2.decisions[0].evidence[0].text = "非来源正文"
    store = h0.MemoryIntentStore()
    result = h0.run_h0(request, m2, Judge(), store)
    assert (result.status, result.reason) == ("incomplete", "consensus_mismatch")
    assert not store.intents


def test_incomplete_history_cannot_create_intent(inputs):
    store = h0.MemoryIntentStore(history_complete=False)
    result = h0.run_h0(*inputs, Judge(), store)
    assert (result.status, result.reason) == ("incomplete", "history_incomplete")
    assert not store.intents


def test_sampling_plan_from_another_video_is_incomplete(inputs):
    from dataclasses import replace

    request, m2 = inputs
    m2.sampling_plan = replace(m2.sampling_plan, video_id="other-video")
    store = h0.MemoryIntentStore()
    assert h0.run_h0(request, m2, Judge(), store).reason == "sampling_mismatch"
    assert not store.intents


def test_media_resolved_context_is_outside_text_only_flow(inputs):
    store = h0.MemoryIntentStore()
    judge = Judge(context="context_sufficient")
    result = h0.run_h0(*inputs, judge, store)
    assert result.status == "incomplete" and result.reason == "unsupported_media_context"
    assert judge.calls == ["context"] and not store.intents


def test_judge_cannot_rewrite_the_evidence_used_for_admission(inputs):
    class RewritingJudge(Judge):
        def assess_opportunity(self, request, cluster, consensus):
            assessment = super().assess_opportunity(request, cluster, consensus)
            consensus.evidence[0].text = "替换后的引文"
            assessment.candidate.evidence = consensus.evidence
            return assessment

    store = h0.MemoryIntentStore()
    result = h0.run_h0(*inputs, RewritingJudge(), store)
    assert result.reason == "candidate_evidence_mismatch" and not store.intents


@pytest.mark.parametrize(
    ("status", "steps"),
    [
        ("completed", ["consensus"]),
        ("completed", ["preprocess", "signals", "consensus", "cluster"]),
        ("no_signal", ["signals"]),
        ("no_signal", ["signals", "preprocess"]),
        ("no_signal", ["preprocess", "signals", "cluster"]),
    ],
)
def test_inconsistent_success_steps_stop_before_judging(inputs, status, steps):
    request, m2 = inputs
    data = m2.model_dump()
    data.update(status=status, completed_steps=steps)
    if status == "no_signal":
        data.update(signals=[], clusters=[], decisions=[])
    upstream = M2AnalysisResult.model_validate(data)
    judge, store = Judge(), h0.MemoryIntentStore()
    result = h0.run_h0(request, upstream, judge, store)
    assert (result.status, result.reason) == ("incomplete", "upstream_steps_mismatch")
    assert result.intent is None and not judge.calls and not store.intents
