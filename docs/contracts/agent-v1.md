# Agent v1 输入输出契约

- 状态：M0 初版，用户授权建立；真实采集/宿主接入尚未验收。
- 权威类型：[contracts](../../packages/agent/src/requirementseeker_agent/contracts/)。
- 导出文件：[请求 Schema](../../packages/agent/schemas/analysis-request.schema.json)、[结果 Schema](../../packages/agent/schemas/analysis-result.schema.json)。
- 可运行样例：[请求](../../packages/agent/tests/fixtures/valid/request.json)、[新候选结果](../../packages/agent/tests/fixtures/valid/result-new.json)、[全部样例索引](../../packages/agent/tests/fixtures/manifest.json)。

## 1. 通用约定

JSON 使用 UTF-8，版本字段必填且为 `1.0`。标识不得为空或包含空白，正文不得为空白文本，但不会自动裁切或改写正文。数字、布尔字段不接受字符串或布尔转整数。所有对象拒绝未知字段，包括密钥、生命周期或旧 Tag 权重字段。

时间必须为带时区 RFC3339 字符串，例如 `2026-09-05T12:00:00Z`。Python 调用也可传入有时区的 `datetime`；不得传入 Unix 数值或无时区时间。Python API 使用 `model_validate_json()` 解析线协议，使用 `model_dump_json()` 导出。

## 2. AnalysisRequest

一个请求只分析一个平台的视频有效评论快照。

| 字段 | 约定 |
|---|---|
| `run_id` / `retry_of_run_id` | 当前宿主运行 ID / 原运行 ID 或 null；两者不能相等 |
| `snapshot_id` | 调用方保存的快照引用；不是自动计算的内容哈希 |
| `snapshot_kind` | 固定 `active_video_snapshot`，不接受增量 delta |
| `analysis_time` | 显式分析时刻，校验不读取系统时钟 |
| `versions` | `rules`、`prompts`、`schema`；Python 内部字段 `schema_version` 使用线协议别名 `schema` |
| `model` | 配置引用、模型名称/可得版本、文本/图像能力声明；不得包含 API Key |
| `video` | 平台 bilibili/douyin、稳定 ID、作者 ID 或 null、标题、简介、HTTP(S) 来源、原生标签 |
| `comments` | 已组装的有效快照；可为空；相同评论 ID 不得重复 |
| `media_context` | 字幕、转写、OCR 或帧引用；可为空 |
| `history_revision` | 后续历史去重查询所针对的数据版本 |
| `budget` / `retry_policy` | 单视频预算与有限重试，默认值见 ADR |
| `cancellation_requested` | 请求开始时的取消状态快照；默认 false，实时取消通道由后续宿主接口实现 |

每条评论必须有平台、视频和评论 ID，作者 ID 缺失显式使用 null，父评论可为 null。没有采集父评论正文时可保留父 ID 引用，不要求快照包含完整回复树。评论不能回复自身。

评论的首次采集与到期时刻相差十天；只有在分析时刻仍有效的记录可进入请求。重复作者、作者自己、没有需求的评论仍可能是合法输入；它们能否计入共识由 M1/M2 判定，不能把“输入校验通过”当作“产生机会”。

媒体 `kind=frame` 时必须给 `asset_ref` 且 text 为 null；其他类型必须给 text 且 asset_ref 为 null。媒体引用不被 M0 解读为 URL 或本地路径。来源视频 URL 只存为来源资料，不由本命令访问，且禁止内嵌用户名/密码。

## 3. 领域结果

| 类型 | 含义与主要约束 |
|---|---|
| `NeedSignal` | 单评论候选痛点、需要、替代方案、产品缺陷与摘要 |
| `NeedCluster` | 当前视频内的候选评论 ID 集合和摘要；成员非空且不重复 |
| `ConsensusDecision` | cluster_id、passed、reason_code、证据；passed=true 至少三条不同作者/评论且同视频的代表证据；false 不声明正式通过证据 |
| `ContextDecision` | 文本足够、需要媒体、补充后足够或不足，附理由和上下文引用 |
| `OpportunityCandidate` | 标题、摘要、用户、场景、问题、现状、软件形态、最小功能、过滤理由、风险、证据 |
| `OpportunityMergeDecision` | 候选 ID、目标正式机会 ID、合并依据、追加证据 |
| `InferenceStep` | 稳定节点 ID、事实/推断类型、标题、完整正文、证据引用；数组顺序即展示顺序 |
| `ModelInvocationAudit` | 调用 ID、配置/模型、版本、输入哈希、步骤、尝试序号、状态、usage、错误码 |

`Evidence` 保存平台、视频 ID、评论 ID、作者稳定/匿名标识、原始引文。M0 能验证声明的结构和唯一性；M1/M4 必须将这些字段与可信输入比对、排除视频作者并组装正式证据快照。M0 不会验证一段引文是否由模型伪造，也不会执行语义过滤。

事实节点必须带引用，推断节点不能冒充事实。节点正文是给用户阅读的结论、依据与限制，不要求收集模型内部思考过程。永久保存前还须根据证据保留策略筛选正文。

`usage=null` 表示未获得完整用量，不是零 Token。获得完整用量时 input/output/total 为非负整数且总数必须相加一致。错误审计必须有 error_code，成功或取消审计不带 error_code；普通原始响应不进入审计类型。

## 4. AnalysisResult 与 outcome

结果封装包含线协议版本、运行/分析 ID、平台/视频 ID、已完成步骤、领域分析数组和非空 outcomes 数组。每个 outcome 通过 status 判别，不能把其他类别的负载混入。

| status | 必需负载 | 含义 |
|---|---|---|
| `new_candidate` | candidate、idempotency_key | 可以交由宿主检查并提交的新候选 |
| `merge_evidence` | merge、idempotency_key | 向指定已有机会追加证据的意图 |
| `rejected` | cluster_id 或 null、reason_code | 业务拒绝或真正没有有效需求 |
| `context_insufficient` | cluster_id、reason_code | 需要的内容仍不足 |
| `retryable_error` | error.code、error.step | 可重试阶段错误 |
| `fatal_error` | error.code、error.step | 不可自动重试错误 |
| `budget_exhausted` | step、resource | 达预算受控结束，不是模型失败 |
| `cancelled` | step | 在记录的步骤边界停止 |

同一视频可有多个需求簇，允许不同簇的成功与错误共存；宿主应汇总为部分完成等运行状态。结果中的所有代表证据必须属于封装声明的视频和平台。

M0 不执行从请求到结果的分析，不计算幂等键，不入库。结果校验也尚未实现所有跨记录引用与语义一致性检查；后续阶段在提交之前必须完成可信输入核验。测试结果样例仅用于约束数据形状，不能作为真实准入凭据。

## 5. 错误与版本管理

离线 CLI 校验失败输出 `valid=false` 和 errors 数组；每项只有 location 和稳定 code，不回显输入正文、模型配置值或 Python 堆栈。未知字段名可能本身含有敏感内容，因此 location 中该字段替换为 `<unknown-field>`，保留其已知父字段和数组序号。常见 code 包括：

- `literal_error`：版本、平台或状态枚举不支持。
- `extra_forbidden`：出现未定义字段。
- `int_type` / `bool_type`：类型不符。
- `timestamp_must_be_rfc3339_with_timezone`：时间格式不符。
- `timestamp_out_of_supported_range`：时区转换超出可表示的日期范围。
- `snapshot_must_contain_one_platform_video`：评论来源不属于当前视频。
- `snapshot_contains_inactive_comment`：评论尚未采集或已经到期。
- `representative_evidence_requires_distinct_authors`：代表作者重复。
- `file_unreadable` / `file_not_utf8` / `file_too_large`：文件无法用于校验。

字段关系错误的位置可能是所属对象根节点，code 给出具体违反的规则。标准 JSON Schema 不表达所有跨字段关系，消费方仍应使用本库校验或实现同等关系规则。

未知版本拒绝，不静默升级；新增字段或收紧合法负载约束时审查兼容性并更新版本、导出 Schema 和 fixtures。实际持久化与采集接入双方尚须执行契约测试，不能把本地 Schema 初版视为已经联调完成。
