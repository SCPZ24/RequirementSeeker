# export-labels create-only 设计（issue #8）

## 目标与范围

用户已确认下一步独立修复 Dataset Tools 的 [issue #8](https://github.com/SCPZ24/RequirementSeeker/issues/8)。`export-labels` 只能在全新目标上发布空白标注，不自动恢复备份，不覆盖人工标注；失败生成物保留供检查。

采用独立分支 `codex/export-labels-create-only`，从已 fetch 核对的 `upstream/main@98bdf81` 建立。与 DeepSeek 分支分开，不修改根 main。本轮不运行真实标签刷新，不读取或修改 `.local-data`、真实标注、原始评论或凭据，不调用模型，不推送、建 PR、合并或关闭 issue。

## 已确认根因

当前 `labels.py` 在目标缺失且 backup 存在时执行恢复；导出失败时递归删除 UUID staging；旧 staging 不阻止新的 UUID 事务。两个现有测试明确要求恢复 backup 和忽略旧 staging，合成复跑 2 passed，说明旧契约须明确更改，不能只补文档称其安全。

当前发布是 `staging.replace(output)`，检查目标后到发布间还有竞态。采用 Windows 不替换目标的 rename，而非依赖预检查。

## 已选择方案与替代方案

`export_labels(sanitized_root, output_root)` 签名和 CLI 参数不变，默认且仅 create-only，不增加允许旧恢复语义的开关。显式 `--create-only` 并保留旧模式的替代方案会留下误用路径，且本命令本来就拒绝覆盖已存在标注，因此不采用。

固定同级 `.<output-name>.staging` 同时作为事务互斥目录和失败检查目录。继续 UUID staging 并增加另一把锁的替代方案引入两个状态及清理问题，不采用。原有 `.<output-name>.staging.<id>` 遗留状态仍须识别并阻断，不能自动清理或迁移。

## API / CLI 契约

- 只支持 Windows create-only 发布；其他平台在创建任何输出目录或 staging 前报 `label_create_only_unsupported_platform`。
- 目标存在（文件、空目录或非空目录）报 `label_output_already_exists`，不改变其内容。
- 同级 backup、固定 staging 或旧 staging 前缀条目存在时报 `label_output_transaction_already_exists`，保留全部条目，不恢复、不删除。
- 检测到输入、输出、staging、backup 或其祖先路径的 symlink/junction/reparse 跳转时关闭式拒绝；输出路径错误报 `label_output_path_invalid`，输入错误沿用安全来源错误码。
- Windows 会将部分尾点/尾空格名字归一为另一目标。输出路径组件有这类歧义时在写入前以 `label_output_path_invalid` 拒绝，保证目标和事务名称使用同一身份；普通祖先 `..` 相对路径语法不因此误拒绝。这是最终总审发现的事务别名漏口修正，不添加隐式规范化或模式开关。
- 保留现有输入与输出必须分离、模板不能语义预填、采样清单/评论顺序匹配等验证，不扩展标注字段或更改评测参考标准。
- CLI 失败仍只输出固定 ASCII 错误码，exit 2；不输出正文、评论/视频标识、绝对路径或 staging 内容。成功摘要保持原格式。
- 写入或发布的 OSError 转为固定错误码，不能把底层带路径异常回显。发布失败使用 `label_output_commit_failed`；非发布写入失败使用 `label_output_write_failed`。

## 事务流程

1. 检查平台、路径分离、所有可见跳转及目标/backup/历史事务冲突；不存在父目录时只检查已有祖先。
2. 读取并验证全部合成/未来宿主提供的脱敏输入，先构造完整 AnnotationFile 列表及相对输出路径。输入校验失败不创建 staging。
3. 再核对路径与冲突，使用不允许 `exist_ok=True` 的 mkdir 独占固定 staging。同一时刻只能有一个合法调用占有该目录；取得目录后再检查目标是否已经由另一调用发布，避免预检查过期后继续写入。失败者不进入别人的目录写入，不删除获胜者的文件。
4. 在自己取得的 staging 中写入模板；写入前重新检查可见路径跳转。写入失败保留 staging（包括已写部分），不创建目标、不触碰 backup，不自动清理。
5. 发布前再次核对目标/backup/历史冲突、路径及本事务 staging。使用 Windows `staging.rename(output)`，绝不调用 replace。目标即使在最后一次检查后出现，rename 也失败并保留该目标和 staging。
6. 成功后返回位于已发布目标下的 output_files。固定 staging 名称自然随 rename 消失，不另做删除。

所有冲突检查须区分本事务已取得的 staging 和其他事务，不能在发布前因自己的 staging 存在而错误拒绝。

## 失败 staging 与检查策略

失败目录位置可由调用方根据自己提供的目标推出：同级 `.<output-name>.staging`。不自动回显真实路径或写内容清单。该目录可能含脱敏正文，不能上传 issue、提交 Git，或据此宣称不含敏感内容。

失败后再次运行会拒绝；操作者需先独立检查并决定是否保留/转移失败目录。本轮不实现自动 resume/recovery/cleanup 命令，不授权删除任何真实 staging 或备份。

固定 staging 的互斥保证适用于遵循本契约的并发调用；Windows rename 保证最终目标不被替换。路径检查可拒绝观测到的跳转，但不声称抵御同权限恶意进程在任意系统调用间替换祖先目录；本轮不引入 Windows 目录句柄/沙箱系统。需测试关键边界的可控路径替换，不能将有限回归称为任意敌对文件系统安全保证。

平台依据：[Python 3.12 os.rename](https://docs.python.org/3.12/library/os.html#os.rename) 明确 Windows 的已有目标总是失败，而 Unix 可替换空目录。因此不能仅靠跨平台 Path.rename 声称 no-replace。

## 测试与门禁

全部使用 pytest 临时目录和合成 fixture，禁止真实 `.local-data`。逐组先观察 RED、再写最小实现并观察 GREEN，不用 `-x` 把首个失败当作全组证据。

- 新目标发布成功、空白字段/来源顺序不变。
- 已有人工标注字节不变；backup 无目标时仍原地不动且目标缺失。
- 固定/旧 UUID staging、文件形式事务、断链/重定向冲突都拒绝且不更改。
- 输入无效时不产生 staging；写入/发布失败时保留自己的 staging，并阻止误重跑。
- 并发调用不会同时写入同一 staging，至多一份发布成功；失败调用不写入/删除别人的事务。
- 目标在检查后出现（文件/空目录/人工标注目录）不被替换，失败 staging 留存。
- 可控输入/输出祖先与 staging 路径跳转被拒绝；非 Windows 在零写入下失败。
- CLI 成功/失败、旧契约两个测试替换为明确的新契约测试，错误不含路径/正文/合成秘密。
- 完整 Dataset Tools tests、Ruff、format、显式 strict mypy、离线构建及 diff check 通过；适当运行已有跨包集成回归。由独立代理复审竞态和失败所有权边界。

## 文档和实施组织

修改 Dataset Tools README 的旧恢复/UUID staging 描述，更新 `2026-09-25-m2-local-data-refresh.md` 的 labels 预检查和失败策略：包含固定及旧 UUID staging，不自动清理，不重复运行刷新。保留历史执行结果，不把新代码验证说成再次刷新成功。

三个工作单元：基本事务契约、竞态/路径安全回归、CLI/文档与完整门禁。先串行实现避免共享文件冲突，必要时使用新子代理独立规格/质量复审。保存每组真实 RED/GREEN 和已知限制；当前设计确认后再编写实施计划和代码。
