# M0 验收记录

- 日期：2026-09-05。
- 分支：`codex/agent-core`，基线 `3b925cc`。
- 状态：M0 完成，已整理为本地提交；尚未推送或合并。
- 范围：工程底座、契约、样本、离线校验与说明，不包含 M1 需求规则执行。

## 交付物

| 交付 | 位置 |
|---|---|
| 独立 Python 包、工具配置与锁文件 | [packages/agent](../../packages/agent/) |
| 输入输出与八种领域概念 | [contracts](../../packages/agent/src/requirementseeker_agent/contracts/) |
| 两份导出 JSON Schema | [schemas](../../packages/agent/schemas/) |
| 离线 validate / fixtures / schema 命令 | [cli.py](../../packages/agent/src/requirementseeker_agent/cli.py) |
| 30 个正反例契约样本 | [manifest.json](../../packages/agent/tests/fixtures/manifest.json) |
| 10 个合成业务评测种子 | [gold.json](../../packages/agent/evals/gold.json) |
| 无密钥模型配置示例 | [model-config.example.json](../../packages/agent/examples/model-config.example.json) |
| 工程决策、保留与重放语义 | [ADR-001](../architecture/ADR-001-agent-foundation.md) |
| 字段、状态、错误与责任说明 | [Agent v1 契约](../contracts/agent-v1.md) |
| 安装、开发和离线验收命令 | [开发说明](agent-setup.md) |

## 实际验证

环境：Windows、CPython 3.12.10、uv 0.11.7。工具与依赖版本详见 ADR 和锁文件。

| 检查 | 结果 |
|---|---|
| 自动测试 | 57 项通过 |
| 正反例 manifest | 30 项符合预期，0 项失败 |
| 标准 JSON Schema | 合法样本通过；导出 Schema 与模型一致 |
| Ruff Lint / format | 通过 |
| mypy strict | 10 个源文件通过 |
| uv 锁文件 | 一致 |
| 构建 | sdist 与 wheel 成功 |
| wheel 内容 | 包含 MIT 许可证与 py.typed；没有本地环境或测试原文 |
| 独立 wheel 安装 | 新虚拟环境安装成功，源码目录外导入来自 site-packages |
| 安装后离线校验 | 屏蔽 socket 创建时仍能校验请求，返回 valid=true |

测试采用先失败后实现：最初 37 项契约测试因公共模型缺失失败；实现后通过。CLI/Schema 随后补齐测试与实现。独立代码审查发现并复现了两类问题，均添加失败回归后修复：

1. 极端时间和保留期算术溢出逃逸为原始异常：UTC 转换现在给出固定错误；保留窗口改用时间差检查。
2. 未知字段名可把输入内容带入诊断：未知字段位置使用 `<unknown-field>`，只保留已知父路径和数组下标。

最终代码检查、文档相对链接与 Git 差异检查作为本次交付收尾验证。30 个 fixture 的通过表示正例合法、反例按约定失败，不等于 30 次业务分析成功。

## 剩余边界

- 尚未执行三人共识算法、语义聚类、真实模型调用、预算结算、幂等键计算、数据库提交或数据删除。
- 输出结构符合 Schema 仍不代表证据真实，后续须与可信请求核对并完成跨记录一致性验证。
- 合成金标仅为种子，未经过真实模型评测或项目双方的标注裁决。
- macOS、真实采集 fixture、平台登录、CI 云端运行和全栈接入尚未验证。
- M0 完成不等于 H0 可接入分析模块或 H1 完整 Agent 验收完成。

下一步是 M1：在该契约上实现确定性预处理、来源核验、作者排除、三人门槛及跨日快照边界测试。
