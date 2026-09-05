# RequirementSeeker Agent

M0 提供输入输出契约、JSON Schema 和离线校验。它尚不执行共识判定、模型调用、采集或机会入库。

从仓库根目录运行：

```sh
uv sync --project packages/agent --locked
uv run --project packages/agent rs-agent --help
uv run --project packages/agent rs-agent fixtures packages/agent/tests/fixtures/manifest.json
uv run --project packages/agent pytest packages/agent/tests -q
```

安装依赖需要访问包索引或已存在的缓存，安装后的校验不需要网络、模型 Key 或平台登录。

完整说明见仓库 `docs/development/agent-setup.md`，线协议见 `docs/contracts/agent-v1.md`。未来的业务样本预期保存在 `evals/gold.json`，不代表已完成模型评测。
