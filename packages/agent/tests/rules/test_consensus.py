import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from requirementseeker_agent import AnalysisRequest, NeedCluster
from requirementseeker_agent.contracts.requests import Comment
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


class SnapshotStore:
    """Test substitute for the host-owned store that assembles active snapshots."""

    def __init__(self) -> None:
        self._comments: dict[str, Comment] = {}

    def ingest(self, comments: list[Comment]) -> None:
        for comment in comments:
            existing = self._comments.get(comment.comment_id)
            if existing is not None and existing != comment:
                raise ValueError("comment_id_conflict")
            self._comments[comment.comment_id] = comment

    def active_at(self, analysis_time: datetime) -> list[Comment]:
        return sorted(
            (
                comment
                for comment in self._comments.values()
                if comment.first_collected_at <= analysis_time < comment.expires_at
            ),
            key=lambda comment: (comment.first_collected_at, comment.comment_id),
        )


def request_at(analysis_time: datetime, comments: list[Comment]) -> AnalysisRequest:
    data = request_data()
    data["analysis_time"] = analysis_time.isoformat()
    data["comments"] = [comment.model_dump(mode="json") for comment in comments]
    return parse_request(data)


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


def test_two_plus_one_cross_day_snapshot_passes() -> None:
    all_comments = parse_request().comments
    day_two = datetime(2026, 9, 6, tzinfo=UTC)
    store = SnapshotStore()
    store.ingest(all_comments[:2])
    store.ingest(all_comments[2:])
    request = request_at(day_two, store.active_at(day_two))

    decision = evaluate_consensus(request, cluster_from(request))

    assert decision.passed is True


def test_exact_replay_is_idempotent() -> None:
    request = parse_request()
    store = SnapshotStore()
    store.ingest(request.comments)
    store.ingest(request.comments)

    assert len(store.active_at(request.analysis_time)) == len(request.comments)


def test_expired_comments_are_not_available_for_new_consensus() -> None:
    request = parse_request()
    store = SnapshotStore()
    store.ingest(request.comments)
    after_expiry = datetime(2026, 9, 15, tzinfo=UTC)

    assert store.active_at(after_expiry) == []


def test_conflicting_replay_is_rejected() -> None:
    comment = parse_request().comments[0]
    store = SnapshotStore()
    store.ingest([comment])

    with pytest.raises(ValueError, match="comment_id_conflict"):
        store.ingest([comment.model_copy(update={"author_id": "changed-author"})])
