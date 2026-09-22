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
