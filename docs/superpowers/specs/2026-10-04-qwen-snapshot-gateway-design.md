# 千问固定快照网关设计

## 状态与范围

用户已认可口头方案、信任边界并确认书面规格；离线网关实现、Task1/Task2 独立规格/质量双审、最终整体审及控制者新鲜完整门禁均通过。当前保留隔离分支和工作树，未发布；不自动授予正式评测或新增付费调用权限。
基线为 main@209e15d14a60607e06a2a7814454e57ea6175b0a，隔离分支 codex/qwen-snapshot-gateway。
新增独立 QwenModelGateway，仅支持华北2（北京）的 qwen-plus-2025-12-01。
不替换 DeepSeek，不修改普通 analyze_m2 的 revision=None 兼容行为。
不刷新真实标签、不读取真实评论，不新增付费调用；推送、PR 和合并另行进行发布核对。

## 证据及信任边界

- [固定版本说明](https://help.aliyun.com/zh/model-studio/qwen-max)：官方区分滚动更新模型与固定历史快照。该说明支持快照机制，但不是对特定权重的独立证明。
- [快照能力](https://help.aliyun.com/zh/model-studio/qwen-plus)：qwen-plus-2025-12-01 明确列为日期快照，北京地域支持文本与结构化输出；其他地域能力不得直接沿用。
- [响应契约](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)：model 表示本次请求使用的模型；没有独立 revision 字段，system_fingerprint 当前为 null。
- 已授权的单次合成探测返回 HTTP 200，requested model 与 response model 均为 qwen-plus-2025-12-01；输入29、输出5 Token，返回 JSON 对象。独立 revision 字段不存在，system_fingerprint=null，无重试。

用户接受依赖供应商固定快照契约及逐次响应 model 的身份声明，不要求独立证明底层权重。
一次成功探测只能证明该次响应包含完整快照 ID，不能证明永久不变、不是回显、所有账号均可调用或模型语义质量。
日期 revision 是完整快照标识的本地规范化表示，不是假装供应商返回了独立 revision 字段。

## 最小架构

新增 model/qwen.py，沿用现有同步 ModelCallRequest/ModelCallResponse 与可注入传输风格。
在 model/__init__.py 和包级 __init__.py 导出 QwenModelGateway；不提取跨供应商公共基类，不重构 DeepSeek。
构造参数限于显式 api_key、正整数 max_input_tokens_per_call 和可选测试 transport。
网关不自动读取环境或秘密文件；宿主在取得授权后显式注入密钥。
固定官方地址 https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions，拒绝重定向。

配置身份固定为 model_name=qwen-plus-2025-12-01、model_revision=2025-12-01。
能力为 text/structured_output=True、images=False。structured_output 仅表示 JSON object 模式，不表示供应商强制本项目 JSON Schema。
输入预算仍沿用既有管线估算与预算机制，不声称网关实施精确 Token 或货币上限。

## 请求及响应

发送前拒绝不同模型、revision=None 或不同 revision、scenario_id 非空，以及输出上限不在1..32768的请求。
受控 system 块与外部数据/repair user 块分开；请求明确要求 JSON 对象。
使用 stream=false、enable_thinking=false、enable_search=false、response_format.type=json_object、max_tokens；不发送工具或图像，不在网关内重试。
timeout_seconds 取自现有请求；同步调用仅保留现有调用边界取消语义。

actual model_name 必须读取响应 model，绝不回填请求名称。
仅在响应 model 精确等于已核验 qwen-plus-2025-12-01 时，映射 actual revision=2025-12-01。
其他合法模型名称，包括别名、未知快照或其他模型，保留 actual model_name，actual revision=None，让正式管线拒绝并记录审计；不凭正则日期猜测未知模型的版本契约。
缺失或非法 model 导致非重试 model_identity_unverifiable；此时没有可信实际身份，审计实际字段为空，不能捏造。
解析载荷、finish_reason、usage，计算原始响应 SHA-256；provider_system_fingerprint 仅独立记录，不替代 revision。
成功传输且 model 为合法但未支持的名称、其余响应结构合法时，返回 revision=None 的响应以便既有管线结算用量、拒绝身份并保存实际信息。
无可解析响应时沿用现有失败结算策略，不声称能够获取供应商缺失的精确用量。

认证失败不可重试；429、超时和服务端/网络故障按现有网关错误码标记，由上层既有策略决定是否重试。
其余非法配置、响应结构错误不可重试；异常不包含密钥、认证头或供应商原始错误正文。

## 正式门禁与审计

复用 preflight_real_evaluation 的非空 revision、配置身份精确核对和能力校验。
复用 invoke_model 的冻结请求核对、实际响应核对、预算结算及失败审计。
缓存键继续绑定 FrozenModelIdentity 的 model 与 revision；本工作不修改缓存算法。
审计继续保存 requested_model_name/requested_revision 与 actual_model_name/actual_revision，不能仅写匹配后的身份。
已收到的未知快照保留原始模型名，actual_revision=None。未知快照不构造虚假 revision；已支持身份的 revision 错配由现有管线测试证明拒绝。

## 验收与验证

1. 普通 analyze_m2 + ScenarioModelGateway + revision=None 回归成功。
2. 正式入口 revision=None、请求模型/版本与配置不匹配，在零 HTTP 下失败。
3. 支持快照响应成功；响应别名、未知快照、不同模型、缺失/非法身份均 fail closed，无身份错误自动重试。
4. requested revision 与 actual revision 不同的现有正式管线回归测试保持失败，并验证失败审计。
5. 合成成功及错配调用审计包含 requested/actual 四身份字段；缓存命中和键内冻结 revision 均有断言。
6. 测试固定端点、禁重定向、关闭思考/搜索、JSON object 模式、内容块隔离、输出边界、超时、认证/429/服务端/网络错误、响应结构及密钥不泄漏。
7. 严格按测试先 RED、最小实现后 GREEN；所有 provider 响应均为合成 fixture，不加载真实凭据。
8. 新鲜完整 Agent 测试、Ruff、格式、strict mypy、离线构建及 git diff --check；文档明确真实联调仅限此前独立探测，尚未证明适配器端到端联调。
9. 24/24 只称结构可评测；export-labels 的独立修复已合并，不因此自动运行标签刷新。

预计约4–6个源码/测试/说明文件，按适配器、集成回归、审查三个阶段推进；范围不包含多模型目录、其他地域、流式输出、SDK迁移或正式评测执行器。

## 工作检查点

- [x] 核对项目现有身份、审计与门禁契约。
- [x] 对比快照身份映射与独立 revision 要求两种方案。
- [x] 用户认可快照契约信任边界与最小独立适配器方案。
- [x] 编写规格并自检范围、歧义和一致性。
- [x] 用户确认书面规格。
- [x] 编写实施计划。
- [x] Task1 测试先行、离线实现与规格/质量双审。
- [x] Task2 文档自审与新鲜完整离线门禁。
- [x] Task2 独立规格/质量双审。
- [x] 最终整体审与控制者最终复核。

Task1 提交 `94f8f5f42e8ef88592e97c92b946716ed3a2570c`：新增94项初次93 failed/1项既有 Scenario 回归 passed，随后94项通过；相关定向194项通过。独立规格审194项通过，独立质量审94项通过，无问题；内存撤导出的独立 RED 复现不视作初次时序证据。Task2 新鲜完整门禁为448 passed、Ruff检查通过、62文件格式通过、strict mypy32源码文件通过、离线0.3.0 sdist/wheel构建通过、diff检查通过；本任务改动仅三个跟踪文档。未push/PR/merge，没有新增付费调用、真实数据读取或标签刷新。完整本地记录保留在根仓库忽略的 `docs/execution/2026-10-04-qwen-snapshot-gateway.md`。

Task2 独立规格审与质量审均通过，无阻断问题；两审分别执行 README 构造示例并确认零 HTTP。最终独立整体审覆盖 main209e15d..3f10929 全部八文件，448 passed、Ruff/62文件格式/32源码strict mypy/diff检查通过，无 Critical/Important/Minor；控制者独立新鲜448 passed、跨包集成3 passed及全部静态/离线构建门禁通过。最后收尾仅更新证据状态，不改受审源码和测试。
