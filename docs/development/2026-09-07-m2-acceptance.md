# Agent M2 离线验收记录

## Task 9 评测边界

本记录只覆盖确定性场景网关和 `scenario-videos.json` 合成夹具的不变量。该夹具用于验证评论级信号精确率、同视频评论对聚类指标、待裁决样本排除和伪造引用安全门；视频拆分由独立合成输入验证。结果不是对真实模型语义质量的测量。

真实语义指标仍受以下两个数据准入条件阻塞：

- 缺少至少 12 个完成初次标注的脱敏试点视频。
- 缺少 24 个完成裁决的脱敏试点视频。

因此本记录不宣称 H0、H1 或生产就绪。

## 合成不变量结果

合成夹具包含 3 个视频和 9 条评论，其中 1 条为 `disputed_pending`，不进入指标分母；其余 8 条进入信号评测。夹具还覆盖被拒绝的未知、跨视频、篡改和不存在引用。

| 项目 | 合成结果 | 门槛 | 结果 |
|---|---:|---:|---|
| 信号样本数 | 8 | 仅报告 | — |
| 信号精确率 | 1.00 | ≥ 0.90 | 通过 |
| 预测聚类评论对数 | 2 | 仅报告 | — |
| 金标聚类评论对数 | 2 | 仅报告 | — |
| 聚类精确率 | 1.00 | ≥ 0.90 | 通过 |
| 聚类召回率 | 1.00 | ≥ 0.75 | 通过 |
| 已接受伪造引用数 | 0 | = 0 | 通过 |

这些数值只证明评测计算和安全门对已知合成输入的行为，不可外推为真实模型表现。

## 确定性验证

连续两次运行：

```text
uv run --project packages/agent pytest packages/agent/tests/evaluation packages/agent/tests/pipeline -q
90 passed in 0.20s

uv run --project packages/agent pytest packages/agent/tests/evaluation packages/agent/tests/pipeline -q
90 passed in 0.20s
```

两次用例数量和通过结果一致；耗时差异不属于评测输出。

## Task 10 公共入口与离线演示

本节及其发布门禁保留 Task 10 当时的历史快照；当前适配器状态见文末 DeepSeek 后续交付记录。

- 包版本为 `0.3.0`；M1 导出保持兼容。新增的顶层入口为 `SamplingManifest`、`SamplingPlan`、`ScenarioModelGateway`、`M2AnalysisResult`、`SAMPLING_POLICY_VERSION` 和 `analyze_m2`。
- 从仓库根目录运行 `uv run --project packages/agent python packages/agent/examples/m2_fake_demo.py`。脚本读取仓库自带的合成请求，调用确定性场景网关，不访问平台或模型服务。
- 实际输出为 `status=completed`，3 个稳定 `sig_` ID、1 个稳定 `clu_` ID、一项通过的共识决策；预算记录 3 条评论、2 次调用、200 个输入 Token 和 40 个输出 Token。
- 这项演示和上述合成门槛仅证明离线链路及不变量；真实模型适配器、真实语义指标、macOS 验证和平台接入均未完成，也不构成 H0、H1 或生产就绪声明。

## Task 10 发布门禁

| 检查 | 本地结果 |
|---|---|
| `uv sync --project packages/agent --locked` | 22 个包解析与检查成功 |
| 完整 Agent 测试 | 231 passed |
| Ruff 源码、测试与示例检查 | 通过 |
| Task 10 触及的 Python 文件格式检查 | 4 个文件通过 |
| mypy strict | 29 个源码文件无问题 |
| `uv build --project packages/agent` | 生成 0.3.0 sdist 与 wheel |
| `uv run --offline --locked --project packages/agent python packages/agent/examples/m2_fake_demo.py` | 输出 `completed`，无需联网 |

wheel 已核对包含模型替身、M2 编排与结果类型，以及 `signal-v1.txt`、`cluster-v1.txt` 两份提示词资源。以上检查在 Windows、PowerShell 7 中执行；macOS 和真实模型仍未验证。

合并前独立复核补强了预算与取消门禁：超额 usage 会按实记账并停止成功状态；结构修复会重新估算输入并检查单次能力；预算耗尽会保留已发生调用的审计；公共入口可以观察运行中取消。新增回归测试均经历失败再修复，完整 Agent 回归为 231 项通过。上述结果仍仅覆盖离线场景网关。

## 2026-10-03 真实评测身份准入的离线门禁

本节保留 DeepSeek 适配器交付前的 285 项测试历史快照，不能作为后续代码的最新回归数量。

本轮新增 `preflight_real_evaluation`、不可变且 revision 非空的 `FrozenModelIdentity`、`VersionedModelGateway` 端口和独立 `analyze_m2_real_evaluation` 入口。准入在零模型调用下核对配置身份；实际响应在信号、聚类、结构修复和合并边界核对 model/revision，并保留 requested/actual 四项审计。普通 `analyze_m2` 保持 `revision=None` 行为。

复核修复了缓存模式隔离：阶段键同时绑定模型名称、冻结 revision 和 `identity_verification_required`，普通缓存不能隐藏正式入口的响应身份漂移，正式缓存也不会供普通模式复用。身份失败保留已发生用量及审计，失败阶段不重试、不写缓存。

Schema RED 实测为 1 failed、3 passed，失败位置是 result Schema 与权威 Pydantic 模型不相等；重新导出后与完整回归一同通过。新增演示子进程测试先以缺少 `audits` 的 `KeyError` 失败（1 failed、1 passed），然后实现 `--real-evaluation` 合成选项；Schema 与演示的定向 GREEN 为 6 passed。另以独立合成结果 fixture 锁定新审计 JSON 往返，旧审计缺失四字段时均默认为 null。结果 Schema 继续为 `1.0`：新消费者能读旧审计，但拒绝未知字段的旧消费者必须更新后才能读新审计，不能声称双向兼容。

最终总审还修复了两个组合边界：preflight 对可变 gateway 身份立即建立独立快照，防止能力读取期间更换未核对版本；当身份漂移同时触发用量超预算时，运行仍优先报告预算结束，但审计正确记录身份错误与实际用量。两项原始合成复现均经独立复审确认修复，无剩余阻断问题。

在隔离工作树、Windows 下实际运行：

| 检查 | 本轮实际结果 |
|---|---|
| `uv run --offline --locked --project packages/agent pytest packages/agent/tests -q` | 285 passed |
| Ruff check（全部 src/tests/examples） | 通过 |
| Ruff format --check（全部 src/tests/examples） | 56 files already formatted |
| mypy strict | 30 个源码文件无问题 |
| `uv build --offline --project packages/agent` | 生成 0.3.0 sdist 与 wheel |
| `git diff --check` 与 `git diff --check upstream/main...HEAD` | 通过 |
| `m2_fake_demo.py --real-evaluation` | completed；3 信号、1 簇、1 项通过共识、2 审计；requested/actual 均为 scenario-model / m2-fixture-1 |

这些检查只证明合成链路与身份契约。在这一历史快照中，验收条件 7 是端口及响应身份契约就绪，具体 provider 适配器尚未交付，也未调用真实模型。后续已交付标准库 DeepSeek 适配器，见下节；正式评测仍必须获得 provider 可核验的 revision，不能用请求值回填响应值。同步取消只能在调用边界观察；预算没有货币上限或准确 Token 保证，preflight 也不检查累计输入预算与单次能力兼容性。

现有 24/24 结构准入计数即便通过，也不等于人工语义金标或真实评测完成；本轮未改标签快照，也未扩大用户已接受的评测参考标注标准。标签 `export-labels` 继续由 upstream issue #8 单独跟踪。本轮未读取真实 provider 凭据、未产生真实模型费用，也不宣称 H0、H1 或生产就绪；最终独立代码复审及修复复验已通过。

## 2026-10-03 DeepSeek 适配器后续交付

后续交付 `DeepSeekModelGateway`：显式 Key、每次输入上限和可注入传输；固定官方端点与 `deepseek-flash` 别名；标准库同步文本请求、非流式、关闭 thinking、JSON object 模式、图像能力为 false，不引入 provider SDK。JSON object 模式不表示 provider 强制执行项目 Schema；信号与聚类解析器最小扩展为接受原始 JSON，仍保留结构及可信引用校验。

网关与实际响应 revision 均为 `None`，响应模型名和内容来自实际返回值。独立可选 `provider_system_fingerprint` 默认 None，并从响应复制到审计；它是后端标识，不是不可变权重 revision，也不同于本地原始响应 SHA-256。正式 preflight 对请求 revision 为空报告 `requested_revision_required`，对任填非空请求 revision 报告 `gateway_revision_required`，均在 HTTP 前失败；普通 `analyze_m2` 不能替代正式评测准入。

新增测试先因缺少适配器失败，再验证固定请求形状、实际响应映射、错误码、超时传递、无内部重试与禁止重定向。原始 JSON 阶段解析测试先失败后通过；深层 JSON 复现补强了 `RecursionError` 的脱敏错误映射，解析失败仍保留已发生调用的审计与用量结算。审计扩展同步更新导出 Schema 并验证旧记录默认值。类型门禁还补强了 `finish_reason` 的显式 Literal 类型收窄，运行行为不变。

代码任务、文档及修复均已通过规格/质量独立复核，最终总审未发现阻断问题，并额外验证了 30 个合成组合。测试过程并非每组都独立观察 RED：初始 54 个用例先编写，但使用 `-x` 只观察首个缺适配器失败，部分补充用例在实现后通过；不能称全量严格 TDD。后续本地检查在同一隔离工作树、Windows 下执行：

| 检查 | 后续实际结果 |
|---|---|
| `uv run --offline --locked --project packages/agent pytest packages/agent/tests -q` | 354 passed |
| Ruff check（全部 src/tests/examples） | 通过 |
| Ruff format --check（全部 src/tests/examples） | 58 files already formatted |
| mypy（根目录命令及显式 strict 配置） | 31 个源码文件无问题 |
| `uv build --offline --project packages/agent` | 生成 0.3.0 sdist 与 wheel |

上述测试使用离线传输替身，没有真实 provider 兼容性测试。本轮没有读取真实凭据、调用真实模型或产生模型费用，也未验证真实语义指标，不宣称 H0、H1 或生产就绪。24/24 仍只是结构准入计数；`export-labels` 的 upstream issue #8 必须单独修复后再刷新标签快照，本轮未改标签。
