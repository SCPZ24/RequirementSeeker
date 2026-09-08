from datetime import UTC, datetime, timedelta

import pytest

from requirementseeker_agent.contracts.requests import AnalysisBudget, Comment
from requirementseeker_agent.contracts.sampling import SamplingManifest
from requirementseeker_agent.sampling import (
    SamplingDataInsufficient,
    assess_quality,
    dynamic_target,
    plan_sampling,
)

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def make_comments(count: int, *, video_id: str = "video-1") -> list[Comment]:
    return [
        Comment(
            platform="bilibili",
            video_id=video_id,
            comment_id=f"c{index:03d}",
            author_id=f"a{index:03d}",
            parent_comment_id=None,
            text=f"comment {index}",
            first_collected_at=NOW,
            expires_at=NOW + timedelta(days=10),
        )
        for index in range(count)
    ]


def make_manifest(
    comments: list[Comment],
    *,
    reported_total: int | None = None,
    collection_target: int | None = None,
    collected_total: int | None = None,
    pages_requested: int = 10,
    pages_succeeded: int = 10,
    author_id_present: int | None = None,
    distinct_author_count: int | None = None,
    duplicate_count: int = 0,
    direction: str = "unknown",
    strata: dict[str, list[str]] | None = None,
) -> SamplingManifest:
    count = len(comments)
    ids = [comment.comment_id for comment in comments]
    if strata is None:
        strata = {
            "top": ids[0::4],
            "recent": ids[1::4],
            "replies": ids[2::4],
            "long_tail": ids[3::4],
        }
    return SamplingManifest.model_validate(
        {
            "sampling_schema_version": "1.0",
            "manifest_id": "manifest-1",
            "platform": "bilibili",
            "video_id": "video-1",
            "captured_at": NOW,
            "reported_total": reported_total,
            "collection_target": collection_target or count,
            "collected_total": collected_total or count,
            "pages_requested": pages_requested,
            "pages_succeeded": pages_succeeded,
            "available_strata": list(strata),
            "direction": direction,
            "author_id_present": author_id_present if author_id_present is not None else count,
            "distinct_author_count": (
                distinct_author_count if distinct_author_count is not None else count
            ),
            "exact_duplicate_count": duplicate_count,
            "normalized_duplicate_count": 0,
            "video_metrics": None,
            "candidate_comment_ids": ids,
            "stratum_comment_ids": strata,
        }
    )


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (make_manifest(make_comments(100)), "usable"),
        (
            make_manifest(
                make_comments(70),
                collection_target=100,
                collected_total=100,
                pages_requested=10,
                pages_succeeded=7,
                author_id_present=60,
                distinct_author_count=40,
            ),
            "degraded",
        ),
        (
            make_manifest(
                make_comments(50),
                collection_target=100,
                pages_requested=10,
                pages_succeeded=5,
            ),
            "insufficient",
        ),
    ],
)
def test_quality_gate(manifest: SamplingManifest, expected: str) -> None:
    assert assess_quality(manifest).status == expected


def test_dynamic_target_applies_direction_quality_and_budget_caps() -> None:
    assert dynamic_target(1000, 200, 1.1, 1.0) == 190
    assert dynamic_target(1000, 80, 1.1, 1.2) == 80
    assert dynamic_target(10, 200, 1.0, 1.0) == 10


def test_plan_marks_unknown_population_and_is_input_order_invariant() -> None:
    comments = make_comments(100)
    manifest = make_manifest(comments)
    reversed_strata = {
        name: list(reversed(ids)) for name, ids in manifest.stratum_comment_ids.items()
    }
    reversed_manifest = make_manifest(comments, strata=reversed_strata)
    budget = AnalysisBudget()

    first = plan_sampling(manifest, comments, budget)
    second = plan_sampling(reversed_manifest, list(reversed(comments)), budget)

    assert first.selected_comment_ids == second.selected_comment_ids
    assert first.population_size == 100
    assert first.population_total_unknown is True
    assert "population_total_unknown" in first.reason_codes


def test_plan_uses_35_25_20_20_quotas() -> None:
    comments = make_comments(100)
    ids = [comment.comment_id for comment in comments]
    manifest = make_manifest(
        comments,
        reported_total=100,
        strata={
            "top": ids[:35],
            "recent": ids[35:60],
            "replies": ids[60:80],
            "long_tail": ids[80:],
        },
    )
    plan = plan_sampling(manifest, comments, AnalysisBudget())

    assert plan.target_count == 100
    assert {name: len(ids) for name, ids in plan.selected_by_stratum.items()} == {
        "top": 35,
        "recent": 25,
        "replies": 20,
        "long_tail": 20,
    }


def test_plan_refills_sparse_and_overlapping_strata_deterministically() -> None:
    comments = make_comments(10)
    ids = [comment.comment_id for comment in comments]
    manifest = make_manifest(
        comments,
        reported_total=10,
        strata={
            "top": ids[:1],
            "recent": ids[:4],
            "replies": ids[4:6],
            "long_tail": ids[6:],
        },
    )

    plan = plan_sampling(manifest, comments, AnalysisBudget())

    assert plan.target_count == 10
    assert plan.selected_comment_ids == sorted(ids)
    assert sum(len(values) for values in plan.selected_by_stratum.values()) == 10


def test_plan_fails_closed_for_insufficient_data() -> None:
    comments = make_comments(50)
    manifest = make_manifest(comments, collection_target=100, pages_succeeded=5)

    with pytest.raises(SamplingDataInsufficient, match="sampling_data_insufficient"):
        plan_sampling(manifest, comments, AnalysisBudget())


def test_plan_rejects_candidate_pool_mismatch() -> None:
    comments = make_comments(10)
    manifest = make_manifest(comments)

    with pytest.raises(ValueError, match="candidate_comments_must_match_manifest"):
        plan_sampling(manifest, comments[:-1], AnalysisBudget())


def test_plan_records_request_budget_truncation() -> None:
    comments = make_comments(100)
    manifest = make_manifest(comments, reported_total=1000, direction="software_tool")

    plan = plan_sampling(manifest, comments, AnalysisBudget(max_comments=80))

    assert plan.target_count == 80
    assert "request_comment_budget_limited" in plan.reason_codes
