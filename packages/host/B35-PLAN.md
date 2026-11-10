# B35 最小本地机会管理

2026-11-10，B负责；原人类继续指令已直接核验。起点0dd2f0d，功能基线9d5ded2。本文为本包唯一活动计划，B33/B34计划为历史。继续使用冻结r1普通安装，Agent和旧树不改。

方案：新增opportunities.py提供init/apply/list/detail，复用persistent.py受信意图/来源校验。显式apply已持久意图；new生成local-来源键机会，merge只追加受信history中已初始化的目标。机会与来源关联、applications处理状态在同库事务；按完整来源键重放原应用结果。原意图payload不修改，机会人工字段只初始化。SQLite提交后再输出；只读list/detail用于重启恢复，无导出文件/DB跨资源原子承诺。

init仅首次建表时复制固定受信历史机会；重复init不恢复被删坏目标，不自动覆盖人工字段。list/detail展示机会、来源（含原意图/首次run及完整证据）和应用状态；不扩人工编辑/孵化流程/通用框架。

- [x] 新环境普通安装r1，记录精确身份与基线。
- [x] tests/test_opportunities.py先RED：独立进程新建/追加/重放及列表详情；坏目标/unknown拒绝；人工字段与旧payload保护；事务中途回滚、提交后输出故障恢复。
- [x] 最小实现opportunities.py，保持所有已有意图校验、目标和来源绑定；SQL故障反例与串行重放GREEN。
- [x] 新目录持久演示及逐命令日志，Ruff/diff检查，README说明边界，冻结提交与B独占报告。
- [ ] 直交原C35独立验收；范围内反例TDD返修闭环，再同步记录。

只写packages/host及B35专属报告/证据；Windows固定合成本地串行，不读Key/真实provider/上传付费/多写者/生产服务/发布推送合并/清理旧材料。无下级，公共三文件仅主控维护。
