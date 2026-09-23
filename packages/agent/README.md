# RequirementSeeker Agent

M0 提供输入输出契约、JSON Schema 和离线校验。M1 在已验证的单视频有效快照上执行确定性三人共识判定。M2 增加采样、预算、缓存、需求信号与同视频聚类的离线管线，由确定性场景模型驱动。真实模型适配、采集和机会入库尚未包含在本包中。

从仓库根目录运行：

```sh
uv sync --project packages/agent --locked
uv run --project packages/agent rs-agent --help
uv run --project packages/agent rs-agent fixtures packages/agent/tests/fixtures/manifest.json
uv run --project packages/agent pytest packages/agent/tests -q
```

安装依赖需要访问包索引或已存在的缓存，安装后的校验不需要网络、模型 Key 或平台登录。

## M1 共识规则

调用方先提供完整有效的 `AnalysisRequest`，再传入显式 `NeedCluster`：

```python
from pathlib import Path

from requirementseeker_agent import AnalysisRequest, NeedCluster, evaluate_consensus

request = AnalysisRequest.model_validate_json(
    Path("packages/agent/tests/fixtures/valid/request.json").read_text(encoding="utf-8")
)
cluster = NeedCluster(
    cluster_id="cluster-1",
    comment_ids=["comment-1", "comment-2", "comment-3"],
    summary="合成需求簇",
)
decision = evaluate_consensus(request, cluster)
print(decision.model_dump_json(indent=2))
```

规则从可信请求复制原始评论文本，每位作者只选择一条确定性的代表评论。未知评论引用、未知视频作者、缺失作者、视频作者本人和重复作者不能组成通过结果。M1 不判断评论是否表达相同需求；信号识别与语义聚类由 M2 管线处理。

## M2 离线演示

从仓库根目录运行：

```sh
uv run --project packages/agent python packages/agent/examples/m2_fake_demo.py
```

演示使用仓库中的合成请求和 `ScenarioModelGateway`，不连接平台或模型服务。JSON 输出包含 `completed` 状态、稳定信号与簇 ID、一项通过的三人共识决策，以及评论、调用和 Token 用量。宿主正式调用时需提供同一视频的 `AnalysisRequest`、`SamplingManifest`、`SamplingPlan`、模型网关与语义缓存，再调用 `analyze_m2`；可通过可选的 `cancellation_probe` 在运行中请求取消。

完整说明见仓库 [开发说明](../../docs/development/agent-setup.md)，线协议和稳定原因码见 [Agent v1 契约](../../docs/contracts/agent-v1.md)，M2 当前验收边界见 [离线验收记录](../../docs/development/2026-09-07-m2-acceptance.md)。`evals/gold.json` 只是业务评测种子；真实模型语义评测尚未完成。
