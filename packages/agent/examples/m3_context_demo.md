# M3 合成帧补充与降级

文本判断不能引用未读取的媒体；请求补充时指定的引用必须是当前输入中的帧。不可信引用返回 `incomplete / invalid_context_refs`，不会进入正式 v1。补充后若共识仍成立但业务条件未通过，本地回执保留实际失败条件，正式结果为 `rejected / opportunity_filtered`，意图存储不变。

在专用工作树根目录使用已安装项目依赖的 Python 3.12，生成记录后启动独立的 A29 读取进程：

```powershell
$env:PYTHONPATH = (Resolve-Path packages/agent/src).Path
$out = Join-Path $env:TEMP ('rs-a30-' + [guid]::NewGuid())
python -B -X utf8 packages/agent/examples/m3_context_demo.py $out
python -B -X utf8 packages/agent/examples/read_m4_results.py $out
python -B -m pytest -q packages/agent/tests/pipeline/test_context_fallback.py
```

七行都从合成请求调用 analyze_m2，再把实际返回对象交给 run_h0。文本判断只接收去除媒体的请求副本。仅当判断为 media_required 且当前配置具有图像能力、存在可用帧引用时，才调用本地替身。帧引用是不透明标识；本地映射给出哪些原簇评论仍支持该软件对象。管线从原请求重新计算独立作者数及引文。已补充且共识仍成立时，机会判断只收到选定帧和经复核的成员。

| 场景 | 帧调用 | 结果 |
| --- | ---: | --- |
| no-vision | 0 | 能力不足，context_insufficient |
| missing | 1 | 替身无该材料，context_insufficient |
| revoked | 1 | 三位作者中只剩两位支持，rejected |
| failure / bad-ref | 各 1 | 替身异常 / 不可信引用，incomplete |
| supported | 1 | 复核通过，首次写入一条 new_candidate |
| text-replay | 0 | 文字充分，原键重放，意图仍为一条 |

能力或材料不足使用现有 v1 context_insufficient；共识撤销导出最终未通过的共识及 rejected，sources.json 中保留原 M2 共识。执行失败或不可信引用无可重试性分类，交接为有明确原因的本地 incomplete 且 result=null。演示范围为单视频、单簇、合成帧替身和串行内存提交。
