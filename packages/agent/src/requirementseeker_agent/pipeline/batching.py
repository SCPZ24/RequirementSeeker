"""稳定估算 Token，并确定性规划信号提取与合并批次。"""

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
    """说明规划失败归因于输入 Token 或调用次数限制。"""

    def __init__(self, resource: BatchResource, reason: str) -> None:
        super().__init__(reason)
        self.resource = resource
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CommentBatch:
    """同一视频的一组评论及其保守输入估算。"""

    batch_id: str
    platform: str
    video_id: str
    comment_ids: tuple[str, ...]
    estimated_input_tokens: int


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """完整的信号提取计划，并显式保留后续合并预算。"""

    signal_input_limit: int
    merge_input_reserve: int
    per_call_input_limit: int
    estimated_signal_input_tokens: int
    batches: tuple[CommentBatch, ...]


@dataclass(frozen=True, slots=True)
class MergeItem:
    """等待同视频归并的已验证中间结果。"""

    item_id: str
    video_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MergeBatch:
    """一次合并调用包含的稳定条目 ID 与输入估算。"""

    item_ids: tuple[str, ...]
    estimated_input_tokens: int


def estimate_text_tokens(text: str) -> int:
    """不绑定具体模型分词器，按 UTF-8 字节数保守估算 Token。"""
    normalized = unicodedata.normalize("NFC", text)
    return max(1, ceil(len(normalized.encode("utf-8")) / 3))


def estimate_comment_tokens(comment: Comment) -> int:
    """对评论契约的规范 JSON 估算输入量，避免字段顺序影响结果。"""

    canonical = json.dumps(
        comment.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return estimate_text_tokens(canonical)


def split_input_budget(total: int) -> tuple[int, int]:
    """把累计输入预算按 75% 信号提取、25% 合并进行硬隔离。"""

    if total < 0:
        raise ValueError("input_budget_must_be_non_negative")
    merge = ceil(total * 0.25)
    return total - merge, merge


def _batch_id(platform: str, video_id: str, comment_ids: tuple[str, ...]) -> str:
    """由平台、视频和有序评论 ID 生成可复现的批次 ID。"""

    canonical = "\x1f".join((platform, video_id, *comment_ids)).encode()
    return f"batch_{hashlib.sha256(canonical).hexdigest()[:24]}"


class BatchPlanner:
    """在运行总预算和适配器单次能力之间规划首次适配批次。"""

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
        """为一个视频的评论规划信号提取调用，并预留至少一次合并调用。"""

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
        # 即使信号批次可占满调用上限，也必须为最终合并保留一次调用。
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

        # 稳定排序后采用首次适配，使输入顺序变化不会改变批次边界和 ID。
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
        """规划一层合并批次；多层归并由上层管线重复调用本方法。"""

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
        # 使用与信号批次相同的确定性首次适配策略。
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
