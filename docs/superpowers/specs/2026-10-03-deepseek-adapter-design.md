# DeepSeek V4.1 Flash adapter 设计

## 目标与批准范围

用户选择 DeepSeek V4.1 Flash，并批准先实现 adapter 与离线测试、保留版本不可验证时的正式评测拒绝行为。此设计扩展已完成的 M2 preflight，不降低其版本冻结要求，不授权真实模型调用或费用。

官方 API 请求模型为 `deepseek-flash`，服务为 `https://api.deepseek.com`。官方 Chat Completions 响应定义提供 `model` 和表示后端配置的 `system_fingerprint`；当前公开契约没有将该指纹定义为不可变模型 revision。因此 adapter 不得从请求、展示名称、发布日期或指纹推导实际 revision。

参考资料（2026-10-03 查阅）：

- [官方更新日志](https://api-docs.deepseek.com/zh-cn/updates/)
- [Chat Completions 契约](https://api-docs.deepseek.com/api/create-chat-completion/)
- [模型列表契约](https://api-docs.deepseek.com/api/list-models/)

## 方案与取舍

采用同步、非流式 Chat Completions adapter，使用 Python 标准库 HTTP 客户端，不引入 SDK 依赖。HTTP 边界可注入测试替身；默认网络路径仅在调用方显式 invoke 时执行。与引入通用 SDK 相比，这个固定单端点实现更小，且不引入隐藏重试。替代方案是寻找提供不可变 revision 证明的托管服务，但这需要另行选择部署和契约，不纳入本轮。

adapter 作为具体生产协议实现交付，但不代表通过正式真实评测准入。普通 `analyze_m2` 的兼容语义不变；文档明确普通入口不得被用作绕过正式评测门禁的途径。

## 身份和能力

- adapter 配置身份为 `deepseek-flash`，`model_revision=None`；读取 identity/capabilities 不访问网络或读取凭据。
- 原始响应 `model` 直接进入 `ModelCallResponse.model_name`；无可信 revision 时 `model_revision=None`，不回填请求值。
- 在响应类型和调用审计新增可选 `provider_system_fingerprint`，原样保留非空 provider 指纹，默认 None，兼容旧记录。该字段独立于 `response_fingerprint`（现有响应摘要）和 `actual_revision`。同步 result Schema、fixture 与往返测试。
- 当前仅实现文本输入与 JSON object 输出，支持普通 M2 的结构验证/修复流程；不声称 provider 强制执行阶段 JSON Schema，不发送图片。
- 输入限制使用显式正整数配置，输出上限和超时遵循单次请求；不在本轮承诺精确 Token 估算或货币预算。

## 调用路径

调用方显式提供凭据与非秘密配置，凭据不进入 Contract、审计、缓存或异常文本。adapter 仅连接固定官方 HTTPS 端点，不增加第三方 base URL 配置。

发送前核对请求模型、拒绝不受支持的非空 revision，并拒绝非法调用参数，失败不请求网络。把 system block 作为系统内容，把不可信数据/repair block 保留为独立用户内容，不将外部数据提升为 system；禁用 thinking 以使用固定非推理 JSON 输出路径。设置 `stream=false`、`response_format=json_object`、显式最大输出和超时；确保提示中明确要求 JSON。不记录提示或模型正文。

adapter 将返回的 assistant content 作为原始 payload，由既有管线验证；映射 finish reason、输入/输出实际用量和响应摘要。异常/缺失字段须转换为稳定、非秘密 gateway 错误，不把未经校验的正文当成功结果；用量缺失保留 None，交由现有结算规则处理。

HTTP 超时、429、临时服务/连接异常使用既有错误码和可重试标记；认证、请求配置、协议格式错误不可重试。adapter 本身不重试，重试只由现有调用边界管理。关闭 HTTP 重定向，避免凭据随请求流向其他地址。所有诊断均不得包含密钥、Authorization、原始响应、提示或评论。

## 正式评测 fail closed

`revision=None` 的正式请求仍报 `requested_revision_required`。即使用户自行填写 revision，该 adapter identity 仍为 None，preflight 报 `gateway_revision_required`，并且 HTTP 调用计数为零。不能使用请求字符串或配置指纹放行。

未来只有 provider 契约提供可验证 revision，且另行设计并通过测试后，才能扩展版本身份能力。此实现只能完成生产 adapter 的实际模型身份读取，不能宣称已支持 DeepSeek 的正式版本冻结评测。

## 离线验收

严格先 RED 后 GREEN，全程使用合成 HTTP 替身，不读取真实凭据或调用任何真实端点：

1. 请求端点、角色/信任边界、JSON 模式、非流式、输出与超时正确；凭据仅在 Authorization 中。
2. 响应实际 model 被保留；实际 revision 仍 None；provider 指纹与响应摘要互不混淆。
3. 指纹写入成功/失败调用审计，旧记录缺失字段仍可解析，Schema 一致。
4. 正式入口两种 revision 配置均在零 HTTP 调用时失败；普通 Scenario gateway 的 revision=None 继续通过。
5. 请求身份错误、认证、超时、限流、服务故障、重定向、格式错误均正确映射，异常不泄漏秘密/正文；adapter 无内部重试。
6. Agent 完整测试、Ruff、format、mypy、离线构建及 diff check 通过，并进行独立复审。

## 文档与范围边界

提供中文配置示例，默认不执行真实调用、不加载环境密钥。明确模型别名不等于冻结 revision、后端指纹不等于权重版本、普通调用不是正式评测。

不执行真实语义评测、不修改标注或真实数据、不处理 `export-labels` issue #8、不推送/建 PR/合并。24/24 仍只称结构可评测。同步取消仍限调用边界；不新增流式、图片、工具调用、持久缓存或 pricing 功能。

## 实施组织

预计三个工作单元：adapter 与 HTTP 测试、指纹审计/Schema 集成、中文文档和独立复审。身份与审计有顺序依赖，实施串行；上下文或审查负担较大时委派独立复审，避免多人同时编辑同一文件。沿用现有隔离分支/工作树，不改根 main。
