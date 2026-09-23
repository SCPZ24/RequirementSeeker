# Agent M2 模型网关、需求信号与聚类设计

- 日期：2026-09-07。
- 状态：对话设计与书面规格均已批准，进入实施计划与 TDD 执行。
- 基线：upstream main `55e08a6`，包含已合并的 M0/M1。
- 依据：[Agent 交付计划](../../development/2026-09-05-agent-delivery-plan.md)、[Agent v1 契约](../../contracts/agent-v1.md)、[ADR-001](../../architecture/ADR-001-agent-foundation.md)、[M1 验收记录](../../development/2026-09-07-m1-acceptance.md)。

## 1. 目标与完成边界

M2 在不改变 `AnalysisRequest v1` 线协议的前提下，交付可由确定性假模型驱动的需求信号识别和同视频语义聚类链路。链路包含动态采样计划、确定性分批、模型网关、能力声明、预算、有限重试、结构修复、缓存、引用校验、跨批次聚类和 M1 共识复核。

M2 的离线完成标志是：假模型场景矩阵、适配器一致性、预算/缓存/取消和语义管线测试全部通过，并能从合成输入得到可信的 `NeedSignal`、`NeedCluster` 与 `ConsensusDecision`。这只是 H0 的组成部分，不是完整 H0 或 H1。

以下内容不属于 M2：

- 视频字幕、帧、OCR 或音频的生产获取；M3 只消费受控媒体上下文。
- 软件机会推导、重运营过滤和首批用户来源；属于 M3。
- 历史机会召回、合并决策、提交幂等和正式证据包；属于 M4。
- 采集器 DOM 选择器、登录、Cookie、平台限流绕过或任务调度；属于独立采集任务或宿主。
- 将真实模型尚未运行的结果写成真实语义评测。

## 2. 已批准决策

1. 先使用确定性、高保真的场景假模型；真实模型接入时机由模块负责人根据门槛判断。
2. 假模型与真实模型共享接口、Schema、预算、重试、取消、缓存和适配器一致性测试。
3. 至少取得 12 个脱敏试点视频后进行有限真实模型冒烟；24 个试点视频标注完成后进行完整真实模型评测。
4. 评测采用精确率优先：信号和聚类精确率均不低于 90%，聚类召回率不低于 75%，伪造或篡改证据接受数为零。
5. 评论处理量采用动态目标；评论数据质量是主判断，视频方向和视频相关因素是次要判断。
6. 视频相关因素对动态目标的数量调整限制在基础覆盖的 ±20%，以后只根据真实评测校准。
7. 动态目标不能取消预算、上下文和调用次数的硬上限。
8. 采集器与 Agent 模块隔离。采集器输出本地原始数据，M2 只消费脱敏、标准化结果。
9. 新增独立 `SamplingManifest` 配套契约，不向严格的 `AnalysisRequest v1` 添加可选字段。

## 3. 总体架构

```mermaid
flowchart LR
    A[双平台原始数据] --> B[脱敏与格式校验]
    B --> C[候选评论池与 SamplingManifest]
    C --> D[分层动态采样策略]
    D --> E[AnalysisRequest v1 有效快照]
    E --> F[确定性 Token 分批]
    F --> G[缓存与预算预检]
    G --> H[ModelGateway]
    H --> I[Schema 与评论引用校验]
    I --> J[同视频跨批次聚类]
    J --> K[M1 三人共识复核]
```

组件边界：

| 组件 | 责任 | 不负责 |
|---|---|---|
| `SamplingPolicy` | 评估数据质量、计算目标数量和分层配额 | 读取网页或持有登录态 |
| `BatchPlanner` | 在预算内按稳定顺序拆分评论和合并输入 | 判断评论是否有需求 |
| `ModelGateway` | 执行一次结构化模型调用并标准化传输错误 | 信任模型引用或决定三人门槛 |
| `ScenarioModelGateway` | 按场景产生确定性成功与失败 | 证明真实语义质量 |
| `BudgetLedger` | 预留、结算并限制调用和 Token | 猜测缺失 usage 为零 |
| `SemanticCache` | 保存已验证的阶段结果并执行版本/期限失效 | 缓存未验证原始响应或永久保存正文 |
| `SignalPipeline` | 识别信号、核对评论 ID、生成稳定信号 ID | 生成正式机会 |
| `ClusterPipeline` | 合并同视频信号并生成稳定簇 ID | 跨视频拼接或放宽作者门槛 |
| M1 `evaluate_consensus` | 从可信请求复核作者和原文 | 判断语义是否相同 |

`SamplingPolicy` 在 `AnalysisRequest v1` 创建之前运行：输入是 `SamplingManifest`、同视频候选 `Comment` 列表和 `AnalysisBudget`，输出 `SamplingPlan`。宿主按计划选择的 ID 构造严格的 `AnalysisRequest v1`；M2 管线接收请求时只复核计划、清单和请求一致性，不对已采样请求重复采样。这样原始候选池可以大于 v1 请求的评论上限，同时仍保持 v1 线协议不变。

M2 保持同步 Python API。远程调用可在宿主工作线程运行；实时异步取消和跨进程调度留给 M6。M2 在每个步骤边界及每次模型调用前检查请求中的取消状态和内部取消探针。

## 4. 配套采样契约

### 4.1 `SamplingManifest`

`SamplingManifest` 使用独立 `sampling_schema_version="1.0"`，与 `AnalysisRequest.schema_version` 分开演进。所有对象继续拒绝未知字段。

必要字段：

| 字段 | 类型与语义 |
|---|---|
| `sampling_schema_version` | 固定 `1.0` |
| `manifest_id` | 当前采集清单稳定 ID |
| `platform` / `video_id` | 必须与请求视频一致 |
| `captured_at` | 带时区时间 |
| `reported_total` | 平台报告的评论总数，未知时为 null |
| `collection_target` | 采集任务计划取得的数量 |
| `collected_total` | 实际取得的去重前数量 |
| `pages_requested` / `pages_succeeded` | 采集完整性 |
| `available_strata` | `top`、`recent`、`replies`、`long_tail` 的可用集合 |
| `direction` | 软件工具、教程工作流、生活服务、电商营销、娱乐文化或未知 |
| `author_id_present` | 有稳定作者 ID 的评论数量 |
| `distinct_author_count` | 明确的不同作者数量 |
| `exact_duplicate_count` | 相同稳定评论 ID 的重复数量 |
| `normalized_duplicate_count` | 规范化正文完全相同的额外数量 |
| `video_metrics` | 可空的播放、点赞、收藏、分享、粉丝和制作观察值 |
| `stratum_comment_ids` | 每个层的候选评论 ID，ID 必须属于候选池 |

原始平台 ID 在脱敏后替换为数据集内稳定伪 ID。采样契约不携带 Cookie、Token、账号密码、任意本地路径或未经允许的远程媒体 URL。

### 4.2 评论数据质量门

派生值：

```text
target_completion = min(collected_total / max(collection_target, 1), 1)
page_success_rate = pages_succeeded / max(pages_requested, 1)
author_completeness = author_id_present / max(collected_total, 1)
duplicate_rate = (exact_duplicate_count + normalized_duplicate_count) / max(collected_total, 1)
```

质量状态：

- `usable`：目标完成率和页面成功率均至少 90%，作者完整率至少 80%，重复率不高于 25%，且至少有三位明确的不同作者。
- `degraded`：目标完成率和页面成功率均至少 60%，作者完整率至少 50%，至少两个采集层可用，且至少有三位明确的不同作者；动态目标乘以 1.20 扩大验证。
- `insufficient`：不满足 `degraded`。停止自动语义准入并返回 `sampling_data_insufficient`，不能通过无限增大样本掩盖采集失败。

重复率只依据稳定 ID 和确定性正文规范化计算。语义上的广告、玩笑或灌水由后续信号模型识别，不在采样前伪装成确定性事实。

### 4.3 动态目标

当平台总数未知时，使用实际候选数量作为 `population_size`，并在结果中标记 `population_total_unknown`。基础数量公式：

```text
base = min(
  population_size,
  ceil(32 + 14 * log2(max(population_size, 1))),
  request.budget.max_comments
)
```

方向系数：

- 软件工具、教程、学习、DIY、工作流：1.10。
- 生活、消费、本地服务、电商：1.00。
- 娱乐、游戏、文化、营销推广：0.90。
- 未知：1.00。

数据质量系数：`usable=1.00`，`degraded=1.20`，`insufficient` 不产生目标。

初始目标：

```text
initial_target = ceil(base * direction_factor * data_quality_factor)
target = min(initial_target, population_size, request.budget.max_comments)
```

目标还必须服从输入 Token 估算。若目标评论无法放入模型预算，`SamplingPolicy` 按分层配额减少数量并记录 `input_budget_limited`，不把截断伪装为完整覆盖。

### 4.4 分层组成与二级调整

初始配额：高互动/靠前 35%，最新 25%，回复链 20%，长尾 20%。同一评论出现在多个层时只保留一次，缺额依次由长尾、最新、高互动、回复层中的稳定候选补齐；每层内部按稳定评论 ID 排序。

视频质量在本设计中指“评论反映的视频质量或受众评价”，不是客观制作事实。至少三位不同且非视频作者明确评价同一方向后，才形成 `VideoReceptionSignal`。正向、负向和混合评价分别保留证据 ID，不使用点赞最高的单条评论替代独立作者。

二级调整只执行一次，避免采样反馈循环：

- 混合或相互矛盾的质量评价：当前运行可追加最多 20% 评论，优先长尾和最新层。
- 三位以上作者在多个层给出一致评价：不删除当前已分析评论；下次同版本计划可减少 10%，仍受最小三作者和预算门约束。
- 播放、点赞或账号影响力明显高，但缺少已校准平台基线：不改变数量，只把 10% 配额从高互动层移到长尾层，降低粉丝/热评集中偏差。
- 制作信息不足或评论反复指出看不清、听不清、缺步骤：不改变 M1 门槛，把 10% 配额转向回复链和最新层，并记录 M3 可能需要媒体上下文。

所有数量调整合计截断到 `[-20%, +20%]`。没有真实校准数据时不依据播放量绝对值自动增减数量。

## 5. 双平台真实样本规范

### 5.1 试点与扩展规模

首次真实模型接入的最低试点是 12 个脱敏视频，至少包含 4 个重复需求正例、4 个硬负例和 4 个边界例。完整试点为 24 个视频；H1 前扩展到至少 60 个视频。

24 个视频方向分布：软件/工具/效率 6，教程/学习/DIY/工作流 6，生活/消费/本地服务 4，娱乐/游戏/文化 4，电商/营销/推广 4。抖音与 Bilibili 尽量各 12 个；无法覆盖的平台独立标记，不能由另一平台结果代替。

评论规模按平台报告总数分三档，每档各 8 个视频：不超过 200、201–2,000、超过 2,000。

原始采集量：

- 总数不超过 200：采集全部。
- 201–2,000：采集 `max(200, ceil(总数 * 25%))`，最多 500。
- 超过 2,000：采集 `max(500, ceil(10 * sqrt(总数)))`，最多 1,000。

原始采集继续采用 35/25/20/20 分层比例。无法获得某种排序时保留实际分页顺序并记录，不声称随机采样。

### 5.2 文件和脱敏

原始数据只放在 `.local-data/m2-real/raw/<platform>/<video-key>/`：

```text
video.json
comments.jsonl
collection.json
```

脱敏工具输出到 `.local-data/m2-real/sanitized/`。视频、评论和作者 ID 使用数据集级秘密生成稳定 HMAC；秘密只保存在本机环境变量，不能写入输出。正文移除手机号、邮箱、精确地址、私聊账号和其他直接身份信息，同时保留需求语义。自动替换结果必须抽样人工复核。

数据按视频稳定拆分为开发 40%、校准 30%、留出 30%。同一视频及其评论、回复不能跨集合。模型和 Prompt 调整只能查看开发与校准集；留出集只在冻结候选版本后运行。

### 5.3 标注

每条评论标注 `need_signal`、`signal_kind`、`normalized_need`、`noise_kind` 和 `video_reception`。每个视频标注金标评论簇和争议原因。争议样本由第二次独立标注裁决；未裁决样本不进入阈值分母，产品运行时保守不准入。

合成数据用于故障、边界和回归；真实数据用于语义指标。两种结果分别报告。

## 6. 模型网关与假模型

### 6.1 同步端口

`ModelGateway.invoke(ModelCallRequest) -> ModelCallResponse` 是一次调用端口。请求包含：

- `invocation_id`、`stage`、模型配置引用和版本。
- 版本化系统指令与受控内容块。
- 预期输出 Schema 名称和版本。
- 最大输出 Token、超时和当前尝试号。

网关能力声明还必须给出 `max_input_tokens_per_call`。`BatchPlanner` 使用该适配器实际能力与本次剩余输入预算中的较小值作为单次批次上限；场景假模型声明 4,096 Token 仅作为离线测试能力，不成为真实模型或产品预算的默认值。

响应包含：

- 未信任的结构化载荷。
- 供应商模型名称、可得版本和结束原因。
- `TokenUsage | None`。

网关错误统一为：`timeout`、`rate_limited`、`transport_error`、`capability_unsupported`、`authentication_failed`、`cancelled`。密钥只由真实适配器从指定环境变量读取，不进入请求对象、哈希、日志或异常正文。

### 6.2 场景假模型

`ScenarioModelGateway` 根据显式场景 ID 和规范化输入哈希返回确定结果。它至少包含：

- 合法需求信号、无需求、争议信号和合法跨批次聚类。
- 坏 JSON、缺字段、额外字段、错误类型和超长输出。
- 不存在的评论 ID、跨视频 ID、篡改引文和重复簇成员。
- timeout、rate limit、普通传输错误、认证失败和能力不足。
- usage 正常、usage 缺失、取消前、取消后及修复成功/失败。

假模型不直接返回可信 Evidence。模型只提出评论引用、类型、摘要和簇关系；Agent 从请求生成稳定 ID并回查评论。

### 6.3 真实模型接入门槛

第一次有限冒烟必须同时满足：

1. Prompt、Schema 和管线候选版本已冻结。
2. 假模型全部场景和适配器一致性测试通过。
3. 预算、重试、取消和缓存测试通过。
4. 至少 12 个脱敏试点视频已完成初次标注。
5. 用户只提供模型名、兼容 API Base URL、能力和密钥环境变量名；不提供密钥值。

24 个试点视频完成裁决后运行完整真实评测。真实适配器必须通过相同契约测试；允许语义预测、真实 latency 和 Token usage 与假模型不同，不允许错误分类、证据安全、预算和取消行为不同。

## 7. Prompt、信号和聚类

### 7.1 Prompt 边界

Prompt 作为版本化包资源。评论、标题和简介放入明确的不可信数据区，任何要求改变规则、泄漏配置或执行工具的文本都只能作为评论内容。模型没有文件、网络、命令或工具权限。

信号阶段只请求：评论 ID、`pain|need|alternative|product_defect`、规范化摘要和视频评价方向。模型不能返回作者人数、可信原文或通过结论。

Agent 用 `hash(comment_id, kind, normalized_summary, prompt_version)` 生成稳定 `signal_id`。摘要规范化只统一空白和 Unicode 形式，不翻译、不改写原意。

### 7.2 引用校验

每个模型评论 ID 必须存在于当前请求且属于当前视频。未知、跨视频、重复或未进入信号阶段的引用拒绝当前输出，并最多触发一次结构修复。修复仍失败时返回 `model_output_invalid`，不产生簇或机会。

Cluster 阶段只能引用已验证信号所对应的评论 ID。Agent 用排序后的评论 ID、规范化摘要和聚类 Prompt 版本生成稳定 `cluster_id`。模型生成的 ID 被忽略。

### 7.3 跨批次合并

`BatchPlanner` 为最终合并预留 25% 输入 Token 预算。其余 75% 用于信号提取；每批按稳定顺序填充到估算 Token 上限，不按运行时随机数拆分。

第一阶段输出已验证的信号摘要和评论 ID。第二阶段使用这些标准化信号合并同视频簇。若一次合并放不下，按稳定区间进行树形合并；每层输出重新校验，且总调用数不能超过 `max_model_calls=8`。

无法在预算内完成最终合并时返回 `budget_exhausted`。已验证的批次结果可供同版本重放，但不能被包装为完整 M2 成功，也不能进入 M1 共识。

完成聚类后，对每个簇调用 M1 `evaluate_consensus`。模型不能放宽视频作者、缺失作者、重复作者或三人门槛。

## 8. 预算、重试和取消

`BudgetLedger` 分别跟踪评论、实际模型调用、输入 Token 和输出 Token。调用前先预留保守输入估算和最大输出；预留失败时不调用网关。单次输出上限由上层编排器按批次显式分配，信号模块不把整段运行预算或某个真实模型容量隐式当成单次上限，否则首次调用可能占满预算并阻止已批准的重试或结构修复。

- usage 完整：按实际数结算，释放未用预留。
- usage 缺失：整个预留保持已消费，审计 usage 为 null。
- 每次传输重试和结构修复都增加实际调用数并重新预留。
- timeout、rate limit 和传输错误最多按请求策略重试两次。
- Schema 或引用错误最多修复一次。
- 认证失败、能力不支持和无效配置不自动重试。
- 调用前取消不产生模型审计；调用中的取消产生 `cancelled` 审计；步骤间取消停止后续批次。

同一次运行最多 8 次模型调用、16,000 输入 Token 和 4,000 输出 Token；调用方可以降低或设为零，不能由 M2 自动提高。

## 9. 缓存与审计

缓存键使用规范化 JSON 的 SHA-256，字段为：

```text
stage
normalized_input_hash
sampling_policy_version
batch_manifest_hash
rules_version
prompt_version
schema_version
model_config_ref
model_revision
```

`run_id`、当前机器时间、密钥和未参与决策的展示字段不进入键。模型版本未知时明确使用 null；不能假定后续远程调用可重复。

只缓存通过 Schema、引用和阶段不变量校验的标准化信号或簇，包括合法无信号结果。不缓存 timeout、rate limit、传输错误、取消、坏输出、伪造引用或不完整最终合并。

缓存期限不晚于相关评论和媒体内容的最早到期时间。缓存命中后仍对当前请求重新校验评论 ID、视频、作者和原文。只保存完成阶段所需的最小标准化内容，不保存普通原始模型响应。

`ModelInvocationAudit` 只记录实际模型调用。缓存命中记录为内部 `CacheEvent`，字段包含键、阶段、命中/未命中/过期、来源结果哈希和时间；不能伪造一次成功模型调用。M4 若要把缓存事件加入线协议，应独立评审契约版本。

## 10. M2 中间结果和错误

M2 使用包内类型 `M2AnalysisResult`，不提前改变 `AnalysisResult v1`。字段包括：

- 运行、平台和视频 ID。
- `SamplingPlan` 与实际选择原因。
- 已验证 `NeedSignal`、`NeedCluster`、`ConsensusDecision`。
- 实际 `ModelInvocationAudit`、内部 `CacheEvent` 和最终预算快照。
- `completed_steps`。
- 状态：`completed`、`no_signal`、`budget_exhausted`、`cancelled`、`retryable_error`、`fatal_error`。
- 稳定错误码或耗尽资源；成功状态不携带错误码。

预算耗尽或取消可以保留已完成批次 ID 供审计和同版本重放，但 `clusters` 与共识只能包含完整完成并校验的结果。任何非 `completed`/`no_signal` 状态都不能被宿主当作新增机会。

错误映射：

| 情况 | M2 状态 |
|---|---|
| timeout/rate limit/transport 重试耗尽 | `retryable_error` |
| Schema/引用修复仍失败 | `retryable_error` + `model_output_invalid` |
| 认证、配置或能力不支持 | `fatal_error` |
| 数据质量不满足最低门 | `fatal_error` + `sampling_data_insufficient` |
| 调用或 Token 无法预留 | `budget_exhausted` |
| 用户取消 | `cancelled` |
| 合法无需求信号 | `no_signal` |

## 11. 评测与回归门槛

信号指标按评论计算；聚类指标按同视频评论对计算。预测同簇评论对中金标同簇的比例为聚类精确率，金标同簇评论对被正确找回的比例为聚类召回率。

完整真实试点评测门槛：

- 信号精确率至少 90%。
- 聚类精确率至少 90%。
- 聚类召回率至少 75%。
- 未知、跨视频、篡改或不存在引用的接受数为零。
- M1 作者和三人门槛回归全部通过。
- timeout、限流、坏输出、取消和预算故障不会产生完整机会。

争议样本在产品运行中保守不准入，在第二人裁决前不进入阈值分母。报告同时列出样本量、平台、方向、评论规模、逐例错误、上下文不足率、usage 缺失率、调用量和缓存命中率。

Prompt、Schema、模型配置或模型版本升级必须重新运行固定回归集。确定性不变量不得回归，信号或聚类精确率下降不得超过 2 个百分点；否则保持旧版本。

## 12. 测试策略

1. 采样契约测试：字段、平台/视频一致性、层 ID 归属、质量门和动态数量。
2. 分批测试：输入顺序变化、Token 边界、预留比例、树形合并和调用上限。
3. 网关一致性测试：同一测试套件运行于场景假模型和真实适配器。
4. 故障测试：超时、限流、传输、认证、能力、坏 JSON、Schema、引用、usage 缺失和取消。
5. 缓存测试：命中、合法无信号、过期、版本变化、输入变化和失败不缓存。
6. 管线测试：信号、同视频聚类、跨批次合并、相似词不同场景、提示注入和 M1 复核。
7. 评测测试：稳定视频拆分、指标计算、争议排除、回归阈值和逐例报告。
8. 构建门禁：pytest、Ruff、mypy strict、锁文件、sdist/wheel、离线安装和 `git diff --check`。

## 13. 交付顺序

1. 新增采样、网关、预算、缓存和 M2 中间结果的类型与契约测试。
2. 实现场景假模型及适配器一致性套件。
3. 实现动态采样与确定性分批。
4. 实现预算、重试、结构修复、取消和缓存。
5. 实现信号识别、引用校验、跨批次聚类和 M1 复核。
6. 扩充合成金标与故障矩阵，生成离线评测报告。
7. 样本达到门槛后实现真实 OpenAI 兼容适配器并运行独立联网评测。
8. 更新契约、README、验收记录和 Draft PR；M3–M6 不进入同一 PR。

实施按 [Agent M2 Implementation Plan](../plans/2026-09-07-agent-m2.md) 的逐步 TDD 任务推进。实现计划不得把真实模型未完成项、真实平台未覆盖项或 H1 状态写成已完成。
