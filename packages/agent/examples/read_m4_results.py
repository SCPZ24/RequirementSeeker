"""仅消费序列化文件；不运行分析或提交。"""

import json
import sys
from pathlib import Path

from m4_result_format import project, results_type, sources_type


def read(directory):
    sources = sources_type.validate_json((directory / "sources.json").read_bytes())
    rows = results_type.validate_json((directory / "results.json").read_bytes())
    if not sources or len({s.request.run_id for s in sources}) != len(sources):
        raise ValueError("missing_or_duplicate_run")
    if rows != [project(source) for source in sources]:
        raise ValueError("result_does_not_match_trusted_run")
    for row in rows:
        outcome = row.result.outcomes[0] if row.result else None
        yield {
            "run_id": row.run_id,
            "status": row.status,
            "reason": row.reason,
            "outcome": outcome.status if outcome else None,
            "intent_key": getattr(outcome, "idempotency_key", None),
            "payload": outcome.model_dump(mode="json") if outcome else None,
        }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    for summary in read(Path(sys.argv[1])):
        print(json.dumps(summary, ensure_ascii=False))
