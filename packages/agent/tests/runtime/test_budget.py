from collections.abc import Callable

import pytest

from requirementseeker_agent.contracts.analysis import TokenUsage
from requirementseeker_agent.contracts.requests import AnalysisBudget
from requirementseeker_agent.runtime import BudgetLedger, BudgetLimitExceeded, Reservation


def test_settlement_uses_actual_usage_and_releases_unused_reservation() -> None:
    ledger = BudgetLedger.from_analysis_budget(AnalysisBudget())
    reservation = ledger.reserve(input_tokens=100, output_tokens=50)

    ledger.settle(
        reservation,
        TokenUsage(input_tokens=80, output_tokens=20, total_tokens=100),
    )

    snapshot = ledger.snapshot()
    assert snapshot.model_calls_consumed == 1
    assert snapshot.input_tokens_consumed == 80
    assert snapshot.output_tokens_consumed == 20
    assert snapshot.input_tokens_reserved == 0


def test_missing_usage_consumes_full_reservation() -> None:
    ledger = BudgetLedger.from_analysis_budget(
        AnalysisBudget(max_model_calls=2, max_input_tokens=100, max_output_tokens=20)
    )
    reservation = ledger.reserve(input_tokens=60, output_tokens=10)

    ledger.settle(reservation, usage=None)

    snapshot = ledger.snapshot()
    assert snapshot.input_tokens_consumed == 60
    assert snapshot.output_tokens_consumed == 10


@pytest.mark.parametrize(
    ("budget", "operation", "resource"),
    [
        (AnalysisBudget(max_comments=2), lambda ledger: ledger.consume_comments(3), "comments"),
        (
            AnalysisBudget(max_model_calls=0),
            lambda ledger: ledger.reserve(input_tokens=0, output_tokens=0),
            "model_calls",
        ),
        (
            AnalysisBudget(max_input_tokens=10),
            lambda ledger: ledger.reserve(input_tokens=11, output_tokens=0),
            "input_tokens",
        ),
        (
            AnalysisBudget(max_output_tokens=10),
            lambda ledger: ledger.reserve(input_tokens=0, output_tokens=11),
            "output_tokens",
        ),
    ],
)
def test_budget_limits_fail_before_consumption(
    budget: AnalysisBudget, operation: Callable[[BudgetLedger], object], resource: str
) -> None:
    ledger = BudgetLedger.from_analysis_budget(budget)

    with pytest.raises(BudgetLimitExceeded) as raised:
        operation(ledger)

    assert raised.value.resource == resource
    assert ledger.snapshot().model_calls_consumed == 0


def test_active_reservation_blocks_a_second_call_until_settled() -> None:
    ledger = BudgetLedger.from_analysis_budget(
        AnalysisBudget(max_model_calls=1, max_input_tokens=100, max_output_tokens=20)
    )
    reservation = ledger.reserve(input_tokens=100, output_tokens=20)

    with pytest.raises(BudgetLimitExceeded, match="model_calls"):
        ledger.reserve(input_tokens=1, output_tokens=1)

    ledger.settle(reservation, TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15))
    assert ledger.snapshot().model_calls_consumed == 1


def test_reservation_cannot_be_settled_twice() -> None:
    ledger = BudgetLedger.from_analysis_budget(AnalysisBudget())
    reservation = ledger.reserve(input_tokens=10, output_tokens=10)
    ledger.settle(reservation, usage=None)

    with pytest.raises(ValueError, match="reservation_not_active"):
        ledger.settle(reservation, usage=None)


def test_forged_reservation_does_not_remove_the_active_reservation() -> None:
    ledger = BudgetLedger.from_analysis_budget(AnalysisBudget())
    reservation = ledger.reserve(input_tokens=10, output_tokens=10)
    forged = Reservation(reservation.reservation_id, input_tokens=1, output_tokens=1)

    with pytest.raises(ValueError, match="reservation_not_active"):
        ledger.settle(forged, usage=None)

    ledger.settle(reservation, usage=None)
    assert ledger.snapshot().input_tokens_consumed == 10
