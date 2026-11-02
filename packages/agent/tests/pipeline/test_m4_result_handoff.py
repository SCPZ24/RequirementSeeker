import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def generated(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / "generate_m4_results.py"), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return tmp_path


def read(directory):
    return subprocess.run(
        [sys.executable, str(EXAMPLES / "read_m4_results.py"), str(directory)],
        capture_output=True,
        text=True,
    )


def test_independent_read_of_real_runs(generated):
    proc = read(generated)
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [(r["status"], r["outcome"]) for r in rows] == [
        ("committed", "merge_evidence"),
        ("already_committed", "merge_evidence"),
        ("incomplete", None),
        ("committed", "new_candidate"),
        ("already_committed", "new_candidate"),
        ("no_opportunity", "rejected"),
        ("incomplete", "cancelled"),
        ("incomplete", "budget_exhausted"),
        ("incomplete", "fatal_error"),
        ("incomplete", "retryable_error"),
    ]
    assert rows[0]["intent_key"] == rows[1]["intent_key"]
    assert rows[3]["intent_key"] == rows[4]["intent_key"]
    assert rows[2]["reason"] == "history_incomplete"
    schema = json.loads((EXAMPLES.parent / "schemas/analysis-result.schema.json").read_bytes())
    validator = Draft202012Validator(schema)
    for row in json.loads((generated / "results.json").read_bytes()):
        if row["result"] is not None:
            validator.validate(row["result"])


@pytest.mark.parametrize(
    "fault",
    ["target", "candidate", "quote", "key", "state", "null_cancel", "missing_run", "failure"],
)
def test_reader_rejects_corrupted_artifact(generated, fault):
    path = generated / "results.json"
    rows = json.loads(path.read_bytes())
    merge = rows[0]["result"]["outcomes"][0]["merge"]
    if fault == "target":
        merge["target_opportunity_id"] = "invented"
    elif fault == "candidate":
        merge["candidate_id"] = "invented"
    elif fault == "quote":
        merge["evidence"][0]["text"] = "other"
    elif fault == "state":
        rows[1]["status"] = "committed"
    elif fault == "key":
        rows[1]["result"]["outcomes"][0]["idempotency_key"] = "0" * 64
    elif fault == "null_cancel":
        rows[6]["result"] = None
    elif fault == "missing_run":
        rows.pop()
    else:
        rows[2]["reason"] = "no_signal"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert read(generated).returncode != 0
