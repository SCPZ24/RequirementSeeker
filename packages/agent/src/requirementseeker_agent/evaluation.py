"""Deterministic offline evaluation helpers for Agent M2."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
from math import ceil
from typing import Literal

Adjudication = Literal["adjudicated", "disputed_pending"]
ReferenceStatus = Literal["valid", "unknown", "cross_video", "tampered", "nonexistent"]


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    development: list[str]
    calibration: list[str]
    holdout: list[str]


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    video_id: str
    comment_id: str
    predicted_signal: bool
    gold_signal: bool
    predicted_cluster_id: str | None
    gold_cluster_id: str | None
    adjudication: Adjudication = "adjudicated"
    reference_status: ReferenceStatus = "valid"
    reference_accepted: bool = True


@dataclass(frozen=True, slots=True)
class SignalMetrics:
    precision: float
    sample_count: int
    predicted_positive_count: int


@dataclass(frozen=True, slots=True)
class ClusterMetrics:
    precision: float
    recall: float
    predicted_pair_count: int
    gold_pair_count: int


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    sample_count: int
    disputed_pending_count: int
    signal_precision: float
    predicted_signal_positive_count: int
    cluster_precision: float
    cluster_recall: float
    predicted_cluster_pair_count: int
    gold_cluster_pair_count: int
    accepted_forged_reference_count: int
    signal_precision_passed: bool
    cluster_precision_passed: bool
    cluster_recall_passed: bool
    forged_reference_gate_passed: bool

    @property
    def all_gates_passed(self) -> bool:
        return (
            self.signal_precision_passed
            and self.cluster_precision_passed
            and self.cluster_recall_passed
            and self.forged_reference_gate_passed
        )


def stable_video_split(video_ids: Iterable[str]) -> DatasetSplit:
    ordered = sorted(set(video_ids), key=lambda item: sha256(item.encode()).digest())
    development_end = ceil(len(ordered) * 0.4)
    calibration_end = development_end + ceil(len(ordered) * 0.3)
    return DatasetSplit(
        ordered[:development_end],
        ordered[development_end:calibration_end],
        ordered[calibration_end:],
    )


def _validated_records(records: Iterable[EvaluationRecord]) -> list[EvaluationRecord]:
    materialized = list(records)
    identities = [(record.video_id, record.comment_id) for record in materialized]
    if len(identities) != len(set(identities)):
        raise ValueError("evaluation_records_must_have_unique_video_comment_ids")
    return materialized


def _signal_metrics(records: list[EvaluationRecord]) -> SignalMetrics:
    eligible = [record for record in records if record.adjudication != "disputed_pending"]
    predicted = [record for record in eligible if record.predicted_signal]
    true_positives = sum(record.gold_signal for record in predicted)
    precision = true_positives / len(predicted) if predicted else 0.0
    return SignalMetrics(precision, len(eligible), len(predicted))


def signal_metrics(records: Iterable[EvaluationRecord]) -> SignalMetrics:
    return _signal_metrics(_validated_records(records))


def _pairs[PairMember: Hashable](
    clusters: Iterable[set[PairMember]],
) -> set[frozenset[PairMember]]:
    return {frozenset(pair) for cluster in clusters for pair in combinations(cluster, 2)}


def _cluster_metrics[PairMember: Hashable](
    predicted: Iterable[set[PairMember]],
    gold: Iterable[set[PairMember]],
) -> ClusterMetrics:
    predicted_pairs = _pairs(predicted)
    gold_pairs = _pairs(gold)
    matched_pairs = predicted_pairs & gold_pairs
    precision = len(matched_pairs) / len(predicted_pairs) if predicted_pairs else 0.0
    recall = len(matched_pairs) / len(gold_pairs) if gold_pairs else 0.0
    return ClusterMetrics(precision, recall, len(predicted_pairs), len(gold_pairs))


def cluster_metrics(
    predicted: Iterable[set[str]],
    gold: Iterable[set[str]],
) -> ClusterMetrics:
    return _cluster_metrics(predicted, gold)


def _clusters(
    records: Iterable[EvaluationRecord],
    attribute: Literal["predicted_cluster_id", "gold_cluster_id"],
) -> list[set[tuple[str, str]]]:
    grouped: defaultdict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        cluster_id = getattr(record, attribute)
        if cluster_id is not None:
            grouped[(record.video_id, cluster_id)].add((record.video_id, record.comment_id))
    return list(grouped.values())


def evaluation_report(records: Iterable[EvaluationRecord]) -> EvaluationReport:
    materialized = _validated_records(records)
    eligible = [record for record in materialized if record.adjudication != "disputed_pending"]
    signals = _signal_metrics(materialized)
    clusters = _cluster_metrics(
        _clusters(eligible, "predicted_cluster_id"),
        _clusters(eligible, "gold_cluster_id"),
    )
    accepted_forged = sum(
        record.reference_accepted and record.reference_status != "valid" for record in materialized
    )
    return EvaluationReport(
        sample_count=signals.sample_count,
        disputed_pending_count=len(materialized) - signals.sample_count,
        signal_precision=signals.precision,
        predicted_signal_positive_count=signals.predicted_positive_count,
        cluster_precision=clusters.precision,
        cluster_recall=clusters.recall,
        predicted_cluster_pair_count=clusters.predicted_pair_count,
        gold_cluster_pair_count=clusters.gold_pair_count,
        accepted_forged_reference_count=accepted_forged,
        signal_precision_passed=signals.precision >= 0.90,
        cluster_precision_passed=clusters.precision >= 0.90,
        cluster_recall_passed=clusters.recall >= 0.75,
        forged_reference_gate_passed=accepted_forged == 0,
    )
