# Export Labels Create-only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** issue #8：只创建新标注根，不恢复 backup、不删除失败 staging，不覆盖任何既有目标。

**Architecture:** Windows 非替换 rename 发布，固定同级 staging 独占；先校验全部输入，再取得 staging 并写入。保持 API/CLI 参数，补足路径检查和安全错误码。

**Tech Stack:** Python 3.12、pathlib、Pydantic、pytest、Ruff、mypy、uv。

工作目录：`C:/Users/Fantason/.codex/worktrees/export-labels-create-only/RequirementSeeker`，分支 `codex/export-labels-create-only`，基线 98bdf81，设计提交 932792a，Dataset Tools 基线126 passed。用户已确认书面设计并授权执行。全部 synthetic 临时目录；不读取 `.local-data`、凭据或真实标签，不运行刷新，不调用模型，不发布或关闭 issue。三个任务串行，规格审通过后质量审；发现问题先复现 RED，再修复。

## Task 1：事务契约

文件：`packages/dataset-tools/src/requirementseeker_dataset/labels.py`；`packages/dataset-tools/tests/test_labels.py`。

- [x] 在旧两个测试原位改为新行为断言，并增加事务失败用例。不使用 `-x`，完整运行新行为组，记录具体失败原因。

```python
with pytest.raises(LabelValidationError, match="label_output_transaction_already_exists"):
    export_labels(sanitized, output)
assert not output.exists()
assert (backup / relative_annotation).read_bytes() == original
```

固定 `.labels.staging` 和 `.labels.staging.<id>` 均应拒绝，旧内容原样；任意人工标注保持字节不变。写入失败保留固定 stage，目标缺失；输入校验错误没有 stage；不支持的平台在零写入下失败。平台模拟仅 patch 本模块能力标志，不更改进程全局 os.name，避免影响 pathlib。

- [x] Run `uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests/test_labels.py -q`，观察旧恢复/忽略 staging 与新用例实际 RED；原有验证/模板用例可保持 GREEN。
- [x] 实现以下固定事务检查（名称可按现有风格，但语义不可扩展），删除因本任务不再使用的 shutil/uuid 导入：

```python
def _check_output_transaction(output: Path, *, owns_staging: bool = False) -> None:
    staging = output.with_name(f".{output.name}.staging")
    backup = output.with_name(f".{output.name}.backup")
    for path in (output, staging, backup):
        for part in (path, *path.parents):
            if _is_path_redirect(part):
                raise LabelValidationError("label_output_path_invalid")
    if output.exists():
        raise LabelValidationError("label_output_already_exists")
    if backup.exists() or (not owns_staging and staging.exists()):
        raise LabelValidationError("label_output_transaction_already_exists")
    if output.parent.exists():
        prefix = f".{output.name}.staging.".casefold()
        if any(path.name.casefold().startswith(prefix) for path in output.parent.iterdir()):
            raise LabelValidationError("label_output_transaction_already_exists")
```

用调用层 OSError→固定错误码包装，不能回显路径。确保先拒绝空名字/根路径；按现有 Path 检查处理 resolve 的失败。

- [x] `export_labels` 保留现有 AnnotationFile 构造、相对路径及全部输入规则，但把遍历/读取/验证/构造移到 mkdir 前；在原始未 resolve 的输入路径上检查祖先跳转，在每个输入文件读取前再次检查。输出先检查原始路径再 resolve，保持输入/输出分离。
- [x] 固定 staging 取得与写入流程：

```python
_check_output_transaction(output)
try:
    staging.mkdir(parents=True)  # 不使用 exist_ok
except FileExistsError:
    raise LabelValidationError("label_output_transaction_already_exists") from None
except OSError:
    raise LabelValidationError("label_output_write_failed") from None
try:
    _check_output_transaction(output, owns_staging=True)
    for relative, annotation in prepared:
        _require_local_path(staging / relative, staging)
        _write_annotation(staging / relative, annotation)
    _check_output_transaction(output, owns_staging=True)
    _commit_label_root(staging, output)
except OSError:
    raise LabelValidationError("label_output_write_failed") from None
```

不得添加清理 finally/except，不删除自己或别人 stage，不创建/移动 backup。`prepared` 在 mkdir 前由原有验证逻辑构造，包含相对路径和 AnnotationFile；返回 annotations/output_files 语义不变。输入跳转用现有 `_require_local_path`/`_require_local_tree`，输出跳转用输出安全码。

- [x] 修改发布，仅使用 Windows rename，平台能力在入口和发布均关闭式检查；目标出现仍报安全错误、保留两边内容：

```python
if not _WINDOWS_CREATE_ONLY:
    raise LabelValidationError("label_create_only_unsupported_platform")
try:
    staging.rename(output)
except OSError:
    raise LabelValidationError("label_output_commit_failed") from None
```

`_WINDOWS_CREATE_ONLY = os.name == "nt"`，不新增 API 模式开关。发布前路径与冲突检查不替代 rename 的 no-replace 保障。

- [x] 原定向组及完整 Dataset Tools GREEN 后跑 Ruff/format/显式 strict mypy，提交 `fix(dataset): make label export create-only`。规格/质量双审通过；记录所有实际 RED/GREEN，包括原已存在行为的测试未失败，不夸大 TDD。

Task 1 证据：`e735126` 基础契约；初组 RED14/34，补守卫 RED19/34→GREEN53，安全码 RED7/53→GREEN60。规格审输入根权限异常 `c913f8c` RED1/60→GREEN61；质量审 NUL Path `32a9034` RED2/61→GREEN63。两项修复独立复审通过，完整155 passed、Ruff、17文件格式、9源码 strict mypy、diff check通过。已有目标文件/空目录行为原已正确，仅直接GREEN回归。

## Task 2：竞态和路径回归

文件：新 `packages/dataset-tools/tests/test_label_transactions.py`（复用现有合成 fixture，不能导入真实材料）；必要修正仅 `labels.py`。新源模块不增加。

- [x] Windows 真实 rename 新目标成功，以及最后检查后目标出现：注入 `Path.rename` 包装，先在目标写入合成哨兵，再调用原 rename；分别测试目标文件、空目录、人工标注目录。检查目标摘要不变，固定 stage 保留，API 只抛安全错误。
- [x] 使用 Event 有界等待进行并发测试：第一调用取得 stage 后暂停，第二调用拒绝且不能写入/删除 stage，第一调用释放继续成功；另一用例模拟预检查过期，mkdir 前另一次调用已成功发布，随后重新检查拒绝写入。
- [x] 路径检查测试覆盖：输入根及祖先、输出祖先、backup/staging/legacy symlink 或 junction/reparse；读取或发布关键边界模拟出现跳转；能创建 junction 时做 Windows 实际测试，权限限制明确 skip，不把 skip 当通过。所有外部哨兵位于 pytest 临时目录。
- [x] 新测试先运行完整分组：已实现的不覆盖目标/并发行为可直接 GREEN 作为回归；若任何缺陷，记录 RED 并只增加最小检查修复。禁止为了取得 RED 撤回已通过的行为或测试被测对象替身。
- [x] 如出现输出路径失败误用来源码，按设计修正输出固定码；所有 OSError、权限、失败 mkdir/写入均需安全转换，其他事务不改动。
- [x] Run `uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests/test_label_transactions.py packages/dataset-tools/tests/test_labels.py -q`，然后完整 Dataset Tools、Ruff/format/mypy；提交 `test(dataset): cover label transaction races`（若有修复，提交名称如实）。独立规格/质量双审。

Task 2 证据：`5e9eaea` 仅新增18项测试，全部直接GREEN（真实rename4、并发2、实际junction9且0skip、权限失败3），未改生产代码。首轮17/1失败来自递归mkdir的测试注入时机，修正后通过，不计产品RED。定向81/完整173 passed、Ruff/18文件格式/9源码 strict mypy/diff check通过，规格/质量双审独立81 passed且无问题；规格审额外三个独立进程并发复跑6/6通过。

## Task 3：CLI、文档及收尾

文件：`packages/dataset-tools/tests/test_cli.py`、`packages/dataset-tools/src/requirementseeker_dataset/cli.py`（仅若测试证明必要）、`packages/dataset-tools/README.md`、`docs/superpowers/plans/2026-09-25-m2-local-data-refresh.md`。

- [x] CLI 测试固定错误 JSON 和 exit2，不含合成路径/正文/秘密；backup/stage 不恢复不清理，失败 stage 保留；不支持平台在零写入下失败。API/参数无新 flag，成功摘要维持原格式。
- [x] README 区分 sanitize 的旧 UUID/恢复语义和 export-labels 新默认create-only，不改变 sanitizer。描述固定stage/旧UUID阻断、Windows限定、失败不自动清理及威胁模型，不建议删除真实标注或备份。
- [x] 刷新计划只更新预检查和失败策略：检查固定 `.labels-v2.staging`、旧 `.labels-v2.staging.*`、backup 和文件/重定向形式冲突；保持历史结果，写明不能把旧24个标注当成可覆盖模板。不能运行文档中的真实命令。
- [x] 新 CLI 缺口先 RED 再实现；已存在正确输出可作为直接 GREEN 回归，记录区别。文档规格/质量审通过。
- [x] 主代理运行：

```sh
uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests -q
uv run --offline --locked --project packages/dataset-tools ruff check packages/dataset-tools/src packages/dataset-tools/tests
uv run --offline --locked --project packages/dataset-tools ruff format --check packages/dataset-tools/src packages/dataset-tools/tests
uv run --offline --locked --project packages/dataset-tools mypy --config-file packages/dataset-tools/pyproject.toml packages/dataset-tools/src
uv build --offline --project packages/dataset-tools
git diff --check 98bdf81..HEAD
```

运行已有 `tests/integration` 回归的确切位置先用 `rg --files` 确认；按其现有依赖配置执行，不引入新集成基础设施。

Task 3 证据：`1427dde` 新增15项CLI回归、更新README/历史刷新计划，cli.py无需修改。20CLI/完整188 passed、0skip，含3实际junction；首轮14失败为测试辅助误取无video_id字段，修正后直接GREEN，不算产品RED。规格/质量双审独立20 passed且无问题，9 PowerShell块AST0错误。额外隔离synthetic helper运行被环境自动策略拒绝，未得运行证据，未绕过重试；不能将AST解析称为运行安全证明。

主代理于1427dde独立全门禁：188 passed（15.27s）、跨包JSON集成3 passed（0.81s）、Ruff、18文件格式、9源码 strict mypy、离线sdist/wheel0.1.0、基线98bdf81..HEAD diff check均通过。根main d4858f1和DeepSeek分支1eb2426未变，工作树干净；真实标签、凭据、模型及文档真实命令未访问或执行。最终总审进行中。

- [x] 提交 `docs(dataset): document label export transaction safety`，新独立代理总审设计/实现及所有失败所有权边界，修复阻断并重跑门禁。
- [x] 更新根忽略的 HANDOFF 和当日执行日志，记录实际计数/skip及未验证平台。保留分支/工作树，不推送、建PR、合并、关闭issue或刷新标签。

最终总审修正：原1427dde门禁虽188 passed，但发现Windows尾点/尾空格目标按原始名字检查事务、发布时归一成另一目标，6项backup/fixed/legacy组合非法成功；尾空格返回路径不可读。`c87011f` 最小入口拒绝歧义输出组件；完整组RED10 failed/64 passed→GREEN74，保留正常祖先 `..` 语法；完整199 passed、集成3 passed、Ruff/18格式/9源码 strict mypy/diff check通过。等待主代理全门禁与原总审复验，不能沿用原188项总审通过结论。

最终结论（f56fdf6）：主代理最新199 passed（18.73s）/0skip、集成3 passed（1.93s）、Ruff/18格式/9源码 strict mypy/离线构建0.1.0/diff全部通过。原总审独立完整199/集成3及所有门禁通过；六个原别名场景零mkdir/write/rename拒绝，事务字节及目录条目不变，四个无冲突/祖先歧义拒绝、合法祖先..成功、CLI固定错误；P1关闭，无未解决问题。之前12个读/写中出现事务组合、最后输入无效零mkdir、发布前平台能力撤销保留stage也独立实测通过。12个实际junction在Windows执行，原生非Windows未验证；文档PowerShell只有AST证据，额外helper运行被策略拒绝不计通过。

本地收尾完成，根忽略HANDOFF/执行日志同步。保留分支/工作树，不推送、建PR、合并、关闭issue、刷新真实标签或调用模型。再次刷新前须集成本分支并单独授权；24/24只能是结构可评测，不能称逐条人工语义金标。
