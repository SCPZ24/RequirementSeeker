import importlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("kind", ["request", "result"])
def test_committed_schema_matches_generated_schema(kind):
    package = importlib.import_module("requirementseeker_agent")
    model = package.AnalysisRequest if kind == "request" else package.AnalysisResult
    path = ROOT / "schemas" / f"analysis-{kind}.schema.json"
    assert path.exists(), "Versioned JSON Schema has not been exported"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == model.model_json_schema()
    Draft202012Validator.check_schema(saved)


def test_valid_fixtures_also_pass_standard_json_schema():
    manifest = ROOT / "tests/fixtures/manifest.json"
    assert manifest.exists(), "Fixture manifest has not been written"
    for case in json.loads(manifest.read_text(encoding="utf-8"))["cases"]:
        if not case["valid"]:
            continue
        schema = json.loads(
            (ROOT / "schemas" / f"analysis-{case['kind']}.schema.json").read_text(encoding="utf-8")
        )
        data = json.loads((manifest.parent / case["path"]).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)


def test_gold_cases_are_labeled_synthetic_and_reference_existing_inputs():
    path = ROOT / "evals/gold.json"
    assert path.exists(), "Synthetic gold seed has not been written"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    assert dataset["provenance"] == "synthetic"
    assert dataset["status"] == "seed_not_model_evaluated"
    assert len(dataset["cases"]) >= 8
    for case in dataset["cases"]:
        assert (path.parent / case["request_path"]).is_file()
        assert case["expected_behavior"]
