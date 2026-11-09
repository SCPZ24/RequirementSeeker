# B33 最小本地合成宿主

本CLI只支持Windows/Python3.12及已验收A32-r1固定合成输入，不接真实评论、模型或配置。源码基线26d7a1ba；Agent版本0.3.0不唯一，必须核r1 ZIP SHA256 `42b60c718073b2687cdc8da05582ba6f8e9e24e82a27dc0e253bfaf9b94b6f6a`。先按包README在源码外全新venv普通离线安装其runtime-requirements.txt；禁止editable或源码PYTHONPATH。

```powershell
$python='E:/RequirementSeeker-handoffs/B33-2026-11-09/venv/Scripts/python.exe'
$hostCli='E:/.codex/worktrees/b33-minimal-host/RequirementSeeker/packages/host/cli.py'
$bundle='E:/RequirementSeeker-handoffs/B33-2026-11-09/unpacked/bundle'
& $python -I -B -X utf8 $hostCli --bundle $bundle run --case replay --output 'E:/RequirementSeeker-handoffs/B33-2026-11-09/my-new-run'
# 从stdout取得run_id，独立进程读回对应子目录；替换下方RUN_ID。
& $python -I -B -X utf8 $hostCli --bundle $bundle read 'E:/RequirementSeeker-handoffs/B33-2026-11-09/my-new-run/RUN_ID'
```

run的case：new、append、replay、zero、incomplete、unknown、fatal、retryable、cancelled、budget。它们都从r1固定request.json构造合成请求，调用安装包公开analyze_m2与run_h0；不是读取样例结果冒充执行。宿主复用已审r1 examples中的固定judge、历史替身和本地来源/结果格式，examples目录是测试适配依赖，Agent仍从site-packages加载。每次启动检查完整r1清单、wheel摘要及37个安装包文件字节；网络socket连接/解析/发送被拒绝。

run退出0表示committed/already_committed或合法no_opportunity；退出1表示未完成、错误、取消、预算或输入/记录拒绝；参数错误退出2。read退出0仅表示记录可读取，不代表业务成功；必须查看status/outcome/reason。incomplete/null不得转为零机会。unknown场景的历史判断失败可能无法映射正式v1结果，宿主独占host-error.json保存未完成和Agent原始原因，由read明确展示，不伪造正式结果。

`--max-retries 0|1|2`默认0，仅正式outcome=retryable_error触发额外尝试；其它失败及未知不重试。每次尝试独立run_id，新请求retry_of_run_id指向上一尝试；Agent内部传输/输出修复次数置0，避免双层放大。超时场景固定失败，所以额外2次后仍退出1，不宣称恢复成功。replay是同进程共享MemoryIntentStore的两次实际调用，分别存储回执；不提供跨进程恢复/幂等，不是数据库提交。机会人工字段及旧payload由已审Agent保留。

输出根必须全新。每次attempt.json在调用前create-only/fsync，随后sources/results及receipt.json；I/O中断保留半成品并由read拒绝。read核来源投影、回执及文件摘要；它不证明来源的外部真实性，也不是防恶意本机全量篡改的签名系统。不要手改旧回执或删除原始失败记录。修复后另建输出根。

标准库测试（同一普通安装环境）：

```powershell
& $python -I -B -X utf8 'E:/.codex/worktrees/b33-minimal-host/RequirementSeeker/packages/host/tests/test_cli.py' $hostCli $bundle
```

8个测试方法含多个子场景，覆盖独立读取、重试上限/链路、非可重试停止、重放、旧目录逐字节保护、半写及篡改拒绝、完成回执后的输出故障及冲突错误文件。验证记录与实际持久演示位于B33源码外证据目录，精确提交和C33独立结论见模块报告；此README不是C验收声明。不包含API/Web、调度、生产持久化、Key配置、真实模型、H1或macOS验收。

完成receipt存在时，read必须完整验证其身份、摘要及来源投影；旁路host-error不能遮蔽它，receipt损坏也不能降级绕过验证。完成落盘后的终端输出失败只影响命令交付，不生成相冲突的业务失败回执。
