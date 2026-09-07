# M1 确定性共识验收记录

- 日期：2026-09-07。
- 分支：`codex/agent-m1`，基线 `ed94fc6`。
- 代码验收提交：`02c6d26`，包含规则、边界测试和公共 API。
- 状态：本地实现与验收完成；因 GitHub Git 通道连接失败，尚未同步上游 main、推送或创建 stacked PR。

## 完成范围

- 公开 `evaluate_consensus(AnalysisRequest, NeedCluster) -> ConsensusDecision`。
- 规则版本 `m1.0`，包版本 `0.2.0`。
- 拒绝未知评论引用；视频作者未知时失败关闭。
- 排除缺失作者和视频作者本人，同一作者只计一次。
- 按首次采集时间与评论 ID 选择确定性代表证据，保持可信请求原文。
- 用测试替身验证有效期内跨日“2 + 1”、完全重放、到期排除和冲突载荷拒绝。

## TDD 证据

第一轮先添加共识测试，运行：

```powershell
uv run --project packages/agent pytest packages/agent/tests/rules/test_consensus.py -q
```

测试收集因 `ModuleNotFoundError: No module named 'requirementseeker_agent.rules'` 失败。实现最小规则模块后，同一命令为 8 项通过；加入跨日场景后为 12 项通过。

第二轮先添加公共 API 测试。首次运行得到 `0.1.0 != 0.2.0` 的预期失败；导出规则并更新包版本后，该测试通过。

## 验收场景

| 场景 | 结果 |
|---|---|
| 同视频三位明确、不同且非视频作者 | 通过并返回三条可信证据 |
| 同作者多条评论 | 每位作者只计一次 |
| 评论作者缺失或属于视频作者 | 不计入门槛 |
| 视频作者未知 | `video_author_unknown`，失败关闭 |
| 簇引用不存在的评论 | `cluster_references_unknown_comment` |
| 簇成员顺序变化 | 代表证据和结果不变 |
| 有效期内前两人、次日新增一人 | 通过 |
| 同一快照完全重放 | 不重复累计 |
| 评论到期 | 不进入新活动快照 |
| 同一稳定 ID 的载荷改变 | 测试替身拒绝冲突 |

## 责任和限制

- `NeedCluster` 由显式测试数据提供；M1 没有实现需求信号识别或语义聚类。
- 跨日 `SnapshotStore` 只存在于测试中，用于验证宿主接入语义；生产存储、事务和清理仍属于宿主。
- M1 没有模型调用、多模态、预算结算、缓存、历史机会去重或完整 `AnalysisResult` 编排。
- 没有真实采集 fixture、真实模型评测、macOS 或云端 CI 结果。
- 本里程碑不是 H0 或 H1。

## 最终门禁

| 检查 | 结果 |
|---|---|
| pytest | 70 项通过 |
| Ruff lint / format | 通过；19 个 Python 文件已格式化 |
| mypy strict | 12 个源文件无问题 |
| uv lock | 22 个包解析一致 |
| 构建 | `requirementseeker_agent-0.2.0.tar.gz` 与 `requirementseeker_agent-0.2.0-py3-none-any.whl` 成功 |
| Git 差异 | `git diff --check` 通过；无个人日志、交接或 AGENTS 修改 |

发布必须等待上游 main 与 M0 分支成功同步。
