"""合并可信信号，重建稳定需求簇，并重新执行 M1 共识。"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import Field, ValidationError, model_validator

from ..contracts.analysis import (
    ConsensusDecision,
    ModelInvocationAudit,
    NeedCluster,
    NeedSignal,
)
from ..contracts.common import Contract, Identifier, Text
from ..contracts.requests import AnalysisRequest
from ..model.types import ControlledContentBlock, ModelCallRequest, ModelGateway
from ..rules import evaluate_consensus
from ..runtime.budget import BudgetLedger
from .batching import (
    BatchPlanner,
    BatchPlanningError,
    MergeItem,
    estimate_text_tokens,
    split_input_budget,
)
from .invocation import (
    CancellationProbe,
    InvocationBudgetExceeded,
    InvocationCancelled,
    InvocationFailure,
    invoke_model,
)
from .signals import InvalidModelOutput, normalize_summary

CLUSTER_PROMPT_VERSION = "cluster-v1"
_PROMPT_PATH = Path(__file__).parents[1] / "prompts" / "cluster-v1.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text("utf-8").strip()


class ProposedCluster(Contract):
    """模型只提出成员关系与摘要；cluster_id 永远不可信。"""

    cluster_id: Identifier | None = None
    comment_ids: list[Identifier] = Field(min_length=1)
    summary: Text

    @model_validator(mode="after")
    def unique_members(self) -> Self:
        if len(self.comment_ids) != len(set(self.comment_ids)):
            raise ValueError("cluster_members_must_be_unique")
        return self


class ClusterPayload(Contract):
    """聚类阶段唯一允许的顶层载荷。"""

    clusters: list[ProposedCluster]


@dataclass(frozen=True, slots=True)
class ClusterResult:
    """完整验证的簇、M1 决策及全部真实模型调用审计。"""

    clusters: tuple[NeedCluster, ...]
    decisions: tuple[ConsensusDecision, ...]
    audits: tuple[ModelInvocationAudit, ...]


@dataclass(frozen=True, slots=True)
class _MergeNode:
    """一个不可拆分的已验证合并节点。"""

    node_id: str
    comment_ids: tuple[str, ...]
    summary: str
    kind: str | None

    def model_data(self) -> dict[str, object]:
        data: dict[str, object] = {
            "item_id": self.node_id,
            "comment_ids": list(self.comment_ids),
            "summary": self.summary,
        }
        if self.kind is not None:
            data["kind"] = self.kind
        return data

    def planning_text(self) -> str:
        return json.dumps(
            self.model_data(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def stable_cluster_id(
    comment_ids: Sequence[str],
    summary: str,
    prompt_version: str,
) -> str:
    """由排序成员、规范摘要和 Prompt 版本生成稳定簇 ID。"""

    members = "\x1f".join(sorted(comment_ids))
    value = "\x1e".join((members, normalize_summary(summary), prompt_version))
    return f"clu_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def _trusted_signals(
    request: AnalysisRequest,
    signals: Sequence[NeedSignal],
) -> tuple[NeedSignal, ...]:
    """在发给模型前确认信号只引用当前可信快照。"""

    signal_ids = [signal.signal_id for signal in signals]
    comment_ids = [signal.comment_id for signal in signals]
    if len(signal_ids) != len(set(signal_ids)):
        raise ValueError("cluster_signal_ids_must_be_unique")
    if len(comment_ids) != len(set(comment_ids)):
        raise ValueError("cluster_signal_comments_must_be_unique")
    trusted_comment_ids = {comment.comment_id for comment in request.comments}
    if any(comment_id not in trusted_comment_ids for comment_id in comment_ids):
        raise ValueError("cluster_signal_comment_not_in_request")
    return tuple(sorted(signals, key=lambda signal: signal.signal_id))


def _signal_nodes(signals: tuple[NeedSignal, ...]) -> tuple[_MergeNode, ...]:
    return tuple(
        _MergeNode(
            node_id=signal.signal_id,
            comment_ids=(signal.comment_id,),
            summary=normalize_summary(signal.summary),
            kind=signal.kind,
        )
        for signal in signals
    )


def _cluster_nodes(clusters: tuple[NeedCluster, ...]) -> tuple[_MergeNode, ...]:
    return tuple(
        _MergeNode(
            node_id=cluster.cluster_id,
            comment_ids=tuple(cluster.comment_ids),
            summary=cluster.summary,
            kind=None,
        )
        for cluster in clusters
    )


def _fixed_input_tokens(request: AnalysisRequest) -> int:
    empty_source = json.dumps(
        {
            "video": {
                "platform": request.video.platform,
                "video_id": request.video.video_id,
            },
            "items": [],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    empty_untrusted = f"UNTRUSTED_SIGNAL_DATA\n{empty_source}\nEND_UNTRUSTED_SIGNAL_DATA"
    return estimate_text_tokens(f"{_SYSTEM_PROMPT}\n{empty_untrusted}") + 32


def _call_request(
    request: AnalysisRequest,
    nodes: tuple[_MergeNode, ...],
    *,
    level: int,
    batch_index: int,
    max_output_tokens: int,
    timeout_seconds: int,
    scenario_id: str | None,
) -> tuple[ModelCallRequest, int]:
    # 已验证节点仍来自模型，必须继续放在不可信数据块内。
    source = {
        "video": {
            "platform": request.video.platform,
            "video_id": request.video.video_id,
        },
        "items": [node.model_data() for node in nodes],
    }
    untrusted = (
        "UNTRUSTED_SIGNAL_DATA\n"
        + json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\nEND_UNTRUSTED_SIGNAL_DATA"
    )
    call = ModelCallRequest(
        invocation_id=f"cluster-level-{level}-batch-{batch_index}",
        stage="cluster",
        model_config_ref=request.model.config_ref,
        model_name=request.model.model_name,
        model_revision=request.model.revision,
        prompt_version=CLUSTER_PROMPT_VERSION,
        content_blocks=[
            ControlledContentBlock(
                block_id="cluster-system-v1",
                kind="system",
                content=_SYSTEM_PROMPT,
            ),
            ControlledContentBlock(
                block_id=f"cluster-data-{level}-{batch_index}",
                kind="untrusted_data",
                content=untrusted,
            ),
        ],
        expected_schema_name="need-clusters",
        expected_schema_version="1.0",
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        attempt=1,
        scenario_id=scenario_id,
    )
    return call, _estimated_call_input(call)


def _estimated_call_input(call: ModelCallRequest) -> int:
    # 为 Schema 名称和消息封装保留固定余量，避免按纯正文低估。
    contents = "\n".join(block.content for block in call.content_blocks)
    return estimate_text_tokens(contents) + 32


def _validate_payload(
    payload: object,
    nodes: tuple[_MergeNode, ...],
) -> tuple[NeedCluster, ...]:
    parsed = ClusterPayload.model_validate(payload)
    allowed_comment_ids = frozenset(comment_id for node in nodes for comment_id in node.comment_ids)
    flattened = [comment_id for cluster in parsed.clusters for comment_id in cluster.comment_ids]
    # 完整层级必须精确覆盖输入；未知、遗漏或跨簇重复都整体拒绝。
    if len(flattened) != len(set(flattened)):
        raise ValueError("cluster_members_must_not_overlap")
    if set(flattened) != allowed_comment_ids:
        raise ValueError("cluster_members_must_exactly_cover_inputs")

    proposed_member_sets = [set(cluster.comment_ids) for cluster in parsed.clusters]
    for node in nodes:
        node_members = set(node.comment_ids)
        destinations = [candidate for candidate in proposed_member_sets if candidate & node_members]
        if len(destinations) != 1 or not node_members <= destinations[0]:
            raise ValueError("validated_merge_node_must_not_be_split")

    clusters: list[NeedCluster] = []
    for proposed in parsed.clusters:
        ordered_members = sorted(proposed.comment_ids)
        summary = normalize_summary(proposed.summary)
        clusters.append(
            NeedCluster(
                cluster_id=stable_cluster_id(
                    ordered_members,
                    summary,
                    CLUSTER_PROMPT_VERSION,
                ),
                comment_ids=ordered_members,
                summary=summary,
            )
        )
    return tuple(sorted(clusters, key=lambda cluster: cluster.cluster_id))


def _prepend_audits(
    error: InvocationFailure | InvocationCancelled | InvocationBudgetExceeded,
    prior: tuple[ModelInvocationAudit, ...],
) -> InvocationFailure | InvocationCancelled | InvocationBudgetExceeded:
    combined = prior + error.audits
    if isinstance(error, InvocationCancelled):
        return InvocationCancelled(combined)
    if isinstance(error, InvocationBudgetExceeded):
        return InvocationBudgetExceeded(error.resource, combined)
    return InvocationFailure(error.code, retryable=error.retryable, audits=combined)


def _invoke_batch(
    request: AnalysisRequest,
    nodes: tuple[_MergeNode, ...],
    gateway: ModelGateway,
    ledger: BudgetLedger,
    *,
    level: int,
    batch_index: int,
    max_output_tokens: int,
    cancellation_probe: CancellationProbe,
    timeout_seconds: int,
    scenario_id: str | None,
    prior_audits: tuple[ModelInvocationAudit, ...],
) -> tuple[tuple[NeedCluster, ...], tuple[ModelInvocationAudit, ...], int]:
    call, estimated_input_tokens = _call_request(
        request,
        nodes,
        level=level,
        batch_index=batch_index,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        scenario_id=scenario_id,
    )
    audits: tuple[ModelInvocationAudit, ...] = ()
    next_attempt = 1
    planned_input_tokens = 0
    repairs = min(request.retry_policy.max_output_repairs, 1)
    for repair_index in range(repairs + 1):
        if repair_index:
            repair = ControlledContentBlock(
                block_id=f"cluster-repair-{level}-{batch_index}-{repair_index}",
                kind="repair",
                content=(
                    "The previous response was invalid. Return the schema again using every "
                    "comment_id in UNTRUSTED_SIGNAL_DATA exactly once, keep each listed item "
                    "intact, and use no other IDs."
                ),
            )
            call = call.model_copy(update={"content_blocks": [*call.content_blocks, repair]})

        estimated_input_tokens = _estimated_call_input(call)
        if estimated_input_tokens > gateway.capabilities.max_input_tokens_per_call:
            raise BatchPlanningError("input_tokens", "merge_batch_exceeds_gateway_input_limit")

        try:
            invocation = invoke_model(
                request,
                call,
                gateway,
                ledger,
                input_tokens=estimated_input_tokens,
                start_attempt=next_attempt,
                cancellation_probe=cancellation_probe,
            )
        except (InvocationFailure, InvocationCancelled, InvocationBudgetExceeded) as error:
            raise _prepend_audits(error, prior_audits + audits) from error

        audits += invocation.audits
        planned_input_tokens += estimated_input_tokens * len(invocation.audits)
        next_attempt = invocation.next_attempt
        try:
            if invocation.response.finish_reason != "stop":
                raise ValueError("cluster_response_did_not_finish")
            clusters = _validate_payload(invocation.response.payload, nodes)
        except (ValidationError, ValueError):
            if repair_index == repairs:
                raise InvalidModelOutput(prior_audits + audits) from None
            continue
        return clusters, audits, planned_input_tokens

    raise AssertionError("unreachable")


def cluster_signals(
    request: AnalysisRequest,
    signals: Sequence[NeedSignal],
    gateway: ModelGateway,
    ledger: BudgetLedger,
    *,
    max_output_tokens: int,
    cancellation_probe: CancellationProbe = lambda: False,
    timeout_seconds: int = 30,
    scenario_id: str | None = None,
) -> ClusterResult:
    """合并同视频可信信号，最多修复一次，并以 M1 规则复核结果。"""

    trusted = _trusted_signals(request, signals)
    if not trusted:
        return ClusterResult((), (), ())

    planner = BatchPlanner(
        request.budget.max_input_tokens,
        request.budget.max_model_calls,
        gateway.capabilities.max_input_tokens_per_call,
        _fixed_input_tokens(request),
    )
    merge_reserve = split_input_budget(request.budget.max_input_tokens)[1]
    planned_input_tokens = 0
    audits: tuple[ModelInvocationAudit, ...] = ()
    nodes = _signal_nodes(trusted)
    level = 1
    while True:
        by_id = {node.node_id: node for node in nodes}
        merge_batches = planner.plan_merge(
            [
                MergeItem(node.node_id, request.video.video_id, node.planning_text())
                for node in nodes
            ],
            calls_already_planned=ledger.snapshot().model_calls_consumed,
        )
        layer_clusters: list[NeedCluster] = []
        for batch_index, batch in enumerate(merge_batches, start=1):
            batch_nodes = tuple(by_id[item_id] for item_id in batch.item_ids)
            clusters, batch_audits, input_tokens = _invoke_batch(
                request,
                batch_nodes,
                gateway,
                ledger,
                level=level,
                batch_index=batch_index,
                max_output_tokens=max_output_tokens,
                cancellation_probe=cancellation_probe,
                timeout_seconds=timeout_seconds,
                scenario_id=scenario_id,
                prior_audits=audits,
            )
            audits += batch_audits
            planned_input_tokens += input_tokens
            if planned_input_tokens > merge_reserve:
                raise BatchPlanningError("input_tokens", "merge_input_budget_exhausted")
            layer_clusters.extend(clusters)

        final_level = len(merge_batches) == 1
        ordered_clusters = tuple(sorted(layer_clusters, key=lambda cluster: cluster.cluster_id))
        if final_level:
            decisions = tuple(evaluate_consensus(request, cluster) for cluster in ordered_clusters)
            return ClusterResult(ordered_clusters, decisions, audits)
        nodes = _cluster_nodes(ordered_clusters)
        level += 1
