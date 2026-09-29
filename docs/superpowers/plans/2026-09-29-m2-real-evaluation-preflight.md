# M2 Real Evaluation Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为真实模型评测增加独立的版本冻结与响应身份核对路径，同时保持普通 `analyze_m2` 对 `revision=None` 的兼容行为。

**Architecture:** 新增纯离线 `preflight_real_evaluation`，用 gateway 在调用前暴露的身份和能力生成不可空的 `FrozenModelIdentity`；新增 `analyze_m2_real_evaluation` 作为唯一正式评测入口，并把冻结身份贯穿调用与缓存。共享调用边界在 provider 响应后核对实际 model/revision，失败时结算用量、记录 requested/actual 审计且禁止解析和缓存；普通 `analyze_m2` 不传冻结身份，行为保持不变。

**Tech Stack:** Python 3.12、Pydantic 2、pytest、JSON Schema、Ruff、mypy strict、uv。

---

## 文件职责

- `packages/agent/src/requirementseeker_agent/model/types.py`：定义 gateway 配置身份、冻结身份和版本化 gateway 端口。
- `packages/agent/src/requirementseeker_agent/model/fake.py`：为确定性场景 gateway 提供非付费身份元数据。
- `packages/agent/src/requirementseeker_agent/pipeline/preflight.py`：只读、无模型调用的真实评测准入检查。
- `packages/agent/src/requirementseeker_agent/contracts/analysis.py`：扩展调用审计的 requested/actual 身份字段。
- `packages/agent/src/requirementseeker_agent/pipeline/invocation.py`：在响应后核对冻结身份并生成完整审计。
- `packages/agent/src/requirementseeker_agent/pipeline/signals.py`、`clusters.py`：把可选冻结身份传到每一次实际调用。
- `packages/agent/src/requirementseeker_agent/pipeline/m2.py`：保持普通入口，新增真实评测入口，并让缓存键使用经过验证的冻结 revision。
- `packages/agent/src/requirementseeker_agent/model/__init__.py`、`pipeline/__init__.py`、`__init__.py`：导出新的稳定公共类型和入口。
- `packages/agent/tests/pipeline/test_preflight.py`：覆盖调用前 fail-closed 规则。
- `packages/agent/tests/pipeline/test_invocation.py`：覆盖响应身份、用量结算、不可重试和审计字段。
- `packages/agent/tests/pipeline/test_m2.py`：覆盖普通兼容路径、真实评测入口和冻结缓存。
- `packages/agent/tests/runtime/test_cache.py`：直接证明 cache key 包含 model revision。
- `packages/agent/schemas/analysis-result.schema.json`：与扩展后的审计契约同步。
- `packages/agent/README.md`、`docs/contracts/agent-v1.md`：说明正式入口、授权边界、身份冻结和在途取消限制。

### Task 1: 建立真实评测 preflight 身份契约

**Files:**
- Create: `packages/agent/src/requirementseeker_agent/pipeline/preflight.py`
- Create: `packages/agent/tests/pipeline/test_preflight.py`
- Modify: `packages/agent/src/requirementseeker_agent/model/types.py`
- Modify: `packages/agent/src/requirementseeker_agent/model/fake.py`
- Modify: `packages/agent/src/requirementseeker_agent/model/__init__.py`

- [ ] **Step 1: 写 preflight 的失败测试**

在 `test_preflight.py` 使用合成 `AnalysisRequest` 和不会调用 `invoke` 的 gateway，覆盖非空 revision 成功，以及以下稳定错误码：

```python
import pytest

from requirementseeker_agent.model import (
    ModelCapabilities,
    ModelRuntimeIdentity,
    ScenarioModelGateway,
)
from requirementseeker_agent.pipeline.preflight import (
    RealEvaluationPreflightError,
    preflight_real_evaluation,
)

from .test_invocation import analysis_request


class PreflightGateway(ScenarioModelGateway):
    def __init__(self, identity: ModelRuntimeIdentity) -> None:
        super().__init__("valid_pipeline")
        self._identity = identity

    @property
    def identity(self) -> ModelRuntimeIdentity:
        return self._identity


def frozen_request():
    request = analysis_request()
    model = request.model.model_copy(
        update={"model_name": "scenario-model", "revision": "m2-fixture-1"}
    )
    return request.model_copy(update={"model": model})


def test_preflight_freezes_matching_non_null_revision() -> None:
    frozen = preflight_real_evaluation(
        frozen_request(),
        PreflightGateway(ModelRuntimeIdentity(model_name="scenario-model", model_revision="m2-fixture-1")),
    )
    assert frozen.model_name == "scenario-model"
    assert frozen.model_revision == "m2-fixture-1"


@pytest.mark.parametrize(
    ("request_revision", "gateway_name", "gateway_revision", "code"),
    [
        (None, "scenario-model", "m2-fixture-1", "requested_revision_required"),
        ("m2-fixture-1", "scenario-model", None, "gateway_revision_required"),
        ("m2-fixture-1", "other-model", "m2-fixture-1", "gateway_model_mismatch"),
        ("m2-fixture-1", "scenario-model", "other-revision", "gateway_revision_mismatch"),
    ],
)
def test_preflight_fails_closed_on_unfrozen_identity(
    request_revision: str | None,
    gateway_name: str,
    gateway_revision: str | None,
    code: str,
) -> None:
    request = frozen_request()
    request = request.model_copy(
        update={"model": request.model.model_copy(update={"revision": request_revision})}
    )
    gateway = PreflightGateway(
        ModelRuntimeIdentity(model_name=gateway_name, model_revision=gateway_revision)
    )

    with pytest.raises(RealEvaluationPreflightError) as raised:
        preflight_real_evaluation(request, gateway)

    assert raised.value.code == code
```

另加一个 gateway `supports_structured_output=False` 的测试，期望 `gateway_capability_unsupported`。测试不得调用 `invoke`，可令 `invoke` 直接 `raise AssertionError("preflight_must_not_invoke_model")`。

- [ ] **Step 2: 运行 RED**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_preflight.py -q
```

Expected: collection 失败，指出 `ModelRuntimeIdentity` 或 `pipeline.preflight` 尚不存在；这证明测试针对缺失能力。

- [ ] **Step 3: 实现最小身份类型和 preflight**

在 `model/types.py` 增加：

```python
class ModelRuntimeIdentity(Contract):
    """gateway 在付费调用前可提供的非秘密模型身份。"""

    model_name: Identifier
    model_revision: Identifier | None


class FrozenModelIdentity(Contract):
    """通过真实评测准入核对后的不可空模型身份。"""

    model_name: Identifier
    model_revision: Identifier


class VersionedModelGateway(ModelGateway, Protocol):
    """真实评测要求的可核验 gateway 端口。"""

    @property
    def identity(self) -> ModelRuntimeIdentity: ...
```

在 `ScenarioModelGateway` 增加只读属性：

```python
@property
def identity(self) -> ModelRuntimeIdentity:
    return ModelRuntimeIdentity(
        model_name="scenario-model",
        model_revision="m2-fixture-1",
    )
```

在新建的 `pipeline/preflight.py` 实现：

```python
from typing import Literal

from ..contracts.requests import AnalysisRequest
from ..model import FrozenModelIdentity, VersionedModelGateway

PreflightErrorCode = Literal[
    "requested_revision_required",
    "gateway_revision_required",
    "gateway_model_mismatch",
    "gateway_revision_mismatch",
    "gateway_capability_unsupported",
]


class RealEvaluationPreflightError(RuntimeError):
    def __init__(self, code: PreflightErrorCode) -> None:
        super().__init__(code)
        self.code = code


def preflight_real_evaluation(
    request: AnalysisRequest,
    gateway: VersionedModelGateway,
) -> FrozenModelIdentity:
    requested_revision = request.model.revision
    if requested_revision is None:
        raise RealEvaluationPreflightError("requested_revision_required")
    identity = gateway.identity
    if identity.model_revision is None:
        raise RealEvaluationPreflightError("gateway_revision_required")
    if identity.model_name != request.model.model_name:
        raise RealEvaluationPreflightError("gateway_model_mismatch")
    if identity.model_revision != requested_revision:
        raise RealEvaluationPreflightError("gateway_revision_mismatch")
    capabilities = gateway.capabilities
    if (
        not request.model.supports_text
        or not capabilities.supports_text
        or not capabilities.supports_structured_output
    ):
        raise RealEvaluationPreflightError("gateway_capability_unsupported")
    return FrozenModelIdentity(
        model_name=identity.model_name,
        model_revision=identity.model_revision,
    )
```

从 `model/__init__.py` 导出三个新类型。不要把 preflight 接到 `analyze_m2`。

- [ ] **Step 4: 运行 GREEN 和类型检查**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_preflight.py packages/agent/tests/model -q
uv run --offline --locked --project packages/agent mypy packages/agent/src
```

Expected: 新 preflight 与既有 model 测试全部通过；mypy 报告无问题。

- [ ] **Step 5: 提交**

```powershell
git add packages/agent/src/requirementseeker_agent/model packages/agent/src/requirementseeker_agent/pipeline/preflight.py packages/agent/tests/pipeline/test_preflight.py
git commit -m "feat(agent): add real evaluation model preflight"
```

### Task 2: 扩展 requested/actual 调用审计

**Files:**
- Modify: `packages/agent/src/requirementseeker_agent/contracts/analysis.py`
- Modify: `packages/agent/src/requirementseeker_agent/pipeline/invocation.py`
- Modify: `packages/agent/tests/pipeline/test_invocation.py`

- [ ] **Step 1: 写审计字段 RED 测试**

在现有成功、传输失败和取消测试中加入明确断言，并新增 provider 返回空 revision 的 fixture helper：

```python
def test_success_audit_records_requested_and_actual_identity() -> None:
    gateway = ScriptedGateway([response({"signals": []})])

    result = invoke_model(
        analysis_request(), model_call(), gateway, ledger(), input_tokens=100
    )

    audit = result.audits[0]
    assert audit.requested_model_name == "synthetic-model"
    assert audit.requested_revision is None
    assert audit.actual_model_name == "scenario-model"
    assert audit.actual_revision == "m2-fixture-1"


def test_transport_error_audit_has_no_actual_identity() -> None:
    gateway = ScriptedGateway([ModelGatewayError("timeout", retryable=True)] * 3)

    with pytest.raises(InvocationFailure) as raised:
        invoke_model(analysis_request(), model_call(), gateway, ledger(), input_tokens=100)

    audit = raised.value.audits[0]
    assert audit.requested_model_name == "synthetic-model"
    assert audit.requested_revision is None
    assert audit.actual_model_name is None
    assert audit.actual_revision is None
```

- [ ] **Step 2: 运行 RED**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_invocation.py -q
```

Expected: 新断言以 `ModelInvocationAudit` 缺少 requested/actual 属性失败。

- [ ] **Step 3: 最小扩展审计契约与构造逻辑**

在 `ModelInvocationAudit` 保留现有 `model_name` 字段以兼容既有线协议，并增加可选字段：

```python
class ModelInvocationAudit(Contract):
    invocation_id: Identifier
    model_config_ref: Identifier
    model_name: Identifier
    requested_model_name: Identifier | None = None
    requested_revision: Identifier | None = None
    actual_model_name: Identifier | None = None
    actual_revision: Identifier | None = None
    versions: Versions
    input_hash: Hash
    step: Step
    attempt: PositiveInt
    status: Literal["success", "error", "cancelled"]
    usage: TokenUsage | None
    error_code: Identifier | None
```

在 `_audit` 构造中使用：

```python
model_name=response.model_name if response is not None else call.model_name,
requested_model_name=call.model_name,
requested_revision=call.model_revision,
actual_model_name=None if response is None else response.model_name,
actual_revision=None if response is None else response.model_revision,
```

不得把空 actual revision 替换成 requested revision。

- [ ] **Step 4: 运行 GREEN**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_invocation.py packages/agent/tests/contract/test_results.py -q
```

Expected: 两组测试通过；传输错误 actual 字段为 null，收到响应的审计保留 provider 原值。

- [ ] **Step 5: 提交**

```powershell
git add packages/agent/src/requirementseeker_agent/contracts/analysis.py packages/agent/src/requirementseeker_agent/pipeline/invocation.py packages/agent/tests/pipeline/test_invocation.py
git commit -m "feat(agent): audit requested and actual model identity"
```

### Task 3: 在调用边界核对 provider 实际身份

**Files:**
- Modify: `packages/agent/src/requirementseeker_agent/model/types.py`
- Modify: `packages/agent/src/requirementseeker_agent/pipeline/invocation.py`
- Modify: `packages/agent/tests/pipeline/test_invocation.py`

- [ ] **Step 1: 写实际身份不一致的 RED 测试**

构造冻结身份并分别覆盖错误 revision、空 revision 和错误 model name：

```python
from requirementseeker_agent.model import FrozenModelIdentity


@pytest.mark.parametrize(
    ("actual_name", "actual_revision", "code"),
    [
        ("scenario-model", "other-revision", "model_identity_mismatch"),
        ("other-model", "m2-fixture-1", "model_identity_mismatch"),
        ("scenario-model", None, "model_identity_unverifiable"),
    ],
)
def test_frozen_identity_rejects_unverified_provider_response(
    actual_name: str,
    actual_revision: str | None,
    code: str,
) -> None:
    request = analysis_request()
    call = model_call().model_copy(
        update={"model_name": "scenario-model", "model_revision": "m2-fixture-1"}
    )
    gateway_response = response({"signals": []}).model_copy(
        update={"model_name": actual_name, "model_revision": actual_revision}
    )
    gateway = ScriptedGateway([gateway_response])
    budget = ledger()

    with pytest.raises(InvocationFailure) as raised:
        invoke_model(
            request,
            call,
            gateway,
            budget,
            input_tokens=100,
            frozen_identity=FrozenModelIdentity(
                model_name="scenario-model", model_revision="m2-fixture-1"
            ),
        )

    assert raised.value.code == code
    assert raised.value.retryable is False
    assert len(gateway.calls) == 1
    assert budget.snapshot().model_calls_consumed == 1
    audit = raised.value.audits[0]
    assert audit.status == "error"
    assert audit.error_code == code
    assert audit.actual_model_name == actual_name
    assert audit.actual_revision == actual_revision
```

再加匹配身份成功测试，断言原响应正常返回且审计为 success。

- [ ] **Step 2: 运行 RED**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_invocation.py -q
```

Expected: `invoke_model` 不接受 `frozen_identity` 或错误响应未失败。

- [ ] **Step 3: 实现 fail-closed 身份核对**

把两个代码加入 `GatewayErrorCode`：

```python
"model_identity_mismatch",
"model_identity_unverifiable",
```

给 `invoke_model` 增加仅供正式路径传入的可选参数：

```python
frozen_identity: FrozenModelIdentity | None = None,
```

在成功响应完成 `ledger.settle(reservation, response.usage)` 后、取消检查和 success audit 前执行：

```python
if frozen_identity is not None:
    identity_error: GatewayErrorCode | None = None
    if response.model_revision is None:
        identity_error = "model_identity_unverifiable"
    elif (
        response.model_name != frozen_identity.model_name
        or response.model_revision != frozen_identity.model_revision
    ):
        identity_error = "model_identity_mismatch"
    if identity_error is not None:
        audits.append(
            _audit(
                request,
                attempted_call,
                status="error",
                response=response,
                error_code=identity_error,
            )
        )
        raise InvocationFailure(
            identity_error,
            retryable=False,
            audits=tuple(audits),
        )
```

普通调用的默认 `None` 不核对身份，确保兼容条件 1。

- [ ] **Step 4: 运行 GREEN 和相关回归**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_invocation.py packages/agent/tests/runtime/test_budget.py -q
```

Expected: 身份匹配成功；缺失或不一致均一次调用后非重试失败；用量已经结算；预算测试保持通过。

- [ ] **Step 5: 提交**

```powershell
git add packages/agent/src/requirementseeker_agent/model/types.py packages/agent/src/requirementseeker_agent/pipeline/invocation.py packages/agent/tests/pipeline/test_invocation.py
git commit -m "fix(agent): fail closed on model identity drift"
```

### Task 4: 接入真实评测入口和冻结 revision 缓存

**Files:**
- Modify: `packages/agent/src/requirementseeker_agent/pipeline/signals.py`
- Modify: `packages/agent/src/requirementseeker_agent/pipeline/clusters.py`
- Modify: `packages/agent/src/requirementseeker_agent/pipeline/m2.py`
- Modify: `packages/agent/src/requirementseeker_agent/pipeline/__init__.py`
- Modify: `packages/agent/src/requirementseeker_agent/__init__.py`
- Modify: `packages/agent/tests/pipeline/test_m2.py`
- Modify: `packages/agent/tests/runtime/test_cache.py`
- Modify: `packages/agent/tests/test_public_api.py`

- [ ] **Step 1: 写普通兼容和正式入口 RED 测试**

在 `test_m2.py` 明确覆盖验收条件 1–5：

```python
from requirementseeker_agent.model import ModelRuntimeIdentity
from requirementseeker_agent.pipeline.m2 import analyze_m2_real_evaluation
from requirementseeker_agent.pipeline.preflight import RealEvaluationPreflightError


def test_regular_pipeline_keeps_revision_none_compatibility() -> None:
    request, manifest, plan = inputs()
    assert request.model.revision is None

    result = analyze_m2(
        request,
        manifest,
        plan,
        ScenarioModelGateway("valid_pipeline"),
        InMemorySemanticCache(),
    )

    assert result.status == "completed"


def test_real_evaluation_rejects_revision_none_before_call() -> None:
    request, manifest, plan = inputs()
    gateway = CountingGateway("valid_pipeline")

    with pytest.raises(RealEvaluationPreflightError) as raised:
        analyze_m2_real_evaluation(
            request, manifest, plan, gateway, InMemorySemanticCache()
        )

    assert raised.value.code == "requested_revision_required"
    assert gateway.calls == []


def test_real_evaluation_uses_matching_frozen_identity() -> None:
    request, manifest, plan = inputs()
    request = request.model_copy(
        update={
            "model": request.model.model_copy(
                update={"model_name": "scenario-model", "revision": "m2-fixture-1"}
            )
        }
    )
    gateway = CountingGateway("valid_pipeline")

    result = analyze_m2_real_evaluation(
        request, manifest, plan, gateway, InMemorySemanticCache()
    )

    assert result.status == "completed"
    assert all(audit.requested_revision == "m2-fixture-1" for audit in result.audits)
    assert all(audit.actual_revision == "m2-fixture-1" for audit in result.audits)
```

让 `CountingGateway.identity` 返回 `self._inner.identity`。另建一个继承类，在 `invoke` 返回后把 `model_revision` 改成 `other-revision`，断言正式入口返回 `fatal_error`、`error_code == "model_identity_mismatch"`、没有 cluster 结果且缓存第二次仍 miss。

在 `test_cache.py` 增加直接键测试：

```python
def test_cache_key_changes_with_frozen_model_revision() -> None:
    assert cache_key(parts(model_revision="revision-a")) != cache_key(
        parts(model_revision="revision-b")
    )
```

- [ ] **Step 2: 运行 RED**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_m2.py packages/agent/tests/runtime/test_cache.py packages/agent/tests/test_public_api.py -q
```

Expected: 缺少 `analyze_m2_real_evaluation`，正式入口测试失败；cache key 单测可能已经通过，记录它是既有事实而不是新增 RED，并继续保留为验收锁定。

- [ ] **Step 3: 把冻结身份贯穿信号和聚类调用**

给 `extract_signals`、`cluster_signals` 和 `clusters._invoke_batch` 增加：

```python
frozen_identity: FrozenModelIdentity | None = None,
```

每个 `invoke_model` 调用显式传递：

```python
frozen_identity=frozen_identity,
```

所有内部聚类批次递归/循环调用都传同一个不可变值；不得从响应重新生成冻结身份。

- [ ] **Step 4: 拆出共享实现并新增正式入口**

在 `m2.py` 保留原公共签名，并引入内部实现：

```python
def analyze_m2(
    request: AnalysisRequest,
    manifest: SamplingManifest,
    plan: SamplingPlan,
    gateway: ModelGateway,
    cache: InMemorySemanticCache,
    *,
    cancellation_probe: CancellationProbe = lambda: False,
) -> M2AnalysisResult:
    return _analyze_m2(
        request,
        manifest,
        plan,
        gateway,
        cache,
        cancellation_probe=cancellation_probe,
        frozen_identity=None,
    )


def analyze_m2_real_evaluation(
    request: AnalysisRequest,
    manifest: SamplingManifest,
    plan: SamplingPlan,
    gateway: VersionedModelGateway,
    cache: InMemorySemanticCache,
    *,
    cancellation_probe: CancellationProbe = lambda: False,
) -> M2AnalysisResult:
    frozen_identity = preflight_real_evaluation(request, gateway)
    return _analyze_m2(
        request,
        manifest,
        plan,
        gateway,
        cache,
        cancellation_probe=cancellation_probe,
        frozen_identity=frozen_identity,
    )
```

把原 `analyze_m2` 函数体原样移动到 `_analyze_m2`，只做三类定向替换：

```python
def _analyze_m2(
    request: AnalysisRequest,
    manifest: SamplingManifest,
    plan: SamplingPlan,
    gateway: ModelGateway,
    cache: InMemorySemanticCache,
    *,
    cancellation_probe: CancellationProbe,
    frozen_identity: FrozenModelIdentity | None,
) -> M2AnalysisResult:
```

第一类：信号/聚类调用传 `frozen_identity=frozen_identity`。第二类：计算一次缓存 revision：

```python
cache_revision = (
    request.model.revision
    if frozen_identity is None
    else frozen_identity.model_revision
)
```

第三类：`_signal_cache_key` 和 `_cluster_cache_key` 接收 `model_revision` 参数，最终传给 `_stage_cache_key`；`_stage_cache_key` 的 `CacheKeyParts` 使用：

```python
model_revision=model_revision,
```

不要在正式路径回退到 `request.model.revision` 或 response revision。

- [ ] **Step 5: 导出正式入口并运行 GREEN**

从 `pipeline/__init__.py` 和顶层 `__init__.py` 导出 `analyze_m2_real_evaluation`；在 `test_public_api.py` 断言其可调用。运行：

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline packages/agent/tests/runtime/test_cache.py packages/agent/tests/test_public_api.py -q
uv run --offline --locked --project packages/agent mypy packages/agent/src
```

Expected: 普通 `revision=None` 完成；正式空 revision 在零调用处失败；实际身份漂移映射为非成功结果且不缓存；全部目标测试和 mypy 通过。

- [ ] **Step 6: 提交**

```powershell
git add packages/agent/src/requirementseeker_agent/pipeline packages/agent/src/requirementseeker_agent/__init__.py packages/agent/tests/pipeline/test_m2.py packages/agent/tests/runtime/test_cache.py packages/agent/tests/test_public_api.py
git commit -m "feat(agent): add frozen real evaluation entrypoint"
```

### Task 5: 同步 Schema、文档并完成离线总门禁

**Files:**
- Modify: `packages/agent/schemas/analysis-result.schema.json`
- Modify: `packages/agent/README.md`
- Modify: `docs/contracts/agent-v1.md`
- Modify: `docs/development/2026-09-07-m2-acceptance.md`
- Modify: `packages/agent/tests/contract/test_schema.py` only if a focused assertion is needed; do not weaken equality checks
- Local-only update: `E:/Projects/RequirementSeeker/docs/HANDOFF.md`
- Local-only update: `E:/Projects/RequirementSeeker/docs/execution/2026-09-29.md`

- [ ] **Step 1: 运行 Schema RED**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/contract/test_schema.py -q
```

Expected: `test_committed_schema_matches_generated_schema[result]` 失败，显示已提交 result Schema 尚无四个审计字段。不得修改测试来掩盖差异。

- [ ] **Step 2: 从 Pydantic 权威模型重新生成 result Schema**

Run:

```powershell
uv run --offline --locked --project packages/agent rs-agent schema result | Set-Content -Encoding utf8NoBOM packages/agent/schemas/analysis-result.schema.json
```

该命令直接使用权威 Pydantic 模型输出排序后的单行 JSON，并以 UTF-8 无 BOM 覆盖已提交 Schema；不得手工改动 Schema 含义。重新运行：

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/contract/test_schema.py -q
```

Expected: committed/generated Schema 相等，所有有效 fixture 通过标准 JSON Schema。

- [ ] **Step 3: 更新公开文档**

在 `packages/agent/README.md` 增加真实评测入口示例，明确示例只使用确定性 gateway：

```python
result = analyze_m2_real_evaluation(request, manifest, plan, gateway, cache)
```

紧邻示例写清：调用方仍需单独的真实模型和费用授权；preflight 不读取密钥，也不授予调用权限；同步 gateway 只能在调用边界前后观察取消，不能中断已经在途的 provider 请求。

在 `docs/contracts/agent-v1.md` 记录：

- 普通 M2 允许 `revision=null`；
- real-eval 请求、gateway 配置身份和响应实际身份必须有完全一致的非空 revision；
- audit 四个 requested/actual 字段的 null 语义；
- response 身份失败不得缓存或重试；
- cache key 绑定经过 preflight 的 revision。

在 M2 acceptance 文档中仅记录新的离线门禁，不宣称真实模型 H0/H1，不把 24/24 写成人工语义金标。

- [ ] **Step 4: 运行完整 Agent 门禁**

Run:

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests -q
uv run --offline --locked --project packages/agent ruff check packages/agent/src packages/agent/tests packages/agent/examples
uv run --offline --locked --project packages/agent ruff format --check packages/agent/src packages/agent/tests packages/agent/examples
uv run --offline --locked --project packages/agent mypy packages/agent/src
uv build --offline --project packages/agent
git diff --check upstream/main...HEAD
```

Expected: 全部命令 exit 0；测试数应高于基线 236；构建生成 0.3.0 sdist/wheel；无真实网络模型调用、密钥读取或费用。

- [ ] **Step 5: 复核验收条件和敏感边界**

逐项核对并在本地执行日志记录证据：

```text
1. 普通 analyze_m2 + ScenarioModelGateway + revision=None 通过。
2. real-eval preflight + revision=None 在调用前失败。
3. requested revision 与 actual revision 不同，失败且不缓存。
4. actual revision 缺失，失败且不缓存。
5. CacheKeyParts 与真实入口均使用 frozen revision。
6. audit 保存 requested/actual model/revision。
7. VersionedModelGateway 契约及 response 提供实际身份。
8. 24/24 仅称结构可评测。
9. export-labels 由 upstream issue #8 单独跟踪。
```

检查 Git 差异中没有 `.local-data`、评论、视频 ID、密钥、真实标签内容或 provider 凭据。

- [ ] **Step 6: 提交文档和 Schema**

```powershell
git add packages/agent/schemas/analysis-result.schema.json packages/agent/README.md docs/contracts/agent-v1.md docs/development/2026-09-07-m2-acceptance.md
git commit -m "docs(agent): document real evaluation identity gate"
```

- [ ] **Step 7: 同步本地交接记录**

使用 `apply_patch` 更新根工作区忽略的 `docs/HANDOFF.md` 和 `docs/execution/2026-09-29.md`，写入实际提交、测试计数、门禁结果、已知限制和下一步。不要把本地日志加入当前分支提交。

- [ ] **Step 8: 独立代码复审与最终验证**

按 `requesting-code-review` 检查 `98bdf81..HEAD` 是否满足本计划和设计；修复所有 Critical/Important 项后重新运行 Step 4 全门禁。未经用户后续要求，不推送、不创建 PR、不合并到 main。
