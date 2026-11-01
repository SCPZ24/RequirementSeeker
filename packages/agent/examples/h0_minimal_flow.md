# 单簇 H0 离线演示

从该工作树的仓库根运行，使用 Python 3.12 和 Agent 锁定的依赖。以下 PowerShell 命令显式加载当前工作树的源码。解释器可以来自已安装相同依赖的环境。

```powershell
$env:PYTHONPATH = (Resolve-Path packages/agent/src).Path
python -B -X utf8 packages/agent/examples/h0_minimal_flow.py
```

脚本读取本目录的合成 M2 结果和仓库既有的合成请求、候选 fixture。每次启动都创建空的内存历史。模型接口提供上下文判断与机会评估，管线执行共识复核、机会过滤和证据校验，最后记录提交意图。

| case | 预期 status | 内存意图总数 |
| --- | --- | ---: |
| insufficient_context | context_insufficient | 0 |
| no_consensus | no_opportunity | 0 |
| first | committed | 1 |
| replay | already_committed | 1 |
| upstream_cancelled | incomplete | 1 |

first 与 replay 的 intent_key 应相同。该键按平台、视频和排序后的代表证据评论 ID 计算。运行 ID 和候选措辞不参与计算。相同键但证据内容冲突时返回 incomplete。回执只反映本进程内串行 sink 的状态。

范围限于文本充分的一个簇。上下文不足会在机会判断前停止，模型判断非软件交付、依赖重运营或缺少具体获客路径会得到业务拒绝。多簇编排、媒体补充、不同证据之间的语义去重、持久化事务和真实模型质量仍是后续交付项。
