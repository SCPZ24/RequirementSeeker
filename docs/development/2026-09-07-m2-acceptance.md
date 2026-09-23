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
