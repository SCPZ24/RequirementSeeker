from copy import deepcopy

import pytest
from pydantic import ValidationError

from requirementseeker_agent.contracts.sampling import SamplingManifest


def manifest_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "sampling_schema_version": "1.0",
        "manifest_id": "manifest-1",
        "platform": "bilibili",
        "video_id": "video-1",
        "captured_at": "2026-09-07T08:00:00+08:00",
        "reported_total": 120,
        "collection_target": 100,
        "collected_total": 96,
        "pages_requested": 4,
        "pages_succeeded": 4,
        "available_strata": ["top", "recent"],
        "direction": "software_tool",
        "author_id_present": 90,
        "distinct_author_count": 70,
        "exact_duplicate_count": 1,
        "normalized_duplicate_count": 2,
        "video_metrics": {"views": 5000, "likes": 250},
        "candidate_comment_ids": ["c1", "c2", "c3"],
        "stratum_comment_ids": {"top": ["c1", "c2"], "recent": ["c3"]},
    }
    data.update(overrides)
    return data


def test_manifest_accepts_a_strict_versioned_payload() -> None:
    manifest = SamplingManifest.model_validate(manifest_data())

    assert manifest.platform == "bilibili"
    assert manifest.pages_succeeded <= manifest.pages_requested
    assert manifest.video_metrics is not None
    assert manifest.video_metrics.views == 5000


def test_manifest_rejects_unknown_stratum_comment() -> None:
    with pytest.raises(ValidationError, match="stratum_comment_id_not_in_candidate_pool"):
        SamplingManifest.model_validate(
            manifest_data(stratum_comment_ids={"top": ["missing"], "recent": ["c3"]})
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"pages_succeeded": 5}, "successful_pages_exceed_requested_pages"),
        ({"author_id_present": 97}, "author_count_exceeds_collected_total"),
        ({"distinct_author_count": 91}, "distinct_author_count_exceeds_known_authors"),
        ({"exact_duplicate_count": 97}, "duplicate_count_exceeds_collected_total"),
    ],
)
def test_manifest_rejects_impossible_counts(
    override: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        SamplingManifest.model_validate(manifest_data(**override))


def test_manifest_rejects_duplicate_candidate_ids() -> None:
    with pytest.raises(ValidationError, match="candidate_comment_ids_must_be_unique"):
        SamplingManifest.model_validate(manifest_data(candidate_comment_ids=["c1", "c1", "c3"]))


def test_manifest_rejects_strata_not_declared_available() -> None:
    data = deepcopy(manifest_data())
    data["stratum_comment_ids"] = {"top": ["c1"], "long_tail": ["c2"]}

    with pytest.raises(ValidationError, match="stratum_keys_must_match_available_strata"):
        SamplingManifest.model_validate(data)


def test_manifest_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SamplingManifest.model_validate(manifest_data(cookie="secret"))
