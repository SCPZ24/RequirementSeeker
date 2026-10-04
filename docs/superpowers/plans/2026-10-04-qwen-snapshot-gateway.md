# Qwen Snapshot Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为北京 qwen-plus-2025-12-01 添加可审计、逐次核对冻结身份的独立同步适配器，保持现有兼容路径。

**Architecture:** 采用现有 DeepSeek 网关的标准库传输和契约风格，不重构公共代码。仅已核验响应快照 ID 映射 revision=2025-12-01；未知合法模型保留名称和 None revision，交由正式管线拒绝。复用现有 preflight、invoke_model、冻结缓存和四字段审计。

**Tech Stack:** Python 3.12、urllib.request、Pydantic、pytest、uv、Ruff、strict mypy。

---

## 执行边界与验证命令

工作目录 C:/Users/Fantason/.codex/worktrees/qwen-snapshot-gateway/RequirementSeeker，分支 codex/qwen-snapshot-gateway。
基线设计提交 b86b5b8，Agent 基线354 passed。用户已确认规格并要求继续实施。
仅合成 fixture、离线包和本地提交；不读取密钥、真实数据，不新增 API 调用，不刷新标签，不推送或合并。
subagent-driven-development：Task 1 实施→规格审→质量审，Task 2 新实施者→规格审→质量审，最后整体复核。
不宣称探测证明权重不可变；24/24仅结构可评测。export-labels 已由独立 PR 修复，不运行刷新。

## Task 1: 网关、导出与离线身份集成

**Files:**
- Create: packages/agent/src/requirementseeker_agent/model/qwen.py
- Modify: packages/agent/src/requirementseeker_agent/model/__init__.py
- Modify: packages/agent/src/requirementseeker_agent/__init__.py
- Create: packages/agent/tests/model/test_qwen.py
- Create: packages/agent/tests/pipeline/test_qwen_real_evaluation.py

- [ ] **Step 1: 先写网关测试和正式管线集成测试。**

测试入口必须通过断言检查导出，缺少功能时产生明确 assertion failure，而不是掩盖拼写错误的导入失败：

```python
import requirementseeker_agent as public
import requirementseeker_agent.model as model
from model.test_gateway_contract import make_call

MODEL = "qwen-plus-2025-12-01"
REVISION = "2025-12-01"

def call(**updates):
    return make_call("valid_signals").model_copy(update={
        "model_name": MODEL, "model_revision": REVISION,
        "scenario_id": None, **updates,
    })

def gateway(transport):
    assert hasattr(model, "QwenModelGateway"), "Qwen gateway is missing"
    assert public.QwenModelGateway is model.QwenModelGateway
    return model.QwenModelGateway(
        api_key="offline-secret", max_input_tokens_per_call=4096, transport=transport
    )
```

原始 envelope 合成 fixture 使用 id/object/created/model/system_fingerprint=null、单 choices assistant JSON content/stop、usage prompt=10/completion=5/total=15。发送与响应测试须精确断言 URL、Authorization、timeout、max_tokens、关闭搜索/思考/流式、JSON object、system/user 内容隔离、SHA256、usage、fingerprint、固定配置身份、能力和密钥不出现在 repr。

逐项参数化测试矩阵：
- 请求：不同模型、None/不同 revision、scenario_id、max_output_tokens=0/32769，零传输；32768合法。
- 构造：空白/空/非字符串/非ASCII/CRLF密钥，0/负/布尔/非整数输入限制，固定脱敏错误。
- 身份：完整支持快照→REVISION；别名、未知日期快照、其他模型→保留原名、None；missing/null/空白/非字符串 model→非重试 model_identity_unverifiable。
- 不采用供应商额外 revision 值、system_fingerprint 或请求字段补填身份。
- HTTP 401/403→authentication_failed(false)，429→rate_limited(true)，408/504→timeout(true)，5xx→transport_error(true)，其他4xx/3xx→invalid_configuration(false)；一次传输，不在网关内重试。
- TimeoutError、URLError(TimeoutError)、URLError、OSError、HTTPException→既定脱敏错误。
- 非JSON/非字典/非法UTF8/过深 envelope，choices多项/空/非法role/content/reason，非法fingerprint/usage字段→invalid_configuration(false)。
- stop/length/content_filter及未知reason规范化；usage/fingerprint missing或null允许。
- 默认 opener 使用禁重定向 handler，传递实际 timeout；传输错误不泄漏原始异常正文；非法Unicode请求零传输。

集成 fixture 复用 pipeline.test_m2.inputs() 的合成评论，通过实际 wire messages 解析阶段输入，构造 signals 和 cluster 的真实 Schema 载荷。正式入口不使用 ScenarioModelGateway 代替千问网关。
成功两阶段→completed、两条四身份审计、第二次缓存重放hit/hit且零新调用。
signals/cluster 响应 drift→fatal_error/model_identity_unverifiable，合法未知模型保留实际名称，revision=None，非重试且失败阶段不缓存；用已支持网关返回错误 revision 的测试验证 model_identity_mismatch。
缺失model→fatal_error/model_identity_unverifiable，actual字段None，最坏预算结算，无重试。
正式 preflight None/不同revision/不同模型→零HTTP失败。
ordinary Scenario revision=None 原回归保持成功。冻结key通过拦截 m2.cache_key 的 CacheKeyParts 断言 model/revision及 identity_verification_required=True，并核对更换冻结revision改变digest。

- [ ] **Step 2: 运行全部新测试观察 RED，不使用 -x。**

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/model/test_qwen.py packages/agent/tests/pipeline/test_qwen_real_evaluation.py -q
```

预期新功能相关测试均明确缺少 QwenModelGateway；已有管线行为测试可能 GREEN，分别披露，不把现有 GREEN 描述为新功能 RED。保留失败数与原因；若失败源于 fixture 错误先修 fixture 再观察。

- [ ] **Step 3: 实现最小网关与公开导出。**

沿用 model/deepseek.py 的构造验证、标准库同步 _transport/_NoRedirect、内容块隔离、HTTP/网络异常分类、响应choices/usage/finish_reason/指纹规范化代码，不修改原文件，不建立公共基类。新文件类名 QwenModelGateway，模块/类文档使用中文；身份、请求、响应差异为以下精确代码：

```python
_MODEL = "qwen-plus-2025-12-01"
_REVISION = "2025-12-01"

@property
def identity(self) -> ModelRuntimeIdentity:
    return ModelRuntimeIdentity(model_name=_MODEL, model_revision=_REVISION)

# invoke 发送前
if (
    request.model_name != _MODEL
    or request.model_revision != _REVISION
    or request.scenario_id is not None
    or not 0 < request.max_output_tokens <= 32768
):
    raise ModelGatewayError("invalid_configuration", retryable=False) from None

# messages 仍按已有信任块组装，wire body 不使用调用方任意模型值。
body = json.dumps({
    "model": _MODEL, "messages": messages,
    "enable_thinking": False, "enable_search": False,
    "response_format": {"type": "json_object"},
    "stream": False, "max_tokens": request.max_output_tokens,
}, ensure_ascii=False).encode("utf-8")
wire_request = Request(
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    data=body,
    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
    method="POST",
)
raw = self._transport(wire_request, request.timeout_seconds)

# json.loads 后先确认 data 是 dict，再验证真实 model；该异常不并入普通结构错误。
try:
    name = _IDENTIFIER.validate_python(data["model"])
except (KeyError, ValidationError):
    raise ModelGatewayError("model_identity_unverifiable", retryable=False) from None

# 返回对象其他字段保持原解析规范。
result = ModelCallResponse(
    payload=content, model_name=name,
    model_revision=_REVISION if name == _MODEL else None,
    finish_reason=normalized_reason, usage=usage,
    response_fingerprint=hashlib.sha256(raw).hexdigest(),
    provider_system_fingerprint=fingerprint,
)
```

model/__init__.py 加 `from .qwen import QwenModelGateway` 和 __all__ 条目；包级 __init__.py 同样导入并导出 QwenModelGateway。
identity 每次返回副本，构造不发送HTTP。网关不能读取环境变量或真实凭据。
若测试发现真正缺陷，先补最小复现 RED；如果新追加覆盖立即GREEN，明确记录为既有行为回归，不虚构RED。

- [ ] **Step 4: 验证 GREEN、相关现有回归及静态检查后提交。**

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests/model/test_qwen.py packages/agent/tests/pipeline/test_qwen_real_evaluation.py packages/agent/tests/model/test_deepseek.py packages/agent/tests/pipeline/test_preflight.py packages/agent/tests/pipeline/test_real_evaluation.py -q
uv run --offline --locked --project packages/agent ruff check packages/agent
uv run --offline --locked --project packages/agent ruff format --check packages/agent
uv run --offline --locked --project packages/agent mypy --config-file packages/agent/pyproject.toml packages/agent/src
git diff --check
```

预期测试全通过、静态exit0；仅格式化新增文件与新增导入，必要时只修自己的代码。提交限定本任务五文件，消息 `feat(agent): add frozen Qwen snapshot gateway`。规格审通过后进行质量审，问题修复后重审；不修改公共门禁或真实数据。

## Task 2: 宿主说明、证据记录与最终门禁

**Files:**
- Modify: packages/agent/README.md
- Modify (local only): E:/Projects/RequirementSeeker/docs/HANDOFF.md
- Create (local only): E:/Projects/RequirementSeeker/docs/execution/2026-10-04-qwen-snapshot-gateway.md
- Update checkboxes/evidence: 本实施计划及对应规格

- [ ] **Step 1: 编写准确中文说明与不调用网络的构造示例。**

```python
from requirementseeker_agent import QwenModelGateway

def make_qwen_gateway(api_key: str) -> QwenModelGateway:
    return QwenModelGateway(api_key=api_key, max_input_tokens_per_call=16000)
```

说明配置 model=qwen-plus-2025-12-01/revision=2025-12-01；密钥由宿主显式传入；正式入口仍需另行真实评测/费用授权。
准确陈述已核验快照白名单映射、供应商声明信任边界、未知模型保留名称/None、缺失身份无法记录真实名称、fingerprint不当revision、JSON object不等于Schema强制、输入预算估算和同步取消限制。
独立探测仅29+5Token/HTTP200；适配器本身只有离线验证，不能称已真实端到端联调。24/24仅结构可评测，不称人工语义金标。
记录真实 RED/GREEN 数量、测试命令、静态检查、审查结果、提交和保留事项；没有证据的结果不写成功。
根仓库 .git/info/exclude 明确要求 HANDOFF 与 execution 仅本地保存，不进入上游提交；隔离工作树没有这些历史文件。在根仓库 HANDOFF 顶部添加最新状态，不删除历史 DeepSeek/标签修复证据，执行记录也留在根仓库忽略目录。不得 force-add 这些文件，源码仍只改隔离分支。

- [ ] **Step 2: 完整验证与文件审查。**

```powershell
uv run --offline --locked --project packages/agent pytest packages/agent/tests -q
uv run --offline --locked --project packages/agent ruff check packages/agent
uv run --offline --locked --project packages/agent ruff format --check packages/agent
uv run --offline --locked --project packages/agent mypy --config-file packages/agent/pyproject.toml packages/agent/src
uv build --offline --project packages/agent
git diff --check
git diff --stat 209e15d..HEAD
git status --short
```

预期测试数高于354且零失败；Ruff/格式/mypy/离线sdist及wheel/diff均exit0。检查不包含.local-data、真实评论、密钥；文档不得把本地门禁当远端CI。
仅提交隔离分支的 README、规格和计划，消息 `docs(agent): document Qwen snapshot trust and offline verification`；本地 HANDOFF/execution 不提交。随后规格审、质量审、最终独立整体审，控制者复核并重跑门禁。

- [ ] **Step 3: 收尾。**

使用 finishing-a-development-branch 技能，默认保留隔离工作成果，不自动扩大为正式评测。根据既有发布授权与当前门禁决定是否可进入发布核对；未做发布则明确未推送/未合并。汇报精确完成项、测试和仍待单独授权的真实评测，不要求反复确认已经认可的设计。

## 跟踪

- [x] 用户确认书面规格、隔离分支、354项基线。
- [x] 计划自检：Task1覆盖请求/响应/身份/缓存/审计；Task2覆盖文档及门禁；不改兼容行为。
- [ ] Task1 RED/GREEN、规格审、质量审。
- [ ] Task2文档/完整验证、规格审、质量审。
- [ ] 最终整体审与控制者新鲜验证。
