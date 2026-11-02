# M4 合成历史分流

在 A28 工作树根目录、已有项目依赖的 Python 3.12 下执行：

```powershell
$env:PYTHONPATH = (Resolve-Path packages/agent/src).Path
python -B -X utf8 packages/agent/examples/m4_history_demo.py
python -B -m pytest -q packages/agent/tests/pipeline/test_m4_history.py
```

每行先运行实际 M2，再由原 M3 根据共识生成候选。注入的合成判断器分别表示“同一批量导出需求”和“同样提到导出、但审计员场景不同”。same 生成 merge_evidence，different 生成 new_candidate；对应 replay 的 route_calls 仍为 1，意图数仍为 1。可将追加意图中的 target_opportunity_id、candidate_id 及 evidence 与 historical_record、当前候选及 current_consensus 核对。判断器使用场景给定的答案，不评测真实语义匹配质量。

坏目标、坏候选、坏证据、判断失败和历史不完整均返回 incomplete；incomplete-with-prior-receipt 保留此前的一条意图，没有再写。历史机会的人工阶段、备注、原来源都属于只读快照。演示只生成内存意图，范围限单视频、单簇文本和串行执行。
