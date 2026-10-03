# RequirementSeeker Dataset Tools

该包把已经通过 Collector Schema 校验且由用户批准的原始评论转换为脱敏数据，导出空白人工标注材料，并验证独立标注与争议裁决。它不依赖 Collector 或 Agent 的 Python 包。

## 安全边界

- 原始目录只读。`sanitize` 仅处理批准清单中的视频；清单外目录只计数，不记录原始键。
- HMAC 秘密只能通过 `--secret-env` 指定的环境变量读取。请在当前进程中用本机安全方式设置一个至少 32 字节且可稳定复用的值；不要把值写入命令参数、终端历史、文档、日志或 Git。
- `.local-data/` 下的 raw、sanitized、labels 和浏览器 profile 均为本地材料，不得提交上游。
- 自动化只生成显式 `unlabeled` 模板，不生成金标或模型语义预填。人工复核与必要裁决完成前，样本不能进入评测分母。
- 精确地址候选在脱敏正文中替换为 `[ADDRESS]`，同时留下不含原文的人工复核项。
- `export-labels` 默认且仅支持 create-only，不增加模式开关，只在 Windows 上发布全新标注根；其他平台在创建输出或 staging 前拒绝。已有目标、同级 `.<output-name>.backup`、固定 `.<output-name>.staging` 或旧 `.<output-name>.staging.<id>` 条目都会阻断导出，文件和目录均不能覆盖；观测到路径或祖先的 symlink/junction/reparse 跳转也会拒绝，包括断链。
- 标注导出独占固定 `.<output-name>.staging`，写入或发布失败会保留该目录及已经写入的内容。它不恢复 backup、不清理任何事务条目；再次运行会失败。操作者须先独立检查并决定如何保留或转移失败材料，不能凭错误码删除人工标注或 backup。staging 可能包含脱敏正文，不得上传或提交 Git。
- 固定 staging 的互斥适用于遵守契约的并发调用；Windows rename 拒绝替换最终目标。路径检查拒绝观测到的跳转，但不保证抵御同权限恶意进程在任意系统调用之间替换祖先目录。
- 输出路径组件不能以点或空格结尾（正常祖先 `..` 相对语法除外）；Windows 可能归一这些名字，造成目标和事务名不一致，因此在写入前以 `label_output_path_invalid` 拒绝，不隐式改名。

## 命令

```text
rs-dataset sanitize --raw <raw-root> --plan <manifest.json> --output <sanitized-root> --secret-env <name> [--create-only]
rs-dataset export-labels --sanitized <sanitized-root> --output <label-root>
rs-dataset validate-labels <label-root> --sanitized <sanitized-root>
```

所有命令成功时只向标准输出写一行 ASCII JSON 摘要。参数解析成功后的标注导出事务失败 exit 2，摘要只包含 `error` 固定安全码和 `status:"error"`，stderr 为空，不回显实际路径、评论正文、评论/视频 ID 或秘密值。发布失败为 `label_output_commit_failed`，写入失败为 `label_output_write_failed`；失败目录位置由调用方提供的输出路径推出。成功摘要保持原格式，标注导出包含 `annotation_count`、`comment_count`、`output` 和 `status:"ok"`。

`sanitize --create-only` 仅用于新目标目录：发布时在 Windows 上使用不会覆盖已存在目标的目录重命名；即使目标在预检后出现，也会失败并保留该目标。其他平台暂不支持此模式，会在处理前拒绝。省略该选项时保留原有的替换与备份恢复行为。

`sanitize` 的事务语义没有改变：仍使用独立的 `.<output-name>.staging.<id>`，旧 UUID staging 不阻止新一次运行，也不自动清理旧条目。默认替换模式会在失败时清理本次 staging，并在目标缺失时恢复同级 backup；`--create-only` 拒绝已有目标或 backup，失败时保留本次 staging。它与 `export-labels` 的固定 staging 和拒绝历史事务的规则不同。

## 产物与人工流程

`sanitize` 为每个可用视频写入脱敏后的 `video.json`、`comments.jsonl`、`collection.json`、`sanitization.json` 和 `sampling-manifest.json`。零评论或零成功页的视频只写安全排除报告，并进入 `replacement-candidates.json`。`dataset-split.json` 按伪视频 ID 稳定划分：批准的 24 个视频固定为开发 10、校准 7、留出 7；评论和回复不会跨集合。

`export-labels` 在 `<label-root>/<platform>/<video-id>/annotation.json` 写入主标注模板。每条记录包含脱敏正文和五类显式未标注字段，`clusters` 初始为空。人工填写后，将 `annotator_id` 和 `is_complete` 设置为真实状态。

无争议样本只使用 `annotation.json`。争议样本在同目录增加第二位标注者的 `annotation-secondary.json` 和裁决者的 `adjudication.json`。两位标注者必须不同，三份文件必须引用完全相同的脱敏评论集合；最终裁决不得继续标记为争议。

`validate-labels` 会按对应的脱敏目录核对视频集合、评论 ID 顺序和脱敏正文，再报告标注文件数量和可进入评测的数量。空白模板可以通过结构校验，但可评测数量为零。未知枚举、重复评论、跨视频簇成员、重复簇成员、疑似原始 ID、虚假的完成状态或不完整争议裁决都会被拒绝。单份标注的 Python 结构校验不能单独授予可评测资格。
