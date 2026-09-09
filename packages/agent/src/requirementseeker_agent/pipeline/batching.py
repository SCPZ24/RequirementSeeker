"""Stable token estimates and deterministic signal/merge batch planning."""

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Literal

from ..contracts.requests import Comment

BatchResource = Literal["input_tokens", "model_calls"]


class BatchPlanningError(RuntimeError):
    def __init__(self, resource: BatchResource, reason: str) -> None:
        super().__init__(reason)
        self.resource = resource
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CommentBatch:
    batch_id: str
    platform: str
    video_id: str
    comment_ids: tuple[str, ...]
    estimated_input_tokens: int


@dataclass(frozen=True, slots=True)
class BatchPlan:
    signal_input_limit: int
    merge_input_reserve: int
    per_call_input_limit: int
    estimated_signal_input_tokens: int
    batches: tuple[CommentBatch, ...]


@dataclass(frozen=True, slots=True)
class MergeItem:
    item_id: str
    video_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MergeBatch:
    item_ids: tuple[str, ...]
    estimated_input_tokens: int


def estimate_text_tokens(text: str) -> int:
    """Estimate UTF-8 content conservatively without binding to a model tokenizer."""
    normalized = unicodedata.normalize("NFC", text)
    return max(1, ceil(len(normalized.encode("utf-8")) / 3))


def estimate_comment_tokens(comment: Comment) -> int:
    canonical = json.dumps(
        comment.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return estimate_text_tokens(canonical)


def split_input_budget(total: int) -> tuple[int, int]:
    if total < 0:
        raise ValueError("input_budget_must_be_non_negative")
    merge = ceil(total * 0.25)
    return total - merge, merge


def _batch_id(platform: str, video_id: str, comment_ids: tuple[str, ...]) -> str:
    canonical = "\x1f".join((platform, video_id, *comment_ids)).encode()
    return f"batch_{hashlib.sha256(canonical).hexdigest()[:24]}"


class BatchPlanner:
    def __init__(
        self,
        max_input_tokens: int,
        max_calls: int,
        max_input_tokens_per_call: int,
        fixed_input_tokens: int,
    ) -> None:
        if min(max_input_tokens, max_calls, fixed_input_tokens) < 0:
            raise ValueError("batch_limits_must_be_non_negative")
        if max_input_tokens_per_call <= 0:
            raise ValueError("per_call_input_limit_must_be_positive")
        self._signal_limit, self._merge_reserve = split_input_budget(max_input_tokens)
        self._max_calls = max_calls
        self._per_call_limit = max_input_tokens_per_call
        self._fixed_tokens = fixed_input_tokens

    def plan(self, comments: Sequence[Comment]) -> BatchPlan:
        if not comments:
            return BatchPlan(
                self._signal_limit,
                self._merge_reserve,
                self._per_call_limit,
                0,
                (),
            )
        videos = {(comment.platform, comment.video_id) for comment in comments}
        if len(videos) != 1:
            raise BatchPlanningError("input_tokens", "comments_must_reference_one_video")
        ids = [comment.comment_id for comment in comments]
        if len(ids) != len(set(ids)):
            raise BatchPlanningError("input_tokens", "comment_ids_must_be_unique")
        platform, video_id = next(iter(videos))
        per_batch_limit = min(self._per_call_limit, self._signal_limit)
        max_signal_calls = max(0, self._max_calls - 1)
        batches: list[CommentBatch] = []
        current_ids: list[str] = []
        current_tokens = self._fixed_tokens

        def close_batch() -> None:
            nonlocal current_ids, current_tokens
            members = tuple(current_ids)
            batches.append(
                CommentBatch(
                    _batch_id(platform, video_id, members),
                    platform,
                    video_id,
                    members,
                    current_tokens,
                )
            )
            current_ids = []
            current_tokens = self._fixed_tokens

        for item in sorted(comments, key=lambda value: value.comment_id):
            item_tokens = estimate_comment_tokens(item)
            if self._fixed_tokens + item_tokens > per_batch_limit:
                raise BatchPlanningError(
                    "input_tokens", "comment_exceeds_per_call_input_limit"
                )
            if current_ids and current_tokens + item_tokens > per_batch_limit:
                close_batch()
            current_ids.append(item.comment_id)
            current_tokens += item_tokens
        if current_ids:
            close_batch()

        if len(batches) > max_signal_calls:
            raise BatchPlanningError("model_calls", "signal_batch_call_limit_exhausted")
        total = sum(batch.estimated_input_tokens for batch in batches)
        if total > self._signal_limit:
            raise BatchPlanningError("input_tokens", "signal_input_budget_exhausted")
        return BatchPlan(
            self._signal_limit,
            self._merge_reserve,
            self._per_call_limit,
            total,
            tuple(batches),
        )

    def plan_merge(
        self,
        items: Sequence[MergeItem],
        *,
        calls_already_planned: int,
    ) -> tuple[MergeBatch, ...]:
        if not items:
            return ()
        if len({item.video_id for item in items}) != 1:
            raise BatchPlanningError("input_tokens", "merge_items_must_reference_one_video")
        ids = [item.item_id for item in items]
        if len(ids) != len(set(ids)):
            raise BatchPlanningError("input_tokens", "merge_item_ids_must_be_unique")
        per_batch_limit = min(self._per_call_limit, self._merge_reserve)
        batches: list[MergeBatch] = []
        current_ids: list[str] = []
        current_tokens = self._fixed_tokens
        for item in sorted(items, key=lambda value: value.item_id):
            item_tokens = estimate_text_tokens(f"{item.item_id}\x1f{item.text}")
            if self._fixed_tokens + item_tokens > per_batch_limit:
                raise BatchPlanningError("input_tokens", "merge_item_exceeds_input_limit")
            if current_ids and current_tokens + item_tokens > per_batch_limit:
                batches.append(MergeBatch(tuple(current_ids), current_tokens))
                current_ids = []
                current_tokens = self._fixed_tokens
            current_ids.append(item.item_id)
            current_tokens += item_tokens
        if current_ids:
            batches.append(MergeBatch(tuple(current_ids), current_tokens))
        if calls_already_planned + len(batches) > self._max_calls:
            raise BatchPlanningError("model_calls", "merge_batch_call_limit_exhausted")
        if sum(batch.estimated_input_tokens for batch in batches) > self._merge_reserve:
            raise BatchPlanningError("input_tokens", "merge_input_budget_exhausted")
        return tuple(batches)
