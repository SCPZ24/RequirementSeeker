"""单次分析运行使用的保守预算预留与结算账本。"""

from dataclasses import dataclass
from typing import Literal

from ..contracts.analysis import TokenUsage
from ..contracts.requests import AnalysisBudget

BudgetResource = Literal["comments", "model_calls", "input_tokens", "output_tokens"]


class BudgetLimitExceeded(RuntimeError):
    """指出哪一种运行资源已经超过硬上限。"""

    def __init__(self, resource: BudgetResource) -> None:
        super().__init__(resource)
        self.resource = resource


@dataclass(frozen=True, slots=True)
class Reservation:
    """一次尚未结算的模型调用预算。"""

    reservation_id: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """同时展示已消费与仍被占用的预算。"""

    comments_consumed: int
    model_calls_consumed: int
    input_tokens_consumed: int
    output_tokens_consumed: int
    model_calls_reserved: int
    input_tokens_reserved: int
    output_tokens_reserved: int


class BudgetLedger:
    """先预留最坏用量，调用结束后再按可信 usage 结算。"""

    def __init__(self, limits: AnalysisBudget) -> None:
        self._limits = limits
        self._comments = 0
        self._calls = 0
        self._input = 0
        self._output = 0
        self._next_reservation_id = 1
        self._active: dict[int, Reservation] = {}

    @classmethod
    def from_analysis_budget(cls, budget: AnalysisBudget) -> "BudgetLedger":
        return cls(budget)

    def consume_comments(self, count: int) -> None:
        """登记本次运行已接纳的评论数。"""

        if count < 0:
            raise ValueError("comment_consumption_must_be_non_negative")
        if self._comments + count > self._limits.max_comments:
            raise BudgetLimitExceeded("comments")
        self._comments += count

    def reserve(self, *, input_tokens: int, output_tokens: int) -> Reservation:
        """在调用前原子检查并占用一次调用及其 Token 上限。"""

        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token_reservation_must_be_non_negative")
        reserved_calls = len(self._active)
        reserved_input = sum(item.input_tokens for item in self._active.values())
        reserved_output = sum(item.output_tokens for item in self._active.values())
        if self._calls + reserved_calls + 1 > self._limits.max_model_calls:
            raise BudgetLimitExceeded("model_calls")
        if self._input + reserved_input + input_tokens > self._limits.max_input_tokens:
            raise BudgetLimitExceeded("input_tokens")
        if self._output + reserved_output + output_tokens > self._limits.max_output_tokens:
            raise BudgetLimitExceeded("output_tokens")
        reservation = Reservation(self._next_reservation_id, input_tokens, output_tokens)
        self._next_reservation_id += 1
        self._active[reservation.reservation_id] = reservation
        return reservation

    def settle(self, reservation: Reservation, usage: TokenUsage | None) -> None:
        """完成一次调用；缺少 usage 时按原预留量保守记账。"""

        # 必须先确认仍为当前预留，再删除，防止重复或伪造结算消耗预算。
        active = self._active.get(reservation.reservation_id)
        if active != reservation:
            raise ValueError("reservation_not_active")
        del self._active[reservation.reservation_id]
        self._calls += 1
        actual_input = reservation.input_tokens if usage is None else usage.input_tokens
        actual_output = reservation.output_tokens if usage is None else usage.output_tokens
        self._input += actual_input
        self._output += actual_output
        if actual_input > reservation.input_tokens or self._input > self._limits.max_input_tokens:
            raise BudgetLimitExceeded("input_tokens")
        if (
            actual_output > reservation.output_tokens
            or self._output > self._limits.max_output_tokens
        ):
            raise BudgetLimitExceeded("output_tokens")

    def snapshot(self) -> BudgetSnapshot:
        """返回不可变快照，供审计和停止条件判断。"""

        return BudgetSnapshot(
            comments_consumed=self._comments,
            model_calls_consumed=self._calls,
            input_tokens_consumed=self._input,
            output_tokens_consumed=self._output,
            model_calls_reserved=len(self._active),
            input_tokens_reserved=sum(item.input_tokens for item in self._active.values()),
            output_tokens_reserved=sum(item.output_tokens for item in self._active.values()),
        )
