from datetime import UTC, datetime, timedelta

import pytest

from requirementseeker_agent.contracts.requests import Comment
from requirementseeker_agent.pipeline.batching import (
    BatchPlanner,
    BatchPlanningError,
    MergeItem,
    estimate_comment_tokens,
    estimate_text_tokens,
    split_input_budget,
)

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def comment(comment_id: str, text: str = "需要批量导出") -> Comment:
    return Comment(
        platform="bilibili",
        video_id="video-1",
        comment_id=comment_id,
        author_id=f"author-{comment_id}",
        parent_comment_id=None,
        text=text,
        first_collected_at=NOW,
        expires_at=NOW + timedelta(days=10),
    )


def test_signal_budget_reserves_twenty_five_percent_for_merge() -> None:
    assert split_input_budget(1600) == (1200, 400)


def test_text_estimator_is_utf8_conservative_and_never_zero() -> None:
    assert estimate_text_tokens("") == 1
    assert estimate_text_tokens("abc") == 1
    assert estimate_text_tokens("需求") == 2


def test_batch_members_are_stable_when_input_order_changes() -> None:
    comments = [comment("c3"), comment("c1"), comment("c2")]
    planner = BatchPlanner(
        max_input_tokens=1600,
        max_calls=8,
        max_input_tokens_per_call=4096,
        fixed_input_tokens=20,
    )

    first = planner.plan(comments)
    second = planner.plan(list(reversed(comments)))

    assert first.batches == second.batches
    assert first.batches[0].comment_ids == ("c1", "c2", "c3")
    assert first.signal_input_limit == 1200
    assert first.merge_input_reserve == 400


def test_batch_closes_at_exact_per_call_boundary() -> None:
    comments = [comment("c1"), comment("c2"), comment("c3")]
    per_comment = estimate_comment_tokens(comments[0])
    planner = BatchPlanner(
        max_input_tokens=1000,
        max_calls=4,
        max_input_tokens_per_call=10 + per_comment * 2,
        fixed_input_tokens=10,
    )

    plan = planner.plan(comments)

    assert [batch.comment_ids for batch in plan.batches] == [("c1", "c2"), ("c3",)]
    assert plan.batches[0].estimated_input_tokens == 10 + per_comment * 2


def test_single_oversized_comment_fails_before_a_batch_is_created() -> None:
    planner = BatchPlanner(
        max_input_tokens=1000,
        max_calls=4,
        max_input_tokens_per_call=20,
        fixed_input_tokens=10,
    )

    with pytest.raises(BatchPlanningError) as raised:
        planner.plan([comment("c1", "x" * 300)])

    assert raised.value.resource == "input_tokens"
    assert raised.value.reason == "comment_exceeds_per_call_input_limit"


def test_signal_batches_cannot_consume_merge_reserve() -> None:
    comments = [comment(f"c{index}", "x" * 240) for index in range(3)]
    planner = BatchPlanner(
        max_input_tokens=400,
        max_calls=8,
        max_input_tokens_per_call=200,
        fixed_input_tokens=10,
    )

    with pytest.raises(BatchPlanningError) as raised:
        planner.plan(comments)

    assert raised.value.resource == "input_tokens"
    assert raised.value.reason == "signal_input_budget_exhausted"


def test_signal_batches_keep_one_call_for_merge() -> None:
    comments = [comment(f"c{index}", "x" * 150) for index in range(4)]
    per_comment = estimate_comment_tokens(comments[0])
    planner = BatchPlanner(
        max_input_tokens=4000,
        max_calls=2,
        max_input_tokens_per_call=10 + per_comment,
        fixed_input_tokens=10,
    )

    with pytest.raises(BatchPlanningError) as raised:
        planner.plan(comments)

    assert raised.value.resource == "model_calls"
    assert raised.value.reason == "signal_batch_call_limit_exhausted"


def test_comments_must_belong_to_one_video() -> None:
    comments = [comment("c1"), comment("c2").model_copy(update={"video_id": "video-2"})]
    planner = BatchPlanner(1600, 8, 4096, 20)

    with pytest.raises(BatchPlanningError, match="comments_must_reference_one_video"):
        planner.plan(comments)


def test_merge_batches_are_stable_and_use_only_reserved_input() -> None:
    planner = BatchPlanner(1600, 8, 180, 20)
    items = [
        MergeItem("s3", "video-1", "第三条需求"),
        MergeItem("s1", "video-1", "第一条需求"),
        MergeItem("s2", "video-1", "第二条需求"),
    ]

    signal_plan = planner.plan([comment("c1")])
    merge = planner.plan_merge(items, calls_already_planned=len(signal_plan.batches))

    assert tuple(item for batch in merge for item in batch.item_ids) == ("s1", "s2", "s3")
    assert sum(batch.estimated_input_tokens for batch in merge) <= 400
