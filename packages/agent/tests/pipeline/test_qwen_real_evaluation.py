import json

import pytest
from model.test_qwen import MODEL, REVISION, fixture, gateway

from requirementseeker_agent.model import ScenarioModelGateway
from requirementseeker_agent.pipeline import (
    RealEvaluationPreflightError,
    analyze_m2,
    analyze_m2_real_evaluation,
    m2,
)
from requirementseeker_agent.runtime import InMemorySemanticCache

from .test_m2 import inputs


def frozen_inputs(**changes):
    request, manifest, plan = inputs()
    return (
        request.model_copy(
            update={
                "model": request.model.model_copy(
                    update={
                        "model_name": MODEL,
                        "revision": REVISION,
                        **changes,
                    }
                )
            }
        ),
        manifest,
        plan,
    )


def transport_fixture(captured, *, wrong_stage=None, name=MODEL, missing=False):
    def transport(request, timeout):
        captured.append(request)
        messages = json.loads(request.data)["messages"]
        source = json.loads(messages[1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
        stage = "signals" if "comments" in source else "cluster"
        payload = (
            {
                "signals": [
                    {"comment_id": comment["comment_id"], "kind": "need", "summary": "批量导出"}
                    for comment in source["comments"]
                ]
            }
            if stage == "signals"
            else {"clusters": [{"comment_ids": ["c1", "c2", "c3"], "summary": "批量导出"}]}
        )
        body = fixture()
        body["choices"][0]["message"]["content"] = json.dumps(payload, ensure_ascii=False)
        if stage == wrong_stage:
            if missing:
                del body["model"]
            else:
                body["model"] = name
        return json.dumps(body, ensure_ascii=False).encode()

    return transport


def test_two_stage_real_gateway_audits_and_cache_replay(monkeypatch):
    request, manifest, plan = frozen_inputs()
    captured, parts = [], []
    original = m2.cache_key

    def capture(value):
        parts.append(value)
        return original(value)

    monkeypatch.setattr(m2, "cache_key", capture)
    adapter = gateway(transport_fixture(captured))
    cache = InMemorySemanticCache()
    first = analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    replay = analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert first.status == replay.status == "completed", first.error_code
    assert len(captured) == len(first.audits) == 2
    for audit in first.audits:
        assert audit.requested_model_name == audit.actual_model_name == MODEL
        assert audit.requested_revision == audit.actual_revision == REVISION
    assert [event.status for event in replay.cache_events] == ["hit", "hit"]
    assert replay.audits == []
    assert {part.stage for part in parts} == {"signals", "cluster"}
    for part in parts:
        assert part.model_name == MODEL and part.model_revision == REVISION
        assert part.identity_verification_required is True
        assert (
            original(part).digest
            != original(part.model_copy(update={"model_revision": "2025-12-02"})).digest
        )


@pytest.mark.parametrize("stage", ["signals", "cluster"])
@pytest.mark.parametrize("name", ["qwen-plus", "qwen-plus-2026-01-01", "other-model"])
def test_unknown_response_identity_fatal_without_retry_or_failed_stage_cache(stage, name):
    request, manifest, plan = frozen_inputs()
    captured = []
    adapter = gateway(transport_fixture(captured, wrong_stage=stage, name=name))
    cache = InMemorySemanticCache()
    first = analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    second = analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert first.status == second.status == "fatal_error"
    assert first.error_code == second.error_code == "model_identity_unverifiable"
    assert first.clusters == [] and second.clusters == []
    assert len(first.audits) == (1 if stage == "signals" else 2)
    audit = first.audits[-1]
    assert audit.requested_model_name == MODEL and audit.requested_revision == REVISION
    assert audit.actual_model_name == name and audit.actual_revision is None
    assert audit.status == "error" and audit.usage.total_tokens == 15
    assert [event.status for event in second.cache_events] == (
        ["miss"] if stage == "signals" else ["hit", "miss"]
    )
    assert len(captured) == (2 if stage == "signals" else 3)


@pytest.mark.parametrize("stage", ["signals", "cluster"])
def test_missing_identity_audit_and_worst_case_settlement(stage):
    request, manifest, plan = frozen_inputs()
    captured = []
    adapter = gateway(transport_fixture(captured, wrong_stage=stage, missing=True))
    cache = InMemorySemanticCache()
    result = analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert result.status == "fatal_error" and result.error_code == "model_identity_unverifiable"
    audit = result.audits[-1]
    assert audit.actual_model_name is audit.actual_revision is audit.usage is None
    assert audit.requested_model_name == MODEL and audit.requested_revision == REVISION
    assert len(captured) == (1 if stage == "signals" else 2)
    assert result.budget.model_calls_reserved == 0
    assert result.budget.output_tokens_reserved == result.budget.input_tokens_reserved == 0
    # 缺少身份的响应没有可结算 usage，失败调用使用实际请求中的最坏输出预留。
    failed_max = json.loads(captured[-1].data)["max_tokens"]
    assert result.budget.output_tokens_consumed == failed_max + (5 if stage == "cluster" else 0)
    assert result.budget.input_tokens_consumed > (10 if stage == "cluster" else 0)
    replay = analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert [event.status for event in replay.cache_events] == (
        ["miss"] if stage == "signals" else ["hit", "miss"]
    )


@pytest.mark.parametrize("stage", ["signals", "cluster"])
def test_existing_pipeline_rejects_actual_revision_mismatch(stage):
    instance = gateway(transport_fixture([]))

    class RevisionMismatch(type(instance)):
        def invoke(self, call):
            result = super().invoke(call)
            return (
                result.model_copy(update={"model_revision": "2025-12-02"})
                if call.stage == stage
                else result
            )

    request, manifest, plan = frozen_inputs()
    captured = []
    adapter = RevisionMismatch(
        api_key="offline-secret",
        max_input_tokens_per_call=4096,
        transport=transport_fixture(captured),
    )
    result = analyze_m2_real_evaluation(request, manifest, plan, adapter, InMemorySemanticCache())
    assert result.status == "fatal_error" and result.error_code == "model_identity_mismatch"
    audit = result.audits[-1]
    assert audit.requested_revision == REVISION and audit.actual_revision == "2025-12-02"
    assert len(captured) == (1 if stage == "signals" else 2)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"revision": None}, "requested_revision_required"),
        ({"revision": "2025-12-02"}, "gateway_revision_mismatch"),
        ({"model_name": "qwen-plus"}, "gateway_model_mismatch"),
    ],
)
def test_preflight_rejects_request_before_http(changes, code):
    request, manifest, plan = frozen_inputs(**changes)
    adapter = gateway(lambda *_: pytest.fail("preflight must not invoke"))
    with pytest.raises(RealEvaluationPreflightError, match=code):
        analyze_m2_real_evaluation(request, manifest, plan, adapter, InMemorySemanticCache())


def test_ordinary_scenario_still_accepts_revision_none():
    request, manifest, plan = inputs()
    request.model.revision = None
    result = analyze_m2(
        request, manifest, plan, ScenarioModelGateway("valid_pipeline"), InMemorySemanticCache()
    )
    assert result.status == "completed"
