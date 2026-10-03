# DeepSeek Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 实现官方 DeepSeek 文本协议 adapter，独立审计后端指纹，不伪造 revision，不开放正式评测。

**Architecture:** 标准库同步 HTTP、固定官方端点、可注入传输边界；先扩展可选指纹字段，再实现 adapter，最后同步中文文档。配置身份及实际 revision 均为 None，preflight 不变。

**Tech Stack:** Python 3.12、urllib、Pydantic、pytest、Ruff、mypy、uv。

工作目录：`C:/Users/Fantason/.codex/worktrees/m2-evaluation-preflight/RequirementSeeker`。用户已批准设计和继续实施，并允许子代理；按任务串行分派新实现代理，先规格审再质量审。不推送、不建 PR、不合并，不读取真实数据或凭据，不访问模型端点。

## Task 1：响应与审计的独立指纹

文件：修改 `packages/agent/src/requirementseeker_agent/model/types.py`、`contracts/analysis.py`、`pipeline/invocation.py`、`packages/agent/schemas/analysis-result.schema.json`；测试 `packages/agent/tests/pipeline/test_invocation.py`、`tests/model/test_gateway_contract.py`、`tests/contract/test_schema.py` 及现有 identity fixture。

- [ ] 在响应及审计增加 `provider_system_fingerprint: Identifier | None = None` 的失败测试。旧 JSON 缺失时为 None；新值 JSON 往返保留。调用成功、身份失败、取消和超预算审计均保留该值；无响应错误为 None。

核心断言使用现有测试辅助函数：

```python
supplied = response({"signals": []}).model_copy(
    update={"provider_system_fingerprint": "fp_synthetic"}
)
result = invoke_model(
    analysis_request(), model_call(), ScriptedGateway([supplied]), ledger(), input_tokens=100
)
assert result.audits[0].provider_system_fingerprint == "fp_synthetic"
assert result.audits[0].actual_revision == "m2-fixture-1"
```

- [ ] 运行 `uv run --offline --locked --project packages/agent pytest packages/agent/tests/pipeline/test_invocation.py packages/agent/tests/model/test_gateway_contract.py -q`，确认新增断言因字段缺失失败。
- [ ] 两个 Contract 增加上述默认字段；`_audit` 增加以下参数，不修改 revision 语义：

```python
provider_system_fingerprint=(
    None if response is None else response.provider_system_fingerprint
),
```

- [ ] 运行 Schema 测试观察权威模型与已导出 Schema 不一致，再更新 JSON Schema（沿用既有导出命令、保留格式），在 identity fixture 增加合成指纹字段及往返断言。
- [ ] 定向回归通过后提交 `feat(agent): audit provider backend fingerprints`。规格和质量复审通过再进入 Task 2。

## Task 2：固定官方协议 adapter

创建 `packages/agent/src/requirementseeker_agent/model/deepseek.py` 和 `packages/agent/tests/model/test_deepseek.py`，修改 model 和包根 `__init__.py` 导出 `DeepSeekModelGateway`。

- [ ] 先写完整合成响应/HTTP 替身和失败测试，不访问网络。入口：`DeepSeekModelGateway(api_key="synthetic-key", max_input_tokens_per_call=16000, transport=...)`。传输边界参数为 urllib Request 与整数超时，返回字节；默认传输使用 urllib opener 且禁用重定向。
- [ ] 测试请求固定 URL `/chat/completions`、Authorization、UTF-8 JSON、独立角色、禁用 thinking、非流式、json_object、输出/超时。system block 只拼系统内容，不可信/repair block 分别作为 user；系统追加明确 JSON 指令。

```python
assert sent_body["model"] == "deepseek-flash"
assert sent_body["thinking"] == {"type": "disabled"}
assert sent_body["response_format"] == {"type": "json_object"}
assert sent_body["stream"] is False
assert actual.model_name == "deepseek-flash"
assert actual.model_revision is None
assert actual.provider_system_fingerprint == "fp_synthetic"
```

- [ ] 运行 `uv run --offline --locked --project packages/agent pytest packages/agent/tests/model/test_deepseek.py -q`，确认缺少 adapter 导致 RED，不能以测试拼写错误为证据。
- [ ] 实现仅此具体协议。配置 identity 模型 deepseek-flash/revision None；能力 text/structured True、images False，输入限额须为正整数。凭据须非空且可安全作为单行 header，仅存私有属性，不进入 repr、Contracts 或异常。发送前拒绝模型不匹配、revision 非空、scenario_id 非空、输出为零或超 provider 上限 393216。API 密钥由调用方显式传入，不读环境。
- [ ] 非流式响应解析校验字典、model 标识、单个 assistant choice 的字符串 content、finish_reason 字符串；provider 指纹缺失/null 为 None，非空值按 Identifier 校验。payload 保留原字符串，供管线阶段验证；实际 revision 恒 None。返回 SHA-256 原始响应字节摘要，不混淆 provider 指纹。usage 缺失/null 为 None；存在则严格校验 prompt/completion/total 数字，拒绝 bool/负数/不一致。
- [ ] HTTP 401/403→authentication_failed 不重试；429→rate_limited 可重试；408/504及 TimeoutError→timeout 可重试；其他5xx、URLError/连接 OSError→transport_error 可重试；其他 HTTP（含重定向）、配置和协议错误→invalid_configuration 不重试。异常用 `from None` 隔离敏感上下文；不包含 HTTP body、key、prompt，不内部重试。Pydantic/JSON/Unicode 格式错误也转换为非秘密错误。
- [ ] 增加并实际观察每组 RED：上述各状态/错误、畸形响应、usage 缺失/非法、后端指纹非 revision、请求配置零调用。用 monkeypatch 替换默认 opener 底层，验证默认路径超时传递和禁用重定向，而非仅测注入 transport。
- [ ] 正式 preflight 对 requested revision None 报 requested_revision_required；对自行填写 revision 报 gateway_revision_required，两者传输计数均0。普通 Scenario revision None 保持通过；普通管线搭配 DeepSeek 合成响应能保留实际身份、None revision 和指纹。
- [ ] 定向测试及 mypy/Ruff 通过后提交 `feat(agent): add fail-closed DeepSeek gateway`，规格审、质量审通过。

## Task 3：中文文档与全门禁

修改 `packages/agent/README.md`、`docs/contracts/agent-v1.md`、`docs/development/2026-09-07-m2-acceptance.md`，记录真实测试结果而非预测。修改中文配置示例，不创建自动读取环境/付费调用的演示脚本。

- [ ] 展示显式构造但不 invoke 的代码：

```python
from requirementseeker_agent import DeepSeekModelGateway

# api_key 由宿主秘密管理提供；此处仅构造，不执行网络调用。
gateway = DeepSeekModelGateway(api_key=api_key, max_input_tokens_per_call=16000)
assert gateway.identity.model_revision is None
```

- [ ] 说明 provider_system_fingerprint 与实际 revision/响应摘要不同，新消费者读旧审计但严格旧消费者需更新 Schema；实际模型身份可读取不等于正式版本冻结，DeepSeek 正式入口当前零调用拒绝。24/24 仅结构可评测，issue #8 独立待修。
- [ ] 完整验证：

```sh
uv run --offline --locked --project packages/agent pytest packages/agent/tests -q
uv run --offline --locked --project packages/agent ruff check packages/agent/src packages/agent/tests packages/agent/examples
uv run --offline --locked --project packages/agent ruff format --check packages/agent/src packages/agent/tests packages/agent/examples
uv run --offline --locked --project packages/agent mypy packages/agent/src
uv build --offline --project packages/agent
git diff --check
```

- [ ] 文档规格审/质量审后提交 `docs(agent): document DeepSeek version limitations`；独立总审设计提交 e555a0a 到最终 HEAD，修复阻断并重跑门禁。
- [ ] 主代理更新忽略的根 `docs/HANDOFF.md` 和 `docs/execution/2026-10-03.md`；保留隔离分支、工作树，不发布。检查根 main 不变、真实数据未跟踪。
