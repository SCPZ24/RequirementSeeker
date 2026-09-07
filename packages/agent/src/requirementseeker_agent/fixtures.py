"""Validate a local manifest of expected successes and structural failures."""

from pathlib import Path
from typing import Any, Self

from pydantic import Field, StrictBool, ValidationError, model_validator

from .contracts.common import Contract, Identifier, Text
from .schema import ContractKind, contract_model
from .validation import InputFileError, issues, read_json_text


class FixtureCase(Contract):
    name: Identifier
    kind: ContractKind
    path: Text
    valid: StrictBool
    expected_code: Identifier | None = None

    @model_validator(mode="after")
    def expected_error(self) -> Self:
        if self.valid == (self.expected_code is not None):
            raise ValueError("only_invalid_fixture_requires_expected_code")
        return self


class FixtureManifest(Contract):
    cases: list[FixtureCase] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        if len({case.name for case in self.cases}) != len(self.cases):
            raise ValueError("fixture_names_must_be_unique")
        return self


def check_fixtures(path: Path) -> dict[str, Any]:
    manifest = FixtureManifest.model_validate_json(read_json_text(path))
    root = path.resolve().parent
    reports: list[dict[str, Any]] = []
    for case in manifest.cases:
        target = (root / case.path).resolve()
        if not target.is_relative_to(root):
            raise InputFileError("fixture_path_outside_root")
        # I/O errors are always failures, even for a negative fixture.
        raw = read_json_text(target)
        try:
            contract_model(case.kind).model_validate_json(raw)
            codes: list[str] = []
            matched = case.valid
        except ValidationError as error:
            codes = [issue["code"] for issue in issues(error)]
            matched = not case.valid and case.expected_code in codes
        reports.append({"name": case.name, "matched": matched, "codes": codes})
    failed = sum(not report["matched"] for report in reports)
    return {"checked": len(reports), "failed": failed, "cases": reports}
