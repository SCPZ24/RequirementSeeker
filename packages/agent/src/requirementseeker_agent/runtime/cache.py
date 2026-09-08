"""In-memory cache for stage results that have already crossed trust validation."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..contracts.common import Contract, Hash, Identifier
from ..model.types import GatewayStage


class CacheKeyParts(Contract):
    stage: GatewayStage
    normalized_input_hash: Hash
    sampling_policy_version: Identifier
    batch_manifest_hash: Hash
    rules_version: Identifier
    prompt_version: Identifier
    schema_version: Identifier
    model_config_ref: Identifier
    model_revision: Identifier | None


@dataclass(frozen=True, slots=True)
class CacheKey:
    digest: str
    stage: GatewayStage


class ValidatedStageValue(Contract):
    stage: GatewayStage
    result_hash: Hash
    payload: dict[str, object]
    source_comment_ids: tuple[Identifier, ...]


@dataclass(frozen=True, slots=True)
class CacheEvent:
    key: str
    stage: GatewayStage
    status: Literal["hit", "miss", "expired"]
    source_result_hash: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    value: ValidatedStageValue
    expires_at: datetime


def cache_key(parts: CacheKeyParts) -> CacheKey:
    canonical = json.dumps(
        parts.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return CacheKey(hashlib.sha256(canonical).hexdigest(), parts.stage)


class InMemorySemanticCache:
    def __init__(self) -> None:
        self._entries: dict[CacheKey, _CacheEntry] = {}

    def put(
        self,
        key: CacheKey,
        value: ValidatedStageValue,
        *,
        expires_at: datetime,
    ) -> None:
        if not isinstance(value, ValidatedStageValue):
            raise TypeError("cache_requires_validated_stage_value")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("cache_expiry_requires_timezone")
        if key.stage != value.stage:
            raise ValueError("cache_stage_mismatch")
        self._entries[key] = _CacheEntry(value.model_copy(deep=True), expires_at)

    def get(
        self,
        key: CacheKey,
        *,
        stage: GatewayStage,
        now: datetime,
    ) -> tuple[ValidatedStageValue | None, CacheEvent]:
        if key.stage != stage:
            raise ValueError("cache_stage_mismatch")
        entry = self._entries.get(key)
        if entry is None:
            return None, CacheEvent(key.digest, stage, "miss", None, now)
        if now >= entry.expires_at:
            del self._entries[key]
            return None, CacheEvent(
                key.digest, stage, "expired", entry.value.result_hash, now
            )
        return entry.value.model_copy(deep=True), CacheEvent(
            key.digest, stage, "hit", entry.value.result_hash, now
        )
