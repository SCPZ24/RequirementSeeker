import json
import subprocess
import sys
from pathlib import Path

DEMO = Path(__file__).parents[1] / "examples" / "m2_fake_demo.py"


def test_fake_demo_is_offline_deterministic_and_complete() -> None:
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(DEMO)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    result = json.loads(outputs[0])
    assert result["status"] == "completed"
    assert len(result["signal_ids"]) == 3
    assert all(item.startswith("sig_") for item in result["signal_ids"])
    assert len(result["cluster_ids"]) == 1
    assert result["cluster_ids"][0].startswith("clu_")
    assert result["decisions"] == [{"cluster_id": result["cluster_ids"][0], "passed": True}]
    assert result["budget"]["model_calls_consumed"] == 2
    assert result["budget"]["comments_consumed"] == 3


def test_real_evaluation_demo_verifies_synthetic_identity() -> None:
    completed = subprocess.run(
        [sys.executable, str(DEMO), "--real-evaluation"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "completed"
    assert len(result["signal_ids"]) == 3
    assert len(result["cluster_ids"]) == 1
    assert result["decisions"][0]["passed"] is True
    assert len(result["audits"]) == result["budget"]["model_calls_consumed"] == 2
    for audit in result["audits"]:
        assert audit["requested_model_name"] == audit["actual_model_name"] == "scenario-model"
        assert audit["requested_revision"] == audit["actual_revision"] == "m2-fixture-1"
