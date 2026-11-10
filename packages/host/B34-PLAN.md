# B34 最小本地持久化计划

负责人B，2026-11-09；原人类批准与请求已直接核验。起点f157c973，功能基线8c403bb。仅本地串行固定合成；Agent/r1冻结。本文是B34唯一活动计划，B33 PLAN仅历史。

设计：标准库SQLite保存意图（含原payload、完整证据和可信来源）、固定历史（含人工字段）、每次运行的Agent提议和宿主持久状态。复用B33实际安装包调用及来源校验；Agent内存committed仅为提议，SQLite提交后才称host committed。按完整来源证据定位原意图，重启重放保留原payload/首次回执；未知及历史不全不写意图。

选择SQLite而非自行实现JSON日志/多文件事务，避免新增依赖及恢复协议。数据库意图和运行回执同一事务；B33提议文件在事务前产生，host-receipt导出在事务后产生。文件失败不回滚已提交数据库；read从数据库恢复事实，不覆盖失败目录。明确不提供跨资源原子、多写者、远程DB或生产保障。

- [x] 新证据目录普通离线安装r1；核身份并复跑B33基线。
- [x] 新增tests/test_persistence.py，独立进程运行persistent.py的run/read命令，先RED：新建/追加重启重放、原payload/人工字段、unknown/incomplete无意图、旧目录保护、有限重试链、事务中途失败回滚、提交后file/stdout故障恢复。
- [x] 新增persistent.py：复用cli执行和读取，来源验证后SQLite事务insert意图与运行记录；已有证据返回原意图/首次回执；持久状态与Agent提议明确分开。合成故障注入仅在事务中、事务后文件/终端三个边界。
- [x] GREEN与持久演示，逐命令cwd/UTC/退出码及原始失败日志写B34专用证据，不覆盖旧证据。Ruff/diff检查，提交冻结。
- [ ] README/B34报告写明恢复操作、原子边界及限制；直交原C34独立复验，范围内问题返修闭环。

文件仅packages/host/persistent.py、tests/test_persistence.py、README.md与本计划；B独占报告在主仓docs/execution/2026-11-09-control-B34-local-persistence.md。无下级，不修改公共三文件；不读Key/真实调用/上传付费/发布推送合并/清理旧材料。
