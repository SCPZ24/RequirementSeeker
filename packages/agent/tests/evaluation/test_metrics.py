import json
from dataclasses import replace
from pathlib import Path

import pytest

from requirementseeker_agent.evaluation import (
    EvaluationRecord,
    cluster_metrics,
    evaluation_report,
    signal_metrics,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "m2" / "scenario-videos.json"


def fixture_records() -> list[EvaluationRecord]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [EvaluationRecord(**item) for item in raw["records"]]


def test_cluster_pair_metrics() -> None:
    metrics = cluster_metrics(
        predicted=[{"c1", "c2", "c3"}],
        gold=[{"c1", "c2"}, {"c3", "c4"}],
    )

    assert metrics.precision == pytest.approx(1 / 3)
    assert metrics.recall == pytest.approx(1 / 2)
    assert metrics.predicted_pair_count == 3
    assert metrics.gold_pair_count == 2


def test_signal_precision_excludes_only_pending_disputes() -> None:
    records = [
        EvaluationRecord("v1", "c1", True, True, "p1", "g1"),
        EvaluationRecord(
            "v1",
            "c2",
            True,
            False,
            "p1",
            "g2",
            adjudication="disputed_pending",
        ),
        EvaluationRecord("v1", "c3", True, False, "p2", "g2"),
    ]

    metrics = signal_metrics(records)

    assert metrics.sample_count == 2
    assert metrics.predicted_positive_count == 2
    assert metrics.precision == pytest.approx(1 / 2)


def test_signal_metrics_rejects_identical_duplicate_comment() -> None:
    record = EvaluationRecord("v1", "c1", True, True, "p1", "g1")

    with pytest.raises(
        ValueError,
        match="evaluation_records_must_have_unique_video_comment_ids",
    ):
        signal_metrics([record, record])


def test_evaluation_report_rejects_conflicting_duplicate_comment() -> None:
    record = EvaluationRecord("v1", "c1", True, True, "p1", "g1")
    conflicting = replace(record, gold_signal=False, gold_cluster_id="g2")

    with pytest.raises(
        ValueError,
        match="evaluation_records_must_have_unique_video_comment_ids",
    ):
        evaluation_report([record, conflicting])


def test_same_comment_id_on_different_videos_is_valid() -> None:
    records = [
        EvaluationRecord("v1", "shared", True, True, None, None),
        EvaluationRecord("v2", "shared", True, True, None, None),
    ]

    report = evaluation_report(records)

    assert report.sample_count == 2


def test_fixture_drives_all_precision_first_gates() -> None:
    report = evaluation_report(fixture_records())

    assert report.sample_count == 8
    assert report.disputed_pending_count == 1
    assert report.signal_precision == pytest.approx(1.0)
    assert report.cluster_precision == pytest.approx(1.0)
    assert report.cluster_recall == pytest.approx(1.0)
    assert report.predicted_cluster_pair_count == 2
    assert report.gold_cluster_pair_count == 2
    assert report.accepted_forged_reference_count == 0
    assert report.signal_precision_passed is True
    assert report.cluster_precision_passed is True
    assert report.cluster_recall_passed is True
    assert report.forged_reference_gate_passed is True
    assert report.all_gates_passed is True


@pytest.mark.parametrize(
    "reference_status",
    ["unknown", "cross_video", "tampered", "nonexistent"],
)
def test_accepted_non_valid_reference_fails_the_safety_gate(
    reference_status: str,
) -> None:
    records = fixture_records()
    records[-1] = replace(
        records[-1],
        reference_status=reference_status,
        reference_accepted=True,
    )

    report = evaluation_report(records)

    assert report.accepted_forged_reference_count == 1
    assert report.forged_reference_gate_passed is False
    assert report.all_gates_passed is False


def signal_boundary_records(true_positive_count: int) -> list[EvaluationRecord]:
    records = [
        EvaluationRecord(
            "v-signal",
            f"c{index}",
            True,
            index < true_positive_count,
            "predicted-signal-pair" if index < 2 else None,
            "gold-signal-pair" if index < 2 else None,
        )
        for index in range(100)
    ]
    return records


@pytest.mark.parametrize(
    ("true_positive_count", "expected_precision", "expected_passed"),
    [(90, 0.90, True), (89, 0.89, False)],
)
def test_signal_precision_gate_boundary(
    true_positive_count: int,
    expected_precision: float,
    expected_passed: bool,
) -> None:
    report = evaluation_report(signal_boundary_records(true_positive_count))

    assert report.signal_precision == pytest.approx(expected_precision)
    assert report.signal_precision_passed is expected_passed
    assert report.all_gates_passed is expected_passed


def pair_boundary_records(
    *,
    predicted_pair_count: int,
    gold_pair_count: int,
) -> list[EvaluationRecord]:
    records: list[EvaluationRecord] = []
    total_pairs = max(predicted_pair_count, gold_pair_count)
    for index in range(total_pairs):
        predicted_cluster = f"predicted-{index}" if index < predicted_pair_count else None
        gold_cluster = f"gold-{index}" if index < gold_pair_count else None
        for member in range(2):
            records.append(
                EvaluationRecord(
                    "v-cluster",
                    f"c{index}-{member}",
                    True,
                    True,
                    predicted_cluster,
                    gold_cluster,
                )
            )
    return records


@pytest.mark.parametrize(
    ("gold_pair_count", "expected_precision", "expected_passed"),
    [(90, 0.90, True), (89, 0.89, False)],
)
def test_cluster_precision_gate_boundary(
    gold_pair_count: int,
    expected_precision: float,
    expected_passed: bool,
) -> None:
    report = evaluation_report(
        pair_boundary_records(predicted_pair_count=100, gold_pair_count=gold_pair_count)
    )

    assert report.cluster_precision == pytest.approx(expected_precision)
    assert report.cluster_precision_passed is expected_passed
    assert report.all_gates_passed is expected_passed


@pytest.mark.parametrize(
    ("predicted_pair_count", "expected_recall", "expected_passed"),
    [(75, 0.75, True), (74, 0.74, False)],
)
def test_cluster_recall_gate_boundary(
    predicted_pair_count: int,
    expected_recall: float,
    expected_passed: bool,
) -> None:
    report = evaluation_report(
        pair_boundary_records(predicted_pair_count=predicted_pair_count, gold_pair_count=100)
    )

    assert report.cluster_recall == pytest.approx(expected_recall)
    assert report.cluster_recall_passed is expected_passed
    assert report.all_gates_passed is expected_passed


def test_cluster_metrics_never_pairs_comments_across_videos() -> None:
    records = [
        EvaluationRecord("v1", "shared-1", True, True, "same", "same"),
        EvaluationRecord("v2", "shared-2", True, True, "same", "same"),
    ]

    report = evaluation_report(records)

    assert report.predicted_cluster_pair_count == 0
    assert report.gold_cluster_pair_count == 0


def test_structured_comment_identity_prevents_separator_collision() -> None:
    records = [
        EvaluationRecord("a", "b\x1fc", True, True, "predicted", "gold-1"),
        EvaluationRecord("a", "b\x1fd", True, True, "predicted", "gold-2"),
        EvaluationRecord("a\x1fb", "c", True, True, "predicted-1", "gold"),
        EvaluationRecord("a\x1fb", "d", True, True, "predicted-2", "gold"),
    ]

    report = evaluation_report(records)

    assert report.predicted_cluster_pair_count == 1
    assert report.gold_cluster_pair_count == 1
    assert report.cluster_precision == 0.0
    assert report.cluster_recall == 0.0
