# RequirementSeeker 本地机会 CLI · Windows 离线交付

本包面向 Windows x64，需已安装 CPython 3.12 x64（含 venv、ensurepip 和 sqlite3）。下文使用 PowerShell 和 Python Launcher。没有 py.exe 时，将 `py -3.12` 换成本机该版本 python.exe 的完整路径。所有示例从同一个 PowerShell 窗口运行。固定合成输入和判断器均在包中。

## 核包并安装

从交付报告取得 ZIP 的 SHA256，先校验下载到当前目录的文件。使用新的解包目录、虚拟环境和数据目录。把 ZIP 保留在解包目录外。

```powershell
$ErrorActionPreference = 'Stop'
$zip = (Resolve-Path '.\B36-windows-offline-host.zip').Path
$expected = Read-Host '粘贴交付报告中的 ZIP SHA256'
if ((Get-FileHash -Algorithm SHA256 $zip).Hash -ne $expected.Trim()) { throw 'ZIP 摘要不一致' }
$pkg = Join-Path $PWD 'b36-package'
$envDir = Join-Path $PWD 'b36-venv'
$data = Join-Path $PWD 'b36-data'
foreach ($p in @($pkg,$envDir,$data)) { if (Test-Path $p) { throw "请使用新路径 $p" } }
Expand-Archive -LiteralPath $zip -DestinationPath $pkg
py -3.12 -I -B -X utf8 "$pkg\install.py" $envDir
if ($LASTEXITCODE -ne 0) { throw '安装失败，请保留失败目录' }
New-Item -ItemType Directory $data | Out-Null
$python = Join-Path $envDir 'Scripts\python.exe'
$bundle = Join-Path $pkg 'bundle'
$intent = Join-Path $pkg 'host\persistent.py'
$op = Join-Path $pkg 'host\opportunities.py'
```

安装器先核对 MANIFEST.json 所列的文件全集和逐项摘要，然后用 bundle/runtime-requirements.txt 锁定的版本和 wheel 哈希执行 pip 离线普通安装。ZIP 摘要覆盖外层清单本身。保留解包目录原貌，将环境及运行输出放在它之外。本包使用安装到 site-packages 的 Agent，host 下的脚本可从任意工作目录调用。bundle 内原始 A32 README 与源码归档只用于追溯。

## 从固定请求到机会、来源和重放

以下循环演示首次新建及首次追加，每种场景各用一个新库。两种场景使用相同的三条源证据，同库执行会识别为同一意图。一个库中已有的合成历史目标为 past-export。

```powershell
foreach ($case in @('new','append')) {
    $db = Join-Path $data "$case.sqlite"
    & $python -I -B -X utf8 $op --bundle $bundle --database $db init
    if ($LASTEXITCODE -ne 0) { throw '初始化失败' }
    $run = & $python -I -B -X utf8 $intent --bundle $bundle --database $db run --case $case --output "$data\$case-first" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $run.host_status -ne 'committed') { throw '意图未提交' }
    $applied = & $python -I -B -X utf8 $op --bundle $bundle --database $db apply $run.run_id | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $applied.status -ne 'applied') { throw '机会未应用' }
    & $python -I -B -X utf8 $op --bundle $bundle --database $db list
    if ($LASTEXITCODE -ne 0) { throw '列表读取失败' }
    & $python -I -B -X utf8 $op --bundle $bundle --database $db detail $applied.opportunity_id
    if ($LASTEXITCODE -ne 0) { throw '详情读取失败' }
    $again = & $python -I -B -X utf8 $intent --bundle $bundle --database $db run --case $case --output "$data\$case-second" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $again.host_status -ne 'already_committed') { throw '重放意图异常' }
    $replay = & $python -I -B -X utf8 $op --bundle $bundle --database $db apply $again.run_id | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $replay.status -ne 'already_applied') { throw '重放应用异常' }
    $replay.original_receipt
}
```

list/detail 返回机会及人工阶段、人工备注。详情中的 sources 给出完整引文、作者与评论 ID、原意图 payload、首次 run_id 和应用回执。新建目标为 local- 加来源键。追加目标为 past-export。第二次运行生成新的 run_id，数据库保留原 payload 和首次应用回执。人工字段由宿主保留。

## 失败、重试和恢复

```powershell
$db = Join-Path $data 'errors.sqlite'
& $python -I -B -X utf8 $intent --bundle $bundle --database $db run --case retryable --max-retries 2 --output "$data\timeout"
$LASTEXITCODE   # 预期 1，三次固定超时
& $python -I -B -X utf8 $intent --bundle $bundle --database $db run --case unknown --max-retries 2 --output "$data\unknown"
$LASTEXITCODE   # 预期 1，一次历史判断失败
$runId = (Get-ChildItem "$data\unknown" -Directory).Name
& $python -I -B -X utf8 $intent --bundle $bundle --database $db read $runId
```

每一行输出是一条 JSON 记录。run 的正常提交、已提交重放和合法零机会退出 0。检查 agent_proposal.status/outcome/reason、host_status 和 apply.status。零机会场景 zero 的提议为 no_opportunity，host_status 为 not_committed。unknown 保留 incomplete、空 payload 和原 agent_reason。失败退出 1，参数错误退出 2。read/list/detail 退出 0 表示可读取。只有正式 retryable_error 分支接受 0 至 2 次额外重试，默认 0，各次 retry_of_run_id 指向前次。每次 run 需要新的输出根，复用旧根会被拒绝。

受信输入来自 bundle/tests/fixtures/valid/request.json，经固定 examples 适配器构造本次三个合成评论。每次运行先验证 A32 完整清单及安装包字节，再执行 Agent。来源校验以受信运行输入和本地库为基础，来源键绑定完整证据。MANIFEST.json 记录包内文件身份，ZIP 摘要由交付报告独立提供。

attempt、sources/results 和 Agent 提议文件先落盘。宿主持久意图与运行回执在同一个 SQLite 事务中提交。host-receipt.json 随后导出。机会 apply 是后续独立的数据库事务，包含机会、来源关联及应用状态。终端输出发生在对应提交之后。进程退出 1 时请保留原目录，根据子目录名取得 run_id，用 persistent.py read 查询数据库。事务前中断可能仅留下文件，数据库会拒绝查询缺失的运行。事务已提交时，即使导出失败，read 也可恢复 host_status。apply 输出异常时，通过 list/detail 查看状态，重复 apply 可取回首次回执。数据库与导出文件分别提交。

可在新的输出根使用 persistent.py run 的 `--fault file` 验证提交后导出失败，再执行 read。opportunities.py apply 的 `--fault stdout` 可验证提交后终端故障。操作范围为固定合成输入、Windows、本地串行 SQLite 和新数据库。旧库迁移仍待验。
