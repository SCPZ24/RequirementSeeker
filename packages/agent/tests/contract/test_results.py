import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

FIXTURES = Path(__file__).parents[1] / "fixtures"


def contract(name):
    module = importlib.import_module("requirementseeker_agent")
    assert hasattr(module, name), f"Public contract {name} has not been implemented"
    return getattr(module, name)


def parse(data):
    return contract("AnalysisResult").model_validate_json(json.dumps(data))


@pytest.fixture
def result_data():
    return json.loads((FIXTURES / "valid/result-new.json").read_text(encoding="utf-8"))


def test_result_round_trip(result_data):
    assert parse(result_data).outcomes[0].status == "new_candidate"


@pytest.mark.parametrize(
    "violation", ["few", "duplicate_author", "duplicate_comment", "cross_video"]
)
def test_candidate_requires_structurally_distinct_evidence(result_data, violation):
    evidence = result_data["outcomes"][0]["candidate"]["evidence"]
    if violation == "few":
        evidence.pop()
    elif violation == "duplicate_author":
        evidence[1]["author_id"] = evidence[0]["author_id"]
    elif violation == "duplicate_comment":
        evidence[1]["comment_id"] = evidence[0]["comment_id"]
    else:
        evidence[1]["video_id"] = "other"
    with pytest.raises(ValidationError):
        parse(result_data)


def test_new_candidate_requires_candidate_payload(result_data):
    del result_data["outcomes"][0]["candidate"]
    with pytest.raises(ValidationError):
        parse(result_data)


def test_failure_cannot_smuggle_candidate_payload(result_data):
    result_data["outcomes"][0]["status"] = "fatal_error"
    with pytest.raises(ValidationError):
        parse(result_data)


def test_unknown_usage_stays_null():
    audit = contract("ModelInvocationAudit").model_validate_json(
        json.dumps(
            {
                "invocation_id": "call-1",
                "model_config_ref": "local-default",
                "model_name": "synthetic-model",
                "versions": {"rules": "1", "prompts": "1", "schema": "1.0"},
                "input_hash": "a" * 64,
                "step": "signals",
                "attempt": 1,
                "status": "success",
                "usage": None,
                "error_code": None,
            }
        )
    )
    assert audit.usage is None


def test_token_total_must_match_known_components():
    with pytest.raises(ValidationError):
        contract("TokenUsage").model_validate(
            {"input_tokens": 2, "output_tokens": 3, "total_tokens": 1}
        )


def test_false_consensus_does_not_carry_passing_evidence():
    evidence = json.loads((FIXTURES / "valid/result-new.json").read_text(encoding="utf-8"))[
        "outcomes"
    ][0]["candidate"]["evidence"]
    with pytest.raises(ValidationError):
        contract("ConsensusDecision").model_validate_json(
            json.dumps(
                {
                    "cluster_id": "cluster-1",
                    "passed": False,
                    "reason_code": "not_enough_authors",
                    "evidence": evidence,
                }
            )
        )


def test_fact_step_requires_evidence_reference():
    with pytest.raises(ValidationError):
        contract("InferenceStep").model_validate(
            {
                "step_id": "fact-1",
                "kind": "fact",
                "title": "评论证据",
                "body": "事实必须引用材料",
                "evidence_refs": [],
            }
        )


def test_result_video_must_match_candidate(result_data):
    result_data["video_id"] = "different-video"
    with pytest.raises(ValidationError):
        parse(result_data)
