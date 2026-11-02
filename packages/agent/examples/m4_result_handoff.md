# 从运行结果交接 M4 意图

从 A29 工作树根目录，使用已安装项目依赖的 Python 3.12 和一个新的输出目录。生成命令只写入指定目录中的两个 JSON 文件；读取命令可以在另一个支持同版本模块的 Python 进程运行。

```powershell
$env:PYTHONPATH = (Resolve-Path packages/agent/src).Path
$out = Join-Path $env:TEMP ('rs-a29-' + [guid]::NewGuid())
python -B -X utf8 packages/agent/examples/generate_m4_results.py $out
python -B -X utf8 packages/agent/examples/read_m4_results.py $out
python -B -m pytest -q packages/agent/tests/pipeline/test_m4_result_handoff.py
```

宿主保存可信的 `sources.json`（真实输入、实际 M2、假判断器返回的上下文与候选、允许引用的历史目标、真实回执和内存意图变化），与 `results.json` 一起提供给读取方。后者是本地交接数组，其每一项的 `status/reason` 描述本次内存执行；非空 `result` 是单条正式 `AnalysisResult` v1。读取方校验两份材料的运行、候选、目标、完整证据、回执和状态对应关系，输出逐行摘要和 outcome 负载。来源文件是信任输入，本演示没有为两份文件提供外部签名。读取程序只读文件，不执行网关或写意图。

| 实际出口 | 本地回执 | 正式 result |
| --- | --- | --- |
| 追加或新建 | committed | merge_evidence 或 new_candidate |
| 追加或新建的重放 | already_committed | 原意图及原键，当前 run_id |
| 当前历史不完整 | incomplete，history_incomplete | null；可重试性未判定 |
| 合法无信号 | no_opportunity | rejected，no_signal |
| 取消、预算耗尽 | incomplete | cancelled 或 budget_exhausted |
| 网关失败 | incomplete | fatal_error 或 retryable_error，保留真实错误码 |

十行样例可由以上命令完整生成。重放使用不同 run_id，调整同一条评论的采集和到期时间，改变证据排列；按完整内容比较，保留原意图。本次已知 M2 失败的 step 指完成步骤之后停下的阶段边界，预算资源来自 M2。本地生成器为每次导出分配 analysis_id；本例没有生成推断节点，inference_steps 为空。未完成且无 v1 分类依据时须读取本地原因，不能将 null 解释为零机会。历史人工备注与阶段不进入导出字段。验证对象仅为单视频、单簇文本的合成运行及串行内存意图回执；外部业务系统的持久化需要单独确认。
