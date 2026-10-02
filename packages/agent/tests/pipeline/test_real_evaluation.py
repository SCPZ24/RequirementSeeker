import pytest
from pydantic import ValidationError

import requirementseeker_agent as public
from requirementseeker_agent import pipeline
from requirementseeker_agent.model import FrozenModelIdentity, ModelRuntimeIdentity
from requirementseeker_agent.model.types import ModelCallRequest, ModelCallResponse
from requirementseeker_agent.pipeline import m2
from requirementseeker_agent.pipeline.clusters import cluster_signals
from requirementseeker_agent.pipeline.invocation import InvocationFailure
from requirementseeker_agent.pipeline.preflight import RealEvaluationPreflightError
from requirementseeker_agent.runtime import InMemorySemanticCache

from .test_clusters import LimitedScriptedGateway, cluster_payload, ledger, signals_for
from .test_invocation import ScriptedGateway, analysis_request, response
from .test_m2 import CountingGateway, inputs


class VersionedGateway(CountingGateway):
    def __init__(
        self,
        *,
        name="scenario-model",
        revision="m2-fixture-1",
        stage=None,
        wrong=None,
        scenario="valid_pipeline",
        drift_call=None,
    ):
        super().__init__(scenario)
        self.runtime_identity = ModelRuntimeIdentity(model_name=name, model_revision=revision)
        self.stage = stage
        self.wrong = wrong
        self.drift_call = drift_call

    @property
    def identity(self):
        return self.runtime_identity

    def invoke(self, call: ModelCallRequest) -> ModelCallResponse:
        response = (
            super()
            .invoke(call)
            .model_copy(
                update={
                    "model_name": self.identity.model_name,
                    "model_revision": self.identity.model_revision,
                }
            )
        )
        if call.stage == self.stage and (
            self.drift_call is None or len(self.calls) == self.drift_call
        ):
            response = response.model_copy(update=self.wrong)
        return response


def frozen_inputs(**model_changes):
    request, manifest, plan = inputs()
    model = request.model.model_copy(
        update={
            "model_name": "scenario-model",
            "revision": "m2-fixture-1",
            **model_changes,
        }
    )
    return request.model_copy(update={"model": model}), manifest, plan


def test_public_real_evaluation_api():
    assert public.analyze_m2_real_evaluation is pipeline.analyze_m2_real_evaluation
    assert public.FrozenModelIdentity is FrozenModelIdentity
    assert public.RealEvaluationPreflightError is pipeline.RealEvaluationPreflightError
    assert public.preflight_real_evaluation is pipeline.preflight_real_evaluation


def test_frozen_identity_cannot_be_reassigned():
    frozen = FrozenModelIdentity(model_name="scenario-model", model_revision="m2-fixture-1")
    with pytest.raises(ValidationError, match="frozen_instance"):
        frozen.model_revision = "changed"


@pytest.mark.parametrize(
    "requested,gateway,code",
    [
        ({"revision": None}, {}, "requested_revision_required"),
        ({}, {"revision": None}, "gateway_revision_required"),
        ({}, {"name": "other"}, "gateway_model_mismatch"),
        ({}, {"revision": "other"}, "gateway_revision_mismatch"),
    ],
)
def test_preflight_failure_has_zero_calls(requested, gateway, code):
    request, manifest, plan = frozen_inputs(**requested)
    adapter = VersionedGateway(**gateway)
    with pytest.raises(RealEvaluationPreflightError, match=code):
        m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, InMemorySemanticCache())
    assert adapter.calls == []


def test_matching_identity_completes_and_replays_without_audits():
    request, manifest, plan = frozen_inputs()
    adapter = VersionedGateway()
    cache = InMemorySemanticCache()
    first = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    second = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert first.status == second.status == "completed"
    assert len(first.audits) == len(adapter.calls) == 2
    for audit in first.audits:
        assert audit.requested_model_name == audit.actual_model_name == "scenario-model"
        assert audit.requested_revision == audit.actual_revision == "m2-fixture-1"
    assert second.audits == []
    assert [event.status for event in second.cache_events] == ["hit", "hit"]


def test_unsupported_capability_stops_before_calls():
    request, manifest, plan = frozen_inputs()

    class UnsupportedGateway(VersionedGateway):
        @property
        def capabilities(self):
            return super().capabilities.model_copy(update={"supports_structured_output": False})

    adapter = UnsupportedGateway()
    with pytest.raises(RealEvaluationPreflightError, match="gateway_capability_unsupported"):
        m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, InMemorySemanticCache())
    assert adapter.calls == []


@pytest.mark.parametrize("stage", ["signals", "cluster"])
@pytest.mark.parametrize(
    "wrong,code",
    [
        ({"model_name": "other"}, "model_identity_mismatch"),
        ({"model_revision": "other"}, "model_identity_mismatch"),
        ({"model_revision": None}, "model_identity_unverifiable"),
    ],
)
def test_response_drift_fails_without_retry_or_stage_cache(stage, wrong, code):
    request, manifest, plan = frozen_inputs()
    adapter = VersionedGateway(stage=stage, wrong=wrong)
    cache = InMemorySemanticCache()
    first = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    second = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert first.status == second.status == "fatal_error"
    assert first.error_code == code
    assert first.clusters == []
    assert len(first.audits) == (1 if stage == "signals" else 2)
    assert first.audits[-1].status == "error"
    assert [event.status for event in second.cache_events] == (
        ["miss"] if stage == "signals" else ["hit", "miss"]
    )
    assert len(adapter.calls) == (2 if stage == "signals" else 3)


def test_identity_drift_on_signal_repair_is_fatal():
    request, manifest, plan = frozen_inputs()
    adapter = VersionedGateway(
        scenario="repair_pipeline", stage="signals", wrong={"model_revision": None}, drift_call=2
    )
    result = m2.analyze_m2_real_evaluation(
        request, manifest, plan, adapter, InMemorySemanticCache()
    )
    assert result.status == "fatal_error"
    assert [call.stage for call in adapter.calls] == ["signals", "signals"]


@pytest.mark.parametrize("changes", [{"revision": "revision-2"}, {"model_name": "model-2"}])
def test_changed_frozen_identity_misses_cache(changes):
    request, manifest, plan = frozen_inputs()
    cache = InMemorySemanticCache()
    m2.analyze_m2_real_evaluation(request, manifest, plan, VersionedGateway(), cache)
    request = request.model_copy(update={"model": request.model.model_copy(update=changes)})
    adapter = VersionedGateway(name=request.model.model_name, revision=request.model.revision)
    result = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert result.status == "completed"
    assert [event.status for event in result.cache_events] == ["miss", "miss"]
    assert len(adapter.calls) == 2


def test_external_request_mutation_does_not_change_frozen_calls():
    request, manifest, plan = frozen_inputs()

    class MutatingGateway(VersionedGateway):
        def invoke(self, call):
            response = super().invoke(call)
            request.model.revision = "external-mutation"
            self.runtime_identity.model_revision = "runtime-mutation"
            return response.model_copy(update={"model_revision": "m2-fixture-1"})

    adapter = MutatingGateway()
    cache = InMemorySemanticCache()
    result = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    assert result.status == "completed"
    assert {call.model_revision for call in adapter.calls} == {"m2-fixture-1"}
    assert {audit.requested_revision for audit in result.audits} == {"m2-fixture-1"}


def test_ordinary_analysis_still_accepts_no_revision():
    request, manifest, plan = frozen_inputs(revision=None)
    result = m2.analyze_m2(
        request, manifest, plan, CountingGateway("valid_pipeline"), InMemorySemanticCache()
    )
    assert result.status == "completed"


@pytest.mark.parametrize("repair", [False, True])
def test_cluster_identity_validation_reaches_repairs_and_final_merge(repair):
    request = analysis_request()
    ids = [comment.comment_id for comment in request.comments]
    wrong = response(cluster_payload(*ids)).model_copy(update={"model_revision": None})
    if repair:
        adapter = ScriptedGateway([response({"clusters": []}), wrong])
    else:
        adapter = LimitedScriptedGateway(
            [
                response(cluster_payload(*ids[:2])),
                response(cluster_payload(ids[2])),
                wrong,
            ]
        )
    frozen = FrozenModelIdentity(model_name="scenario-model", model_revision="m2-fixture-1")
    with pytest.raises(InvocationFailure, match="model_identity_unverifiable") as failure:
        cluster_signals(
            request,
            signals_for(request),
            adapter,
            ledger(request),
            max_output_tokens=200,
            frozen_identity=frozen,
        )
    assert len(failure.value.audits) == len(adapter.calls) == (2 if repair else 3)
    assert failure.value.audits[-1].status == "error"
    if repair:
        assert adapter.calls[-1].content_blocks[-1].kind == "repair"
    else:
        assert "level-2" in adapter.calls[-1].invocation_id


def test_stage_cache_key_uses_explicit_frozen_identity():
    request, _, plan = frozen_inputs()
    frozen = FrozenModelIdentity(model_name="frozen-name", model_revision="frozen-revision")
    kwargs = dict(
        stage="signals",
        normalized_input_hash="0" * 64,
        batch_manifest={},
        prompt_version="signal-v1",
    )
    first = m2._stage_cache_key(request, plan, frozen_identity=frozen, **kwargs)
    request.model.revision = "mutated-revision"
    request.model.model_name = "mutated-name"
    assert m2._stage_cache_key(request, plan, frozen_identity=frozen, **kwargs) == first
    assert m2._stage_cache_key(request, plan, **kwargs) != first


def test_ordinary_cache_also_distinguishes_model_name():
    request, manifest, plan = frozen_inputs(revision=None)
    cache = InMemorySemanticCache()
    adapter = CountingGateway("valid_pipeline")
    m2.analyze_m2(request, manifest, plan, adapter, cache)
    request.model.model_name = "another-model"
    result = m2.analyze_m2(request, manifest, plan, adapter, cache)
    assert [event.status for event in result.cache_events] == ["miss", "miss"]


@pytest.mark.parametrize("stage", ["signals", "cluster"])
def test_ordinary_cache_cannot_hide_real_evaluation_response_drift(stage):
    request, manifest, plan = frozen_inputs()
    cache = InMemorySemanticCache()
    adapter = VersionedGateway(stage=stage, wrong={"model_revision": "drifted"})
    ordinary = m2.analyze_m2(request, manifest, plan, adapter, cache)
    assert ordinary.status == "completed"
    calls_before = len(adapter.calls)

    formal = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)

    assert formal.status == "fatal_error"
    assert formal.error_code == "model_identity_mismatch"
    expected_calls = 1 if stage == "signals" else 2
    assert [event.status for event in formal.cache_events] == ["miss"] * expected_calls
    assert len(adapter.calls) == calls_before + expected_calls
    assert len(formal.audits) == expected_calls


def test_formal_cache_does_not_supply_ordinary_analysis():
    request, manifest, plan = frozen_inputs()
    cache = InMemorySemanticCache()
    adapter = VersionedGateway()
    formal = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)
    ordinary = m2.analyze_m2(request, manifest, plan, adapter, cache)
    replay = m2.analyze_m2_real_evaluation(request, manifest, plan, adapter, cache)

    assert formal.status == ordinary.status == replay.status == "completed"
    assert [event.status for event in ordinary.cache_events] == ["miss", "miss"]
    assert len(ordinary.audits) == 2
    assert [event.status for event in replay.cache_events] == ["hit", "hit"]
    assert replay.audits == []
    assert len(adapter.calls) == 4
