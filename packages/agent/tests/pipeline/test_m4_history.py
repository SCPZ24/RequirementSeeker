from copy import deepcopy
from dataclasses import replace

import pytest

from requirementseeker_agent.contracts.analysis import OpportunityMergeDecision
from requirementseeker_agent.pipeline.h0 import HistoricalOpportunity, MemoryIntentStore, run_h0

from . import test_h0

inputs = test_h0.inputs


def decision(candidate, history):
    return OpportunityMergeDecision(
        candidate_id=candidate.candidate_id,
        target_opportunity_id=history[0].opportunity_id,
        reason="合成判断：用户、问题与软件方向一致",
        evidence=candidate.evidence,
    )


def existing(m2):
    old = m2.decisions[0].evidence[0].model_copy(update={"video_id": "old-video"})
    return HistoricalOpportunity(
        "existing", "个人", "小时记录", "记录遗漏", "提醒工具", (old,), "人工评估", "保留备注"
    )


@pytest.mark.parametrize("merge", [True, False])
def test_route_and_replay(inputs, merge):
    request, m2 = inputs
    history = (existing(m2),)
    before = deepcopy(history)
    calls = []

    def route(candidate, records):
        calls.append(candidate.model_copy(deep=True))
        records[0].evidence[0].text = "修改判断器自己的副本"
        return decision(candidate, records) if merge else None

    store = MemoryIntentStore(
        history=history, history_revision=request.history_revision, history_judge=route
    )
    first = run_h0(request, m2, test_h0.Judge(), store)
    request.run_id = m2.run_id = "replay"
    again = run_h0(request, m2, test_h0.Judge(), store)
    assert (first.status, again.status) == ("committed", "already_committed")
    assert again.intent == first.intent and len(calls) == len(store.intents) == 1
    assert first.intent.status == ("merge_evidence" if merge else "new_candidate")
    if merge:
        assert first.intent.merge.target_opportunity_id == history[0].opportunity_id
        assert first.intent.merge.candidate_id == calls[0].candidate_id
        assert first.intent.merge.evidence == m2.decisions[0].evidence
    assert history == before


@pytest.mark.parametrize(
    "fault",
    [
        "target",
        "candidate",
        "evidence",
        "failure",
        "malformed",
        "incomplete",
        "revision",
        "missing_judge",
        "duplicate_target",
    ],
)
def test_failure_preserves_existing_state(inputs, fault):
    request, m2 = inputs
    history = (existing(m2),)
    if fault == "duplicate_target":
        history += (replace(history[0], manual_note="另一个同 ID 记录"),)
    before = deepcopy(history)

    def bad(candidate, records):
        proposal = decision(candidate, records)
        if fault == "target":
            proposal.target_opportunity_id = "invented"
        elif fault == "candidate":
            proposal.candidate_id = "wrong"
        elif fault == "evidence":
            proposal.evidence[0].text = "伪造原文"
        elif fault == "failure":
            raise RuntimeError("synthetic")
        elif fault == "malformed":
            return {"merge": True}
        else:
            pytest.fail("不可信历史不能调用判断器")
        return proposal

    store = MemoryIntentStore(
        history=history,
        history_complete=fault != "incomplete",
        history_revision="other" if fault == "revision" else request.history_revision,
        history_judge=None if fault == "missing_judge" else bad,
    )
    outcome = run_h0(request, m2, test_h0.Judge(), store)
    assert outcome.status == "incomplete" and outcome.intent is None
    assert history == before and not store.intents


def test_incomplete_even_when_receipt_already_exists(inputs):
    request, m2 = inputs
    store = MemoryIntentStore(
        history=(existing(m2),), history_revision=request.history_revision, history_judge=decision
    )
    assert run_h0(request, m2, test_h0.Judge(), store).status == "committed"
    prior = deepcopy(store.intents)
    store.history_complete = False
    assert run_h0(request, m2, test_h0.Judge(), store).status == "incomplete"
    assert store.intents == prior
