from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from requirementseeker_agent.runtime import (
    CacheKeyParts,
    InMemorySemanticCache,
    ValidatedStageValue,
    cache_key,
)

NOW = datetime(2026, 9, 8, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def parts(**overrides: object) -> CacheKeyParts:
    data: dict[str, object] = {
        "stage": "signals",
        "normalized_input_hash": HASH_A,
        "sampling_policy_version": "m2.0",
        "batch_manifest_hash": HASH_B,
        "rules_version": "m1.0",
        "prompt_version": "signal-v1",
        "schema_version": "1.0",
        "model_config_ref": "fake-config",
        "model_revision": "fixture-1",
    }
    data.update(overrides)
    return CacheKeyParts.model_validate(data)


def value() -> ValidatedStageValue:
    return ValidatedStageValue(
        stage="signals",
        result_hash=HASH_A,
        payload={"signals": []},
        source_comment_ids=("c1",),
    )


def test_cache_key_is_canonical_and_changes_with_decision_versions() -> None:
    assert cache_key(parts()) == cache_key(parts())
    assert cache_key(parts()) != cache_key(parts(prompt_version="signal-v2"))
    assert cache_key(parts()) != cache_key(parts(normalized_input_hash=HASH_B))


def test_cache_key_parts_reject_run_identity() -> None:
    data = parts().model_dump(mode="json")
    data["run_id"] = "run-1"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        CacheKeyParts.model_validate(data)


def test_cache_reports_miss_then_hit_without_raw_response() -> None:
    cache = InMemorySemanticCache()
    key = cache_key(parts())

    missing, miss_event = cache.get(key, stage="signals", now=NOW)
    cache.put(key, value(), expires_at=NOW + timedelta(days=1))
    found, hit_event = cache.get(key, stage="signals", now=NOW)

    assert missing is None
    assert miss_event.status == "miss"
    assert found == value()
    assert hit_event.status == "hit"
    assert hit_event.source_result_hash == HASH_A


def test_expired_entry_is_deleted() -> None:
    cache = InMemorySemanticCache()
    key = cache_key(parts())
    cache.put(key, value(), expires_at=NOW + timedelta(seconds=1))

    expired, expired_event = cache.get(key, stage="signals", now=NOW + timedelta(seconds=1))
    missing, miss_event = cache.get(key, stage="signals", now=NOW + timedelta(seconds=2))

    assert expired is None
    assert expired_event.status == "expired"
    assert missing is None
    assert miss_event.status == "miss"


def test_cache_accepts_only_validated_stage_values() -> None:
    cache = InMemorySemanticCache()

    with pytest.raises(TypeError, match="cache_requires_validated_stage_value"):
        cache.put(cache_key(parts()), {"raw": "response"}, expires_at=NOW + timedelta(days=1))


def test_cache_rejects_naive_expiry_and_stage_mismatch() -> None:
    cache = InMemorySemanticCache()

    with pytest.raises(ValueError, match="cache_expiry_requires_timezone"):
        cache.put(cache_key(parts()), value(), expires_at=datetime(2026, 9, 9))
    with pytest.raises(ValueError, match="cache_stage_mismatch"):
        cache.get(cache_key(parts()), stage="cluster", now=NOW)


def test_cached_value_is_isolated_from_caller_mutation() -> None:
    cache = InMemorySemanticCache()
    key = cache_key(parts())
    original = value()
    cache.put(key, original, expires_at=NOW + timedelta(days=1))
    original.payload["signals"] = ["tampered"]

    first, _ = cache.get(key, stage="signals", now=NOW)
    assert first is not None
    assert first.payload == {"signals": []}
    first.payload["signals"] = ["changed-after-read"]

    second, _ = cache.get(key, stage="signals", now=NOW)
    assert second is not None
    assert second.payload == {"signals": []}
