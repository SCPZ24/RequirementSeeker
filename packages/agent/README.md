# RequirementSeeker Agent

M0 提供输入输出契约、JSON Schema 和离线校验。M1 在已验证的单视频有效快照上执行确定性三人共识判定；模型调用、语义聚类、采集和机会入库仍未实现。

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

规则从可信请求复制原始评论文本，每位作者只选择一条确定性的代表评论。未知评论引用、未知视频作者、缺失作者、视频作者本人和重复作者不能组成通过结果。M1 不判断评论是否表达相同需求；信号识别与语义聚类属于 M2。

完整说明见仓库 `docs/development/agent-setup.md`，线协议和稳定原因码见 `docs/contracts/agent-v1.md`。未来的业务样本预期保存在 `evals/gold.json`，不代表已完成模型评测。
