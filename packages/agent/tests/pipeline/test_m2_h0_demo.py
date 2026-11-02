import importlib
from pathlib import Path

import pytest


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "examples"))
    return importlib.import_module("m2_h0_demo")


def test_same_real_results_reach_h0_and_replay_once(demo, monkeypatch):
    # 包裹真实入口以观测对象身份，两个阶段仍执行原函数。
    results = []
    consumed = []
    analyze, consume = demo.analyze_m2, demo.run_h0

    def tracked_analyze(*args, **kwargs):
        result = analyze(*args, **kwargs)
        results.append(result)
        return result

    def tracked_consume(request, m2, judge, store):
        assert m2 is results[-1]
        assert request.run_id == m2.run_id
        consumed.append(m2)
        return consume(request, m2, judge, store)

    monkeypatch.setattr(demo, "analyze_m2", tracked_analyze)
    monkeypatch.setattr(demo, "run_h0", tracked_consume)
    rows = list(demo.scenarios())
    first, replay = rows[-2:]
    assert len(results) == len(consumed) == len(rows) == 7
    assert first["run_id"] != replay["run_id"]
    assert first["intent_key"] and first["intent_key"] == replay["intent_key"]
    for row, status in ((first, "committed"), (replay, "already_committed")):
        assert row["status"] == status and row["intent_count"] == 1
        assert row["gateway_stages"] == ["signals", "cluster"]
        assert row["judge_stages"] == ["context", "opportunity"]
        assert row["steps"] == ["preprocess", "signals", "cluster", "consensus"]


def test_negative_cases_are_distinct_and_do_not_write(demo):
    rows = list(demo.scenarios())[:-2]
    assert [(r["case"], r["m2_status"], r["status"], r["reason"]) for r in rows] == [
        ("no_signal", "no_signal", "no_opportunity", "no_signal"),
        ("no_consensus", "completed", "no_opportunity", "insufficient_independent_authors"),
        ("context_insufficient", "completed", "context_insufficient", "insufficient"),
        ("gateway_failure", "fatal_error", "incomplete", "authentication_failed"),
        ("cancelled", "cancelled", "incomplete", "cancelled"),
    ]
    assert all(r["intent_key"] is None and r["intent_count"] == 0 for r in rows)
    assert [r["judge_stages"] for r in rows] == [[], [], ["context"], [], []]
    assert [r["gateway_stages"] for r in rows] == [
        ["signals"],
        ["signals", "cluster"],
        ["signals", "cluster"],
        ["signals"],
        [],
    ]
