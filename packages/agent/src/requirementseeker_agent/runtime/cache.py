"""只保存已经通过信任校验的阶段结果的内存缓存。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..contracts.common import Contract, Hash, Identifier
from ..model.types import GatewayStage


class CacheKeyParts(Contract):
    """所有可能改变语义结果的版本和输入摘要。"""

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
    """规范化决策字段的摘要及其所属阶段。"""

    digest: str
    stage: GatewayStage


class ValidatedStageValue(Contract):
    """已经通过阶段 Schema 与可信引用校验的缓存值。"""

    stage: GatewayStage
    result_hash: Hash
    payload: dict[str, object]
    source_comment_ids: tuple[Identifier, ...]


@dataclass(frozen=True, slots=True)
class CacheEvent:
    """不暴露原始内容的缓存审计事件。"""

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
    """用排序后的规范 JSON 生成跨进程稳定的缓存键。"""

    canonical = json.dumps(
        parts.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return CacheKey(hashlib.sha256(canonical).hexdigest(), parts.stage)


class InMemorySemanticCache:
    """带阶段隔离、到期检查和副本隔离的进程内缓存。"""

    def __init__(self) -> None:
        self._entries: dict[CacheKey, _CacheEntry] = {}

    def put(
        self,
        key: CacheKey,
        value: ValidatedStageValue,
        *,
        expires_at: datetime,
    ) -> None:
        """仅接收已验证值，并保存深副本防止调用方后续篡改。"""

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
        """返回隔离副本及命中状态；到期时立即移除原条目。"""

        if key.stage != stage:
            raise ValueError("cache_stage_mismatch")
        entry = self._entries.get(key)
        if entry is None:
            return None, CacheEvent(key.digest, stage, "miss", None, now)
        if now >= entry.expires_at:
            del self._entries[key]
            return None, CacheEvent(key.digest, stage, "expired", entry.value.result_hash, now)
        # 读取也返回深副本，缓存中的可信基线不会被消费者修改。
        return entry.value.model_copy(deep=True), CacheEvent(
            key.digest, stage, "hit", entry.value.result_hash, now
        )
