"""Request boundary tests: these do not perform semantic consensus analysis."""

import importlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

FIXTURES = Path(__file__).parents[1] / "fixtures"


def contract(name):
    module = importlib.import_module("requirementseeker_agent")
    assert hasattr(module, name), f"Public contract {name} has not been implemented"
    return getattr(module, name)


@pytest.fixture
def request_data():
    return json.loads((FIXTURES / "valid/request.json").read_text(encoding="utf-8"))


def parse(data):
    return contract("AnalysisRequest").model_validate_json(json.dumps(data))


def test_request_round_trip_keeps_comment_text(request_data):
    request_data["comments"][0]["text"] = "  保留原文与空格。\n"
    result = parse(request_data)
    assert json.loads(result.model_dump_json())["comments"][0]["text"] == "  保留原文与空格。\n"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "2.0"),
        (("video", "platform"), "youtube"),
        (("video", "video_id"), "  "),
        (("video", "source_url"), "file:///private/data"),
        (("analysis_time",), "2026-09-05T12:00:00"),
        (("analysis_time",), 1788610000),
        (("budget", "max_comments"), "200"),
        (("budget", "max_comments"), True),
        (("budget", "max_model_calls"), -1),
        (("model", "supports_images"), "false"),
        (("snapshot_kind",), "delta"),
    ],
)
def test_invalid_request_fields_are_rejected(request_data, path, value):
    target = request_data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        parse(request_data)


@pytest.mark.parametrize("key", ["api_key", "tag_weights", "lifecycle"])
def test_unknown_or_host_owned_fields_are_rejected(request_data, key):
    request_data[key] = "not-accepted"
    with pytest.raises(ValidationError):
        parse(request_data)


def test_missing_author_is_explicit_and_structurally_valid(request_data):
    request_data["comments"][0]["author_id"] = None
    assert parse(request_data).comments[0].author_id is None


def test_duplicate_author_is_valid_input_not_automatic_consensus(request_data):
    request_data["comments"][1]["author_id"] = request_data["comments"][0]["author_id"]
    assert len(parse(request_data).comments) == 3


@pytest.mark.parametrize(
    "violation", ["cross_video", "cross_platform", "duplicate_id", "expiry", "expired", "future"]
)
def test_snapshot_relationships_are_checked(request_data, violation):
    comment = request_data["comments"][0]
    if violation == "cross_video":
        comment["video_id"] = "other"
    elif violation == "cross_platform":
        comment["platform"] = "douyin"
    elif violation == "duplicate_id":
        request_data["comments"].append(deepcopy(comment))
    elif violation == "expiry":
        comment["expires_at"] = "2026-09-16T00:00:00Z"
    elif violation == "expired":
        request_data["analysis_time"] = comment["expires_at"]
    else:
        request_data["analysis_time"] = "2026-09-01T00:00:00Z"
    with pytest.raises(ValidationError):
        parse(request_data)


def test_timestamps_normalize_to_utc(request_data):
    request_data["analysis_time"] = "2026-09-05T20:00:00+08:00"
    assert parse(request_data).analysis_time.isoformat() == "2026-09-05T12:00:00+00:00"


def test_budget_cannot_be_smaller_than_supplied_snapshot(request_data):
    request_data["budget"]["max_comments"] = 2
    with pytest.raises(ValidationError):
        parse(request_data)


@pytest.mark.parametrize("kind", ["comment", "media"])
def test_retention_near_maximum_datetime_is_validation_failure(request_data, kind):
    stamp = "9999-12-31T00:00:00Z"
    if kind == "comment":
        request_data["comments"][0]["first_collected_at"] = stamp
        request_data["comments"][0]["expires_at"] = stamp
    else:
        request_data["media_context"] = [
            {
                "context_id": "context-1",
                "kind": "ocr",
                "source_ref": "source-1",
                "text": "synthetic context",
                "asset_ref": None,
                "first_collected_at": stamp,
                "expires_at": "9999-12-31T01:00:00Z",
            }
        ]
    with pytest.raises(ValidationError):
        parse(request_data)


def test_model_config_example_matches_request_contract(request_data):
    path = FIXTURES.parents[1] / "examples/model-config.example.json"
    request_data["model"] = json.loads(path.read_text(encoding="utf-8"))
    assert parse(request_data).model.config_ref == "local-default"
