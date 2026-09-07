# Agent M0 安装、离线验证与开发

当前包只提供契约工具，没有产品 `rs explore`、模型请求或服务器。

## 环境

需要 Python 3.12.10 与 uv。仓库 `.python-version` 位于 `packages/agent`；当前已验证环境为 Windows、uv 0.11.7。依赖已锁定，首次安装需要网络或本地缓存，后续校验无需模型 Key、平台账号或网络。

以下命令均从仓库根目录执行；PowerShell 和 macOS shell 可使用相同命令。macOS 命令尚未在本次环境实跑，不能据此宣称完成目标平台验证。

```sh
uv sync --project packages/agent --locked
uv run --project packages/agent rs-agent --help
uv run --project packages/agent rs-agent validate request packages/agent/tests/fixtures/valid/request.json
uv run --project packages/agent rs-agent validate result packages/agent/tests/fixtures/valid/result-new.json
uv run --project packages/agent rs-agent fixtures packages/agent/tests/fixtures/manifest.json
```

单文件成功时 `valid=true`；失败时 `valid=false` 和结构化错误，退出码 1。命令用法错误退出码 2。样本批量验证成功表示正例合法、反例按期望规则失败，不代表所有输入都应通过。

CLI 每个 JSON 文件最多读取 2 MiB，支持 UTF-8 BOM。批量样本只读取 manifest 所在目录内的文件；路径逃逸、缺失文件或错误 manifest 本身不会被当作负样本成功。

## 模型配置样例

[model-config.example.json](../../packages/agent/examples/model-config.example.json) 仅演示配置引用和能力声明，名字为合成模型。无需创建 `.env` 即可运行 M0。

未来宿主把配置引用解析为本机模型配置，密钥不进入请求/结果 fixture、日志或 Git。`.gitignore` 已忽略 `.env*`（保留 `.env.example`）、本地数据目录和常见登录状态文件。

## 开发检查

```sh
uv run --project packages/agent pytest packages/agent/tests -q
uv run --project packages/agent ruff check packages/agent
uv run --project packages/agent ruff format --check packages/agent
uv run --project packages/agent mypy --config-file packages/agent/pyproject.toml packages/agent/src
uv lock --project packages/agent --check
uv build --project packages/agent
git diff --check
```

测试覆盖结构边界、时间与字段关系、正反例、Schema 漂移、命令退出码和安全诊断；没有访问真实模型或平台。合成业务种子在 [evals/gold.json](../../packages/agent/evals/gold.json)，其 status 明确为尚未进行模型评测。

`schema` 子命令把生成的结构打印为 JSON：

```sh
uv run --project packages/agent rs-agent schema request
uv run --project packages/agent rs-agent schema result
```

修改模型后，应使用 Python 的 `Path.write_text(json.dumps(Model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")` 更新对应 Schema 文件；避免旧 PowerShell 的输出重定向生成 UTF-16。测试会核对模型与两个导出文件逐项一致。

## 构建与独立安装

构建会产生 `packages/agent/dist/requirementseeker_agent-0.1.0.tar.gz` 与 `.whl`。M0 构建产物不发布到包索引。

要验证 wheel，可用 `uv venv --python 3.12.10 .local-data/agent-wheel-check` 创建临时环境，再通过 `uv pip install --python <该环境的Python路径> <wheel路径>` 安装。Windows 的解释器路径为 `Scripts/python.exe`，macOS 为 `bin/python`。

在源码目录外用新环境的 Python 运行 `-m requirementseeker_agent.cli validate request <请求样例绝对路径>`，应得到 valid=true。wheel 内置 Python 契约与 Schema 导出能力；静态 fixtures、评测种子和开发说明随源码仓库交付，CLI 不依赖源码相对路径才能启动。

## 完成范围

M0 不生成业务分析结果。M1 将实现可信输入与证据核验、预处理和三人门槛；M2 接入模型。M5/H0/H1、实际采集格式、macOS 验证和后端事务都仍需后续验收。完整路线见 [交付计划](2026-09-05-agent-delivery-plan.md)。
