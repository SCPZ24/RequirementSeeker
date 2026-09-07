import json
from copy import deepcopy
from pathlib import Path

import pytest

from requirementseeker_agent import AnalysisRequest, NeedCluster
from requirementseeker_agent.rules import evaluate_consensus

FIXTURE = Path(__file__).parents[1] / "fixtures" / "valid" / "request.json"


def request_data() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def parse_request(data: dict[str, object] | None = None) -> AnalysisRequest:
    return AnalysisRequest.model_validate(data or request_data())


def cluster(*comment_ids: str) -> NeedCluster:
    return NeedCluster(cluster_id="cluster-1", comment_ids=list(comment_ids), summary="合成需求簇")


def cluster_from(request: AnalysisRequest) -> NeedCluster:
    return cluster(*(comment.comment_id for comment in request.comments))


def test_three_distinct_non_video_authors_pass() -> None:
    request = parse_request()

    decision = evaluate_consensus(request, cluster_from(request))

    assert decision.passed is True
    assert decision.reason_code == "independent_author_threshold_met"
    assert [item.comment_id for item in decision.evidence] == [
        "comment-1",
        "comment-2",
        "comment-3",
    ]


@pytest.mark.parametrize("kind", ["duplicate", "missing", "video_author"])
def test_ineligible_authors_do_not_count(kind: str) -> None:
    data = request_data()
    comments = data["comments"]
    assert isinstance(comments, list)
    if kind == "duplicate":
        comments[1]["author_id"] = comments[0]["author_id"]
    elif kind == "missing":
        comments[2]["author_id"] = None
    else:
        video = data["video"]
        assert isinstance(video, dict)
        comments[2]["author_id"] = video["author_id"]
    request = parse_request(data)

    decision = evaluate_consensus(request, cluster_from(request))

    assert decision.passed is False
    assert decision.reason_code == "insufficient_independent_authors"
    assert decision.evidence == []


def test_unknown_video_author_fails_closed() -> None:
    data = request_data()
    video = data["video"]
    assert isinstance(video, dict)
    video["author_id"] = None
    request = parse_request(data)

    decision = evaluate_consensus(request, cluster_from(request))

    assert decision.passed is False
    assert decision.reason_code == "video_author_unknown"
    assert decision.evidence == []


def test_unknown_cluster_comment_fails_closed() -> None:
    request = parse_request()

    decision = evaluate_consensus(request, cluster("comment-1", "missing-comment"))

    assert decision.passed is False
    assert decision.reason_code == "cluster_references_unknown_comment"
    assert decision.evidence == []


def test_representative_selection_is_independent_of_cluster_order() -> None:
    data = request_data()
    comments = data["comments"]
    assert isinstance(comments, list)
    earlier_duplicate = deepcopy(comments[0])
    earlier_duplicate.update(
        {
            "comment_id": "comment-0",
            "first_collected_at": "2026-09-04T23:00:00Z",
            "expires_at": "2026-09-14T23:00:00Z",
        }
    )
    comments.append(earlier_duplicate)
    request = parse_request(data)
    forward = cluster("comment-1", "comment-2", "comment-3", "comment-0")
    reverse = cluster(*reversed(forward.comment_ids))

    first = evaluate_consensus(request, forward)
    second = evaluate_consensus(request, reverse)

    assert first == second
    assert [item.comment_id for item in first.evidence] == [
        "comment-0",
        "comment-2",
        "comment-3",
    ]


def test_evidence_preserves_trusted_comment_text_exactly() -> None:
    data = request_data()
    comments = data["comments"]
    assert isinstance(comments, list)
    comments[0]["text"] = "  保留可信原文与空格。\n"
    request = parse_request(data)

    decision = evaluate_consensus(request, cluster_from(request))

    assert decision.evidence[0].text == "  保留可信原文与空格。\n"
