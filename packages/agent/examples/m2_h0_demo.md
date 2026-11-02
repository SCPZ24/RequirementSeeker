# 从合成请求运行 M2 → M3/M4

在本工作树根目录使用已装项目依赖的 Python 3.12 运行：

```powershell
$env:PYTHONPATH = (Resolve-Path packages/agent/src).Path
python -B -X utf8 packages/agent/examples/m2_h0_demo.py
python -B -m pytest -q packages/agent/tests/pipeline/test_m2_h0_demo.py
```

每行 JSON 对应一个独立请求。演示调用 plan_sampling 和普通生产入口 analyze_m2。M2 的 signals、cluster 调用均由 ScenarioModelGateway 在本地回答。M3 的文本判断和机会判断也使用本地替身，候选证据取自该次共识。每个请求使用空语义缓存，七次请求共用一个串行内存意图存储。输出的 gateway_stages 与 judge_stages 是替身的实际调用记录。

| 场景 | M2 | M3/M4 | 意图数 |
| --- | --- | --- | --- |
| 无信号 | no_signal | no_opportunity | 0 |
| 独立作者不足 | completed | no_opportunity | 0 |
| 文本上下文不足 | completed | context_insufficient | 0 |
| 假网关模拟失败 | fatal_error | incomplete | 0 |
| 取消 | cancelled | incomplete | 0 |
| 首次提交 | completed | committed | 1 |
| 换 run_id 重放相同证据 | completed | already_committed | 1 |

核对最后两行 intent_key 相同。输出验证的是本地单视频、单簇文本和进程内意图回执。
