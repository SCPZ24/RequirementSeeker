"""Offline contract tooling, deliberately separate from the future product CLI."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .fixtures import check_fixtures
from .schema import contract_model, export_schema
from .validation import InputFileError, issues, read_json_text


def _emit(value: dict[str, Any]) -> None:
    # ASCII JSON makes the command portable across Windows console encodings.
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Agent v1 contracts offline; no AI analysis"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a request or result JSON file")
    validate.add_argument("kind", choices=["request", "result"])
    validate.add_argument("path", type=Path)
    fixtures = commands.add_parser("fixtures", help="Check a local synthetic fixture manifest")
    fixtures.add_argument("path", type=Path)
    schema = commands.add_parser("schema", help="Print generated JSON Schema")
    schema.add_argument("kind", choices=["request", "result"])
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            _emit(export_schema(args.kind))
        elif args.command == "fixtures":
            report = check_fixtures(args.path)
            _emit(report)
            return 1 if report["failed"] else 0
        else:
            contract_model(args.kind).model_validate_json(read_json_text(args.path))
            _emit({"valid": True, "kind": args.kind, "schema_version": "1.0"})
    except ValidationError as error:
        _emit({"valid": False, "errors": issues(error)})
        return 1
    except InputFileError as error:
        _emit({"valid": False, "errors": [{"location": [], "code": str(error)}]})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
