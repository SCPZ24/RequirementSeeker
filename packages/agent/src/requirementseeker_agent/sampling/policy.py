"""确定性的评论质量门与请求创建前采样策略。"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil, floor, log2
from typing import Literal

from ..contracts.requests import AnalysisBudget, Comment
from ..contracts.sampling import SamplingManifest, SamplingStratum, VideoDirection

SAMPLING_POLICY_VERSION = "m2.0"
_STRATA: tuple[SamplingStratum, ...] = ("top", "recent", "replies", "long_tail")
_REFILL_ORDER: tuple[SamplingStratum, ...] = ("long_tail", "recent", "top", "replies")
_STRATUM_WEIGHTS: dict[SamplingStratum, float] = {
    "top": 0.35, "recent": 0.25, "replies": 0.20, "long_tail": 0.20,
}
_DIRECTION_FACTORS: dict[VideoDirection, float] = {
    "software_tool": 1.10,
    "tutorial_workflow": 1.10,
    "life_service": 1.00,
    # 当前契约无法拆分电商与营销，真实样本足够前按推广类采用保守系数。
    "ecommerce_marketing": 0.90,
    "entertainment_culture": 0.90,
    "unknown": 1.00,
}
QualityStatus = Literal["usable", "degraded", "insufficient"]


class SamplingDataInsufficient(ValueError):
    """采集数据不完整，不能自动进入语义分析。"""


@dataclass(frozen=True, slots=True)
class SamplingQuality:
    """只由可复算的采集统计得到的数据质量结论。"""

    status: QualityStatus
    target_completion: float
    page_success_rate: float
    author_completeness: float
    duplicate_rate: float | None


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    """宿主构造 AnalysisRequest v1 前使用的确定性采样结果。"""

    policy_version: str
    manifest_id: str
    platform: str
    video_id: str
    quality: SamplingQuality
    population_size: int
    population_total_unknown: bool
    target_count: int
    selected_comment_ids: list[str]
    selected_by_stratum: dict[SamplingStratum, list[str]]
    reason_codes: list[str]


def assess_quality(manifest: SamplingManifest) -> SamplingQuality:
    """仅依据确定性采集指标分级，不在此处做语义推测。"""
    completion = min(manifest.collected_total / max(manifest.collection_target, 1), 1.0)
    page_rate = manifest.pages_succeeded / max(manifest.pages_requested, 1)
    author_rate = manifest.author_id_present / max(manifest.collected_total, 1)
    trusted = (
        manifest.sampling_schema_version == "1.1" and manifest.exact_duplicate_count is not None
    )
    duplicate_rate = (
        (manifest.exact_duplicate_count + manifest.normalized_duplicate_count)
        / max(manifest.collected_total, 1)
        if trusted and manifest.exact_duplicate_count is not None
        else None
    )
    if (
        completion >= 0.90
        and page_rate >= 0.90
        and author_rate >= 0.80
        and duplicate_rate is not None
        and duplicate_rate <= 0.25
        and manifest.distinct_author_count >= 3
    ):
        status: QualityStatus = "usable"
    elif (
        completion >= 0.60
        and page_rate >= 0.60
        and author_rate >= 0.50
        and len(manifest.available_strata) >= 2
        and manifest.distinct_author_count >= 3
    ):
        status = "degraded"
    else:
        status = "insufficient"
    return SamplingQuality(status, completion, page_rate, author_rate, duplicate_rate)


def dynamic_target(
    population: int, max_comments: int, direction_factor: float, quality_factor: float
) -> int:
    """应用已批准的对数覆盖公式，同时遵守评论数量硬上限。"""
    if population <= 0 or max_comments <= 0:
        return 0
    base = min(population, ceil(32 + 14 * log2(population)), max_comments)
    return min(ceil(base * direction_factor * quality_factor), population, max_comments)


def _quotas(target: int) -> dict[SamplingStratum, int]:
    """按最大余数法分配整数配额，固定同余时的层级顺序。"""

    exact = {name: target * _STRATUM_WEIGHTS[name] for name in _STRATA}
    quotas = {name: floor(exact[name]) for name in _STRATA}
    remaining = target - sum(quotas.values())
    order = sorted(
        _STRATA, key=lambda name: (-(exact[name] - quotas[name]), _STRATA.index(name))
    )
    for name in order[:remaining]:
        quotas[name] += 1
    return quotas


def _validate_candidates(manifest: SamplingManifest, comments: Sequence[Comment]) -> None:
    """确认调用方传入的评论与清单声明的是同一份候选池。"""

    ids = [comment.comment_id for comment in comments]
    if len(ids) != len(set(ids)) or set(ids) != set(manifest.candidate_comment_ids):
        raise ValueError("candidate_comments_must_match_manifest")
    if any(
        (comment.platform, comment.video_id) != (manifest.platform, manifest.video_id)
        for comment in comments
    ):
        raise ValueError("candidate_comments_must_match_manifest_video")


def plan_sampling(
    manifest: SamplingManifest,
    candidate_comments: Sequence[Comment],
    budget: AnalysisBudget,
) -> SamplingPlan:
    """在宿主构造 AnalysisRequest v1 前选出稳定的评论 ID。"""
    _validate_candidates(manifest, candidate_comments)
    quality = assess_quality(manifest)
    if quality.status == "insufficient":
        raise SamplingDataInsufficient("sampling_data_insufficient")

    # 平台未提供评论总量时，只能以本次采集到的候选池作为可见总体。
    unknown_population = manifest.reported_total is None
    population = (
        len(candidate_comments) if manifest.reported_total is None else manifest.reported_total
    )
    quality_factor = 1.20 if quality.status == "degraded" else 1.00
    direction_factor = _DIRECTION_FACTORS[manifest.direction]
    uncapped = dynamic_target(population, population, direction_factor, quality_factor)
    target = dynamic_target(population, budget.max_comments, direction_factor, quality_factor)
    reasons: list[str] = []
    if unknown_population:
        reasons.append("population_total_unknown")
    if target < uncapped:
        reasons.append("request_comment_budget_limited")

    eligible = set().union(*manifest.stratum_comment_ids.values())
    if target > len(eligible):
        target = len(eligible)
        reasons.append("candidate_pool_limited")

    selected: set[str] = set()
    selected_by: dict[SamplingStratum, list[str]] = {name: [] for name in _STRATA}
    quotas = _quotas(target)
    # 第一轮满足各层配额；排序确保输入顺序变化不会改变结果。
    for name in _STRATA:
        for comment_id in sorted(manifest.stratum_comment_ids.get(name, [])):
            if comment_id not in selected and len(selected_by[name]) < quotas[name]:
                selected.add(comment_id)
                selected_by[name].append(comment_id)

    # 某层样本不足时按固定次序补位，同时用集合避免跨层重复选中。
    for name in _REFILL_ORDER:
        for comment_id in sorted(manifest.stratum_comment_ids.get(name, [])):
            if len(selected) == target:
                break
            if comment_id not in selected:
                selected.add(comment_id)
                selected_by[name].append(comment_id)

    return SamplingPlan(
        policy_version=SAMPLING_POLICY_VERSION,
        manifest_id=manifest.manifest_id,
        platform=manifest.platform,
        video_id=manifest.video_id,
        quality=quality,
        population_size=population,
        population_total_unknown=unknown_population,
        target_count=len(selected),
        selected_comment_ids=sorted(selected),
        selected_by_stratum=selected_by,
        reason_codes=reasons,
    )
