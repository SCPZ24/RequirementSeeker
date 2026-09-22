"""编排确定性采样输入、可信信号、聚类与 M1 共识。"""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime

from ..contracts.analysis import (
    ConsensusDecision,
    ModelInvocationAudit,
    NeedCluster,
    NeedSignal,
)
from ..contracts.requests import AnalysisRequest
from ..contracts.sampling import SamplingManifest
from ..model import GatewayStage, ModelGateway
from ..rules import evaluate_consensus
from ..runtime import (
    BudgetLedger,
    BudgetLimitExceeded,
    BudgetResource,
    CacheEvent,
    CacheKey,
    CacheKeyParts,
    InMemorySemanticCache,
    ValidatedStageValue,
    cache_key,
)
from ..sampling import SAMPLING_POLICY_VERSION, SamplingPlan, assess_quality
from .batching import BatchPlanner, BatchPlanningError, CommentBatch
from .clusters import CLUSTER_PROMPT_VERSION, cluster_signals, stable_cluster_id
from .invocation import InvocationCancelled, InvocationFailure
from .signals import (
    SIGNAL_PROMPT_VERSION,
    InvalidModelOutput,
    extract_signals,
    normalize_summary,
    signal_fixed_input_tokens,
    stable_signal_id,
)
from .types import CompletedStep, M2AnalysisResult, M2Status


def ensure_sampling_inputs_match(
    request: AnalysisRequest,
    manifest: SamplingManifest,
    plan: SamplingPlan,
) -> None:
    """确认请求是由当前清单和计划选出的同一视频快照。"""

    expected_video = (request.video.platform, request.video.video_id)
    if (manifest.platform, manifest.video_id) != expected_video:
        raise ValueError("sampling_manifest_must_match_request_video")
    if (plan.platform, plan.video_id) != expected_video:
        raise ValueError("sampling_plan_must_match_request_video")
    if plan.manifest_id != manifest.manifest_id:
        raise ValueError("sampling_plan_must_match_manifest")
    if plan.policy_version != SAMPLING_POLICY_VERSION:
        raise ValueError("sampling_plan_policy_version_mismatch")
    manifest_quality = assess_quality(manifest)
    if manifest_quality.status != "insufficient" and plan.quality != manifest_quality:
        raise ValueError("sampling_plan_quality_mismatch")
    population = (
        len(manifest.candidate_comment_ids)
        if manifest.reported_total is None
        else manifest.reported_total
    )
    if plan.population_size != population or plan.population_total_unknown != (
        manifest.reported_total is None
    ):
        raise ValueError("sampling_plan_population_mismatch")
    request_ids = [comment.comment_id for comment in request.comments]
    if len(request_ids) != len(set(request_ids)) or set(request_ids) != set(
        plan.selected_comment_ids
    ):
        raise ValueError("request_comments_must_match_sampling_plan")
    if plan.target_count != len(plan.selected_comment_ids):
        raise ValueError("sampling_plan_target_must_match_selection")
    if any(comment_id not in manifest.candidate_comment_ids for comment_id in request_ids):
        raise ValueError("request_comment_not_in_sampling_manifest")
    assigned_ids = [
        comment_id
        for stratum_ids in plan.selected_by_stratum.values()
        for comment_id in stratum_ids
    ]
    if len(assigned_ids) != len(set(assigned_ids)) or set(assigned_ids) != set(
        plan.selected_comment_ids
    ):
        raise ValueError("sampling_plan_strata_must_match_selection")
    for stratum, comment_ids in plan.selected_by_stratum.items():
        allowed = manifest.stratum_comment_ids.get(stratum, [])
        if any(comment_id not in allowed for comment_id in comment_ids):
            raise ValueError("sampling_plan_stratum_member_mismatch")


def _output_limit(request: AnalysisRequest) -> int:
    if request.budget.max_model_calls == 0:
        return 0
    return request.budget.max_output_tokens // request.budget.max_model_calls


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _trusted_input_hash(
    request: AnalysisRequest,
    comment_ids: Sequence[str],
    *,
    signals: Sequence[NeedSignal] = (),
) -> str:
    comments = {comment.comment_id: comment for comment in request.comments}
    payload = {
        "video": request.video.model_dump(mode="json"),
        "comments": [
            comments[comment_id].model_dump(mode="json") for comment_id in sorted(comment_ids)
        ],
        "signals": [
            signal.model_dump(mode="json")
            for signal in sorted(signals, key=lambda item: item.signal_id)
        ],
    }
    return _canonical_hash(payload)


def _stage_cache_key(
    request: AnalysisRequest,
    plan: SamplingPlan,
    *,
    stage: GatewayStage,
    normalized_input_hash: str,
    batch_manifest: object,
    prompt_version: str,
) -> CacheKey:
    return cache_key(
        CacheKeyParts(
            stage=stage,
            normalized_input_hash=normalized_input_hash,
            sampling_policy_version=plan.policy_version,
            batch_manifest_hash=_canonical_hash(batch_manifest),
            rules_version=request.versions.rules,
            prompt_version=prompt_version,
            schema_version=request.schema_version,
            model_config_ref=request.model.config_ref,
            model_revision=request.model.revision,
        )
    )


def _cache_expiry(request: AnalysisRequest, comment_ids: Sequence[str]) -> datetime:
    comments = {comment.comment_id: comment for comment in request.comments}
    return min(comments[comment_id].expires_at for comment_id in comment_ids)


def _signal_cache_key(
    request: AnalysisRequest,
    plan: SamplingPlan,
    batch: CommentBatch,
) -> CacheKey:
    return _stage_cache_key(
        request,
        plan,
        stage="signals",
        normalized_input_hash=_trusted_input_hash(request, batch.comment_ids),
        batch_manifest={
            "batch_id": batch.batch_id,
            "comment_ids": list(batch.comment_ids),
            "estimated_input_tokens": batch.estimated_input_tokens,
        },
        prompt_version=SIGNAL_PROMPT_VERSION,
    )


def _parse_cached_signals(
    value: ValidatedStageValue,
    batch: CommentBatch,
) -> tuple[NeedSignal, ...]:
    if value.result_hash != _canonical_hash(value.payload):
        raise ValueError("cached_signal_result_hash_mismatch")
    if value.source_comment_ids != tuple(sorted(batch.comment_ids)):
        raise ValueError("cached_signal_sources_must_match_batch")
    if set(value.payload) != {"signals"} or not isinstance(value.payload["signals"], list):
        raise ValueError("cached_signal_payload_invalid")
    signals = tuple(NeedSignal.model_validate(item) for item in value.payload["signals"])
    comment_ids = [signal.comment_id for signal in signals]
    if len(comment_ids) != len(set(comment_ids)) or any(
        comment_id not in batch.comment_ids for comment_id in comment_ids
    ):
        raise ValueError("cached_signal_references_invalid_comment")
    for signal in signals:
        if signal.summary != normalize_summary(
            signal.summary
        ) or signal.signal_id != stable_signal_id(
            signal.comment_id,
            signal.kind,
            signal.summary,
            SIGNAL_PROMPT_VERSION,
        ):
            raise ValueError("cached_signal_identity_invalid")
    return signals


def _store_signals(
    cache: InMemorySemanticCache,
    key: CacheKey,
    request: AnalysisRequest,
    batch: CommentBatch,
    signals: Sequence[NeedSignal],
) -> None:
    payload: dict[str, object] = {"signals": [signal.model_dump(mode="json") for signal in signals]}
    cache.put(
        key,
        ValidatedStageValue(
            stage="signals",
            result_hash=_canonical_hash(payload),
            payload=payload,
            source_comment_ids=tuple(sorted(batch.comment_ids)),
        ),
        expires_at=_cache_expiry(request, batch.comment_ids),
    )


def _cluster_cache_key(
    request: AnalysisRequest,
    plan: SamplingPlan,
    signals: Sequence[NeedSignal],
) -> CacheKey:
    comment_ids = [signal.comment_id for signal in signals]
    return _stage_cache_key(
        request,
        plan,
        stage="cluster",
        normalized_input_hash=_trusted_input_hash(
            request,
            [comment.comment_id for comment in request.comments],
            signals=signals,
        ),
        batch_manifest={
            "signal_ids": sorted(signal.signal_id for signal in signals),
            "comment_ids": sorted(comment_ids),
        },
        prompt_version=CLUSTER_PROMPT_VERSION,
    )


def _parse_cached_clusters(
    request: AnalysisRequest,
    value: ValidatedStageValue,
    signals: Sequence[NeedSignal],
) -> tuple[tuple[NeedCluster, ...], tuple[ConsensusDecision, ...]]:
    if value.result_hash != _canonical_hash(value.payload):
        raise ValueError("cached_cluster_result_hash_mismatch")
    allowed_ids = frozenset(signal.comment_id for signal in signals)
    if value.source_comment_ids != tuple(sorted(allowed_ids)):
        raise ValueError("cached_cluster_sources_must_match_signals")
    if set(value.payload) != {"clusters"} or not isinstance(value.payload["clusters"], list):
        raise ValueError("cached_cluster_payload_invalid")
    clusters = tuple(
        sorted(
            (NeedCluster.model_validate(item) for item in value.payload["clusters"]),
            key=lambda item: item.cluster_id,
        )
    )
    flattened = [comment_id for cluster in clusters for comment_id in cluster.comment_ids]
    if len(flattened) != len(set(flattened)) or set(flattened) != allowed_ids:
        raise ValueError("cached_clusters_must_exactly_cover_signals")
    for cluster in clusters:
        if cluster.summary != normalize_summary(
            cluster.summary
        ) or cluster.cluster_id != stable_cluster_id(
            cluster.comment_ids,
            cluster.summary,
            CLUSTER_PROMPT_VERSION,
        ):
            raise ValueError("cached_cluster_identity_invalid")
    decisions = tuple(evaluate_consensus(request, cluster) for cluster in clusters)
    return clusters, decisions


def _store_clusters(
    cache: InMemorySemanticCache,
    key: CacheKey,
    request: AnalysisRequest,
    signals: Sequence[NeedSignal],
    clusters: Sequence[NeedCluster],
) -> None:
    payload: dict[str, object] = {
        "clusters": [cluster.model_dump(mode="json") for cluster in clusters]
    }
    comment_ids = sorted(signal.comment_id for signal in signals)
    cache.put(
        key,
        ValidatedStageValue(
            stage="cluster",
            result_hash=_canonical_hash(payload),
            payload=payload,
            source_comment_ids=tuple(comment_ids),
        ),
        expires_at=_cache_expiry(request, comment_ids),
    )


def _result(
    request: AnalysisRequest,
    plan: SamplingPlan,
    ledger: BudgetLedger,
    *,
    status: M2Status,
    signals: Sequence[NeedSignal] = (),
    clusters: Sequence[NeedCluster] = (),
    decisions: Sequence[ConsensusDecision] = (),
    audits: Sequence[ModelInvocationAudit] = (),
    cache_events: Sequence[CacheEvent] = (),
    completed_steps: Sequence[CompletedStep] = (),
    error_code: str | None = None,
    exhausted_resource: BudgetResource | None = None,
) -> M2AnalysisResult:
    return M2AnalysisResult(
        run_id=request.run_id,
        platform=request.video.platform,
        video_id=request.video.video_id,
        sampling_plan=plan,
        signals=list(signals),
        clusters=list(clusters),
        decisions=list(decisions),
        audits=list(audits),
        cache_events=list(cache_events),
        budget=ledger.snapshot(),
        completed_steps=list(completed_steps),
        status=status,
        error_code=error_code,
        exhausted_resource=exhausted_resource,
    )


def analyze_m2(
    request: AnalysisRequest,
    manifest: SamplingManifest,
    plan: SamplingPlan,
    gateway: ModelGateway,
    cache: InMemorySemanticCache,
) -> M2AnalysisResult:
    """执行一次离线 M2 分析，并把所有停止条件映射为稳定状态。"""

    ledger = BudgetLedger.from_analysis_budget(request.budget)
    completed: list[CompletedStep] = []
    signals: list[NeedSignal] = []
    audits: list[ModelInvocationAudit] = []
    cache_events: list[CacheEvent] = []
    try:
        ensure_sampling_inputs_match(request, manifest, plan)
        if assess_quality(manifest).status == "insufficient":
            return _result(
                request,
                plan,
                ledger,
                status="fatal_error",
                completed_steps=completed,
                error_code="sampling_data_insufficient",
            )
        ledger.consume_comments(len(request.comments))
        completed.append("preprocess")
        if request.cancellation_requested:
            return _result(
                request,
                plan,
                ledger,
                status="cancelled",
                completed_steps=completed,
                error_code="cancelled",
            )

        batch_plan = BatchPlanner(
            request.budget.max_input_tokens,
            request.budget.max_model_calls,
            gateway.capabilities.max_input_tokens_per_call,
            signal_fixed_input_tokens(request),
        ).plan(request.comments)
        per_call_output = _output_limit(request)
        for batch in batch_plan.batches:
            key = _signal_cache_key(request, plan, batch)
            cached, event = cache.get(
                key,
                stage="signals",
                now=request.analysis_time,
            )
            cache_events.append(event)
            if cached is not None:
                signals.extend(_parse_cached_signals(cached, batch))
            else:
                extraction = extract_signals(
                    request,
                    batch,
                    gateway,
                    ledger,
                    max_output_tokens=per_call_output,
                )
                signals.extend(extraction.signals)
                audits.extend(extraction.audits)
                _store_signals(cache, key, request, batch, extraction.signals)
        signals.sort(key=lambda signal: (signal.comment_id, signal.signal_id))
        completed.append("signals")
        if not signals:
            return _result(
                request,
                plan,
                ledger,
                status="no_signal",
                audits=audits,
                cache_events=cache_events,
                completed_steps=completed,
            )

        cluster_key = _cluster_cache_key(request, plan, signals)
        cached_clusters, cluster_event = cache.get(
            cluster_key,
            stage="cluster",
            now=request.analysis_time,
        )
        cache_events.append(cluster_event)
        if cached_clusters is not None:
            clusters, decisions = _parse_cached_clusters(request, cached_clusters, signals)
        else:
            cluster_result = cluster_signals(
                request,
                signals,
                gateway,
                ledger,
                max_output_tokens=per_call_output,
            )
            audits.extend(cluster_result.audits)
            clusters = cluster_result.clusters
            decisions = cluster_result.decisions
            _store_clusters(cache, cluster_key, request, signals, clusters)
        completed.extend(("cluster", "consensus"))
        return _result(
            request,
            plan,
            ledger,
            status="completed",
            signals=signals,
            clusters=clusters,
            decisions=decisions,
            audits=audits,
            cache_events=cache_events,
            completed_steps=completed,
        )
    except BatchPlanningError as error:
        return _result(
            request,
            plan,
            ledger,
            status="budget_exhausted",
            signals=signals,
            audits=audits,
            cache_events=cache_events,
            completed_steps=completed,
            exhausted_resource=error.resource,
        )
    except BudgetLimitExceeded as error:
        return _result(
            request,
            plan,
            ledger,
            status="budget_exhausted",
            signals=signals,
            audits=audits,
            cache_events=cache_events,
            completed_steps=completed,
            exhausted_resource=error.resource,
        )
    except InvocationCancelled as error:
        return _result(
            request,
            plan,
            ledger,
            status="cancelled",
            signals=signals,
            audits=[*audits, *error.audits],
            cache_events=cache_events,
            completed_steps=completed,
            error_code="cancelled",
        )
    except InvalidModelOutput as error:
        return _result(
            request,
            plan,
            ledger,
            status="retryable_error",
            signals=signals,
            audits=[*audits, *error.audits],
            cache_events=cache_events,
            completed_steps=completed,
            error_code="model_output_invalid",
        )
    except InvocationFailure as error:
        return _result(
            request,
            plan,
            ledger,
            status="retryable_error" if error.retryable else "fatal_error",
            signals=signals,
            audits=[*audits, *error.audits],
            cache_events=cache_events,
            completed_steps=completed,
            error_code=error.code,
        )
    except ValueError:
        return _result(
            request,
            plan,
            ledger,
            status="fatal_error",
            cache_events=cache_events,
            completed_steps=completed,
            error_code="invalid_sampling_inputs",
        )
