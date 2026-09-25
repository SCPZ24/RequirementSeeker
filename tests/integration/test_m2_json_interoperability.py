"""Run: uv run --offline --locked --project packages/dataset-tools pytest tests/integration/test_m2_json_interoperability.py -q."""

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for package in ("collector", "dataset-tools", "agent"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

from requirementseeker_agent.contracts.sampling import SamplingManifest as AgentManifest
from requirementseeker_agent.sampling.policy import assess_quality
from requirementseeker_collector.contracts import CollectionRecord
from requirementseeker_dataset.contracts import SamplingManifest as DatasetManifest
from requirementseeker_dataset.sanitize import sanitize_root
from requirementseeker_dataset.source import CollectionInput


@pytest.mark.parametrize("count", [2, None])
def test_collector_json_is_accepted_by_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int | None
) -> None:
    fixture = (
        ROOT
        / "packages/dataset-tools/tests/fixtures/raw/valid/bilibili/BVfake/collection.json"
    )
    data = json.loads(fixture.read_text(encoding="utf-8"))
    if count is not None:
        data.update(collection_schema_version="1.1", exact_duplicate_count=count)
    collector_record = CollectionRecord.model_validate(data)

    serialized = collector_record.model_dump_json()
    dataset_input = CollectionInput.model_validate_json(serialized)

    assert dataset_input.exact_duplicate_count == count
    assert dataset_input.collection_schema_version == (
        "1.1" if count is not None else None
    )

    raw_video = tmp_path / "raw/bilibili/BVfake"
    shutil.copytree(fixture.parent, raw_video)
    (raw_video / "collection.json").write_text(serialized, encoding="utf-8")
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", "local-test-secret-at-least-32-bytes")
    plan = ROOT / "packages/dataset-tools/tests/fixtures/approved-manifest.json"
    output = sanitize_root(
        tmp_path / "raw", plan, tmp_path / "sanitized", "RS_DATASET_TEST_SECRET"
    )
    manifest = AgentManifest.model_validate_json(
        output.sampling_manifests[0].read_bytes()
    )

    assert manifest.sampling_schema_version == "1.1"
    assert manifest.exact_duplicate_count == count
    if count is None:
        quality = assess_quality(manifest)
        assert quality.status in {"degraded", "insufficient"}
        assert quality.duplicate_rate is None


def test_dataset_manifest_json_is_accepted_by_agent_with_provenance() -> None:
    ids = [f"comment_{index:032x}" for index in range(100)]
    data = {
        "sampling_schema_version": "1.1",
        "manifest_id": "synthetic-manifest",
        "platform": "bilibili",
        "video_id": f"video_{1:032x}",
        "captured_at": "2026-09-09T01:00:00Z",
        "reported_total": 100,
        "collection_target": 100,
        "collected_total": 102,
        "pages_requested": 10,
        "pages_succeeded": 10,
        "available_strata": ["top", "recent"],
        "direction": "software_tool",
        "author_id_present": 100,
        "distinct_author_count": 100,
        "exact_duplicate_count": 2,
        "normalized_duplicate_count": 0,
        "video_metrics": None,
        "candidate_comment_ids": ids,
        "stratum_comment_ids": {"top": ids[:50], "recent": ids[50:]},
    }
    known = DatasetManifest.model_validate(data)
    unknown = DatasetManifest.model_validate(
        {**data, "collected_total": 100, "exact_duplicate_count": None}
    )

    known_quality = assess_quality(
        AgentManifest.model_validate_json(known.model_dump_json())
    )
    unknown_quality = assess_quality(
        AgentManifest.model_validate_json(unknown.model_dump_json())
    )

    assert known_quality.status == "usable"
    assert known_quality.duplicate_rate == pytest.approx(2 / 102)
    assert unknown_quality.status == "degraded"
    assert unknown_quality.duplicate_rate is None
