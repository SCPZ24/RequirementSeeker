import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "requirementseeker_agent.cli", *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_validate_valid_request():
    result = run_cli("validate", "request", FIXTURES / "valid/request.json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"valid": True, "kind": "request", "schema_version": "1.0"}


def test_invalid_input_returns_safe_structured_errors(tmp_path):
    data = json.loads((FIXTURES / "valid/request.json").read_text(encoding="utf-8"))
    data["model"]["api_key"] = "synthetic-secret-do-not-echo"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = run_cli("validate", "request", path)
    assert result.returncode == 1
    assert "synthetic-secret-do-not-echo" not in result.stdout + result.stderr
    issue = json.loads(result.stdout)["errors"][0]
    assert issue == {"location": ["model", "<unknown-field>"], "code": "extra_forbidden"}


@pytest.mark.parametrize(
    "raw",
    [b'{"text": "private-raw-text"', b"\xff", b"{}" * 1_100_000],
    ids=["malformed", "encoding", "oversized"],
)
def test_unreadable_or_oversized_json_has_no_traceback_or_raw_input(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    result = run_cli("validate", "request", path)
    assert result.returncode == 1
    assert "private-raw-text" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is False


def test_missing_file_is_a_structured_error(tmp_path):
    result = run_cli("validate", "request", tmp_path / "missing.json")
    assert result.returncode == 1
    assert json.loads(result.stdout)["errors"][0]["code"] == "file_unreadable"


def test_fixture_manifest_verifies_both_positive_and_negative_samples():
    result = run_cli("fixtures", FIXTURES / "manifest.json")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["failed"] == 0
    assert report["checked"] >= 16


def test_empty_manifest_cannot_report_success(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"cases": []}', encoding="utf-8")
    result = run_cli("fixtures", path)
    assert result.returncode == 1


def test_fixture_path_cannot_escape_manifest_directory(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "escape",
                        "kind": "request",
                        "path": "../outside.json",
                        "valid": False,
                        "expected_code": "file_unreadable",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = run_cli("fixtures", path)
    assert result.returncode == 1
    assert "fixture_path_outside_root" in result.stdout


def test_schema_subcommand_outputs_json():
    result = run_cli("schema", "request")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["title"] == "AnalysisRequest"


@pytest.mark.parametrize("stamp", ["0001-01-01T00:00:00+08:00", "9999-12-31T23:59:59-08:00"])
def test_out_of_range_utc_timestamp_is_a_structured_error(tmp_path, stamp):
    data = json.loads((FIXTURES / "valid/request.json").read_text(encoding="utf-8"))
    data["analysis_time"] = stamp
    path = tmp_path / "overflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = run_cli("validate", "request", path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["errors"][0]["code"] == "timestamp_out_of_supported_range"


def test_unknown_field_name_cannot_leak_input(tmp_path):
    data = json.loads((FIXTURES / "valid/request.json").read_text(encoding="utf-8"))
    data["model"]["synthetic-secret-in-key"] = "untrusted-content"
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = run_cli("validate", "request", path)
    assert result.returncode == 1
    assert "synthetic-secret-in-key" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["errors"][0]["location"] == ["model", "<unknown-field>"]
