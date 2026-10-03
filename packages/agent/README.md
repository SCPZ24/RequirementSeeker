# RequirementSeeker Agent

M0 提供输入输出契约、JSON Schema 和离线校验。M1 在已验证的单视频有效快照上执行确定性三人共识判定。M2 增加采样、预算、缓存、需求信号与同视频聚类管线，提供确定性场景网关和标准库实现的 `DeepSeekModelGateway`。DeepSeek 适配器已交付，但不具备正式评测所需的可核验 revision；真实模型语义评测、采集和机会入库尚未完成。

从仓库根目录运行：

```sh
uv sync --project packages/agent --locked
uv run --project packages/agent rs-agent --help
uv run --project packages/agent rs-agent fixtures packages/agent/tests/fixtures/manifest.json
uv run --project packages/agent pytest packages/agent/tests -q
```

安装依赖需要访问包索引或已存在的缓存，安装后的校验不需要网络、模型 Key 或平台登录。

## M1 共识规则

调用方先提供完整有效的 `AnalysisRequest`，再传入显式 `NeedCluster`：

```python
from pathlib import Path

from requirementseeker_agent import AnalysisRequest, NeedCluster, evaluate_consensus

request = AnalysisRequest.model_validate_json(
    Path("packages/agent/tests/fixtures/valid/request.json").read_text(encoding="utf-8")
)
cluster = NeedCluster(
    cluster_id="cluster-1",
    comment_ids=["comment-1", "comment-2", "comment-3"],
    summary="合成需求簇",
)
decision = evaluate_consensus(request, cluster)
print(decision.model_dump_json(indent=2))
```

规则从可信请求复制原始评论文本，每位作者只选择一条确定性的代表评论。未知评论引用、未知视频作者、缺失作者、视频作者本人和重复作者不能组成通过结果。M1 不判断评论是否表达相同需求；信号识别与语义聚类由 M2 管线处理。

## M2 离线演示

从仓库根目录运行：

```sh
uv run --project packages/agent python packages/agent/examples/m2_fake_demo.py
```

演示使用仓库中的合成请求和 `ScenarioModelGateway`，不连接平台或模型服务。JSON 输出包含 `completed` 状态、稳定信号与簇 ID、一项通过的三人共识决策，以及评论、调用和 Token 用量。宿主普通 M2 库调用时需提供同一视频的 `AnalysisRequest`、`SamplingManifest`、`SamplingPlan`、模型网关与语义缓存，再调用 `analyze_m2`；可通过可选的 `cancellation_probe` 在运行中请求取消。

完整说明见仓库 [开发说明](../../docs/development/agent-setup.md)，线协议和稳定原因码见 [Agent v1 契约](../../docs/contracts/agent-v1.md)，M2 当前验收边界见 [离线验收记录](../../docs/development/2026-09-07-m2-acceptance.md)。`evals/gold.json` 只是业务评测种子；真实模型语义评测尚未完成。

## M2 真实评测身份准入（合成演示）

从仓库根目录运行完整的离线示例：

```sh
uv run --offline --locked --project packages/agent python packages/agent/examples/m2_fake_demo.py --real-evaluation
```

脚本把合成请求配置为 `scenario-model` / `m2-fixture-1`，组装 `c1`、`c2`、`c3` 评论与采样计划，调用公开入口 `analyze_m2_real_evaluation`。输出应为 `completed`，包含三个信号、一个通过三人共识的簇和两条 requested/actual 身份一致的审计。默认不带参数仍运行普通 `analyze_m2`。两种模式都只使用确定性 `ScenarioModelGateway`，无需 Key，不产生真实模型费用。

宿主真实评测必须使用 `VersionedModelGateway`：先由纯离线 `preflight_real_evaluation` 核对请求和网关配置的模型名称、非空 revision、文本与结构化输出能力，得到不可变 `FrozenModelIdentity`。正式入口将这一身份贯穿信号、聚类、结构修复和合并调用；每个实际响应还必须提供相同的模型名称与 revision。身份缺失或漂移会形成不可重试的失败结果，已发生用量仍结算，失败阶段不能写入缓存。缓存同时区分模型名称、冻结 revision 和是否要求身份验证，普通模式的缓存不能绕过正式入口的核对。

真实适配器须从 provider 可核验的配置和响应获取 revision；不得用 requested revision 回填 actual revision，也不能仅靠别名或自填字符串声称版本已经冻结。调用方仍需单独取得真实模型及费用授权；preflight 不读取密钥、不授予付费调用权限。同步网关仅在调用边界前后观察取消，不能中断已经在途的请求。预算约束仍在批次规划和调用时执行；preflight 不检查累计输入预算与单次上下文上限的兼容性，也不提供货币上限或精确 Token 用量保证。

## DeepSeek 宿主构造与版本限制

宿主显式传入已授权的 Key 和每次输入 Token 上限；以下完整函数只构造网关，不读取环境变量，也不发起请求：

```python
from requirementseeker_agent import DeepSeekModelGateway


def make_deepseek_gateway(api_key: str) -> DeepSeekModelGateway:
    return DeepSeekModelGateway(
        api_key=api_key,
        max_input_tokens_per_call=16000,
    )
```

适配器固定使用官方端点 `https://api.deepseek.com/chat/completions` 和官方模型别名 `deepseek-flash`，通过 Python 标准库发送单次同步、非流式文本请求，不依赖 provider SDK。请求显式关闭 thinking，使用 `response_format={"type": "json_object"}`；结构化输出能力表示 JSON object 模式，不能理解为 provider 已强制执行本项目 JSON Schema。信号和聚类阶段仍在本地解析原始 JSON 并验证结构与引用。图像能力为 false。

响应中的实际 `model` 和 assistant `content` 分别进入 `model_name` 与 `payload`，实际 revision 始终为 `None`，不会以请求值或模型别名回填。可选 `system_fingerprint` 单独保存在响应及审计的 `provider_system_fingerprint`；它是后端标识，不是不可变权重 revision，也不是本地对原始响应计算的 SHA-256 `response_fingerprint`。

因此，正式入口 preflight 遇到请求 `revision=None` 时抛出 `requested_revision_required`；给请求任填非空 revision 时，因网关 revision 仍为空而抛出 `gateway_revision_required`。两种情况都不会发起 HTTP 请求。普通 `analyze_m2` 可使用 revision 为空的配置，但不能把普通分析当作绕过正式身份准入的真实评测结果。

每次传输使用调用请求的 `timeout_seconds`，适配器没有内部重试；重试仍由现有管线及稳定 `ModelGatewayError` 原因码管理。默认传输禁止重定向，Key 仅用于固定端点认证头，错误诊断不回显 Key、响应正文或底层异常。测试可显式注入 `transport`；当前验证使用离线传输替身，尚未证明真实 provider 的端到端兼容性。
