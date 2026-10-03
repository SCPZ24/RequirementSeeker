import json
import os
import shutil
import subprocess
from argparse import ArgumentParser
from pathlib import Path

import pytest

import requirementseeker_dataset.labels as label_module
from requirementseeker_dataset.cli import build_parser, main
from requirementseeker_dataset.sanitize import sanitize_root

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "local-test-secret-at-least-32-bytes"


def test_cli_reads_secret_by_environment_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw" / "valid", raw)
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", SECRET)

    result = main(
        [
            "sanitize",
            "--raw",
            str(raw),
            "--plan",
            str(FIXTURES / "approved-manifest.json"),
            "--output",
            str(tmp_path / "sanitized"),
            "--secret-env",
            "RS_DATASET_TEST_SECRET",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert SECRET not in output
    assert json.loads(output)["sampling_manifest_count"] == 1


@pytest.mark.skipif(os.name != "nt", reason="create-only publishing requires Windows")
def test_cli_create_only_publishes_to_absent_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw" / "valid", raw)
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", SECRET)
    output = tmp_path / "sanitized"

    result = main(
        [
            "sanitize",
            "--raw",
            str(raw),
            "--plan",
            str(FIXTURES / "approved-manifest.json"),
            "--output",
            str(output),
            "--secret-env",
            "RS_DATASET_TEST_SECRET",
            "--create-only",
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert output.is_dir()


def _all_options(parser: ArgumentParser) -> set[str]:
    options: set[str] = set()
    for action in parser._actions:
        options.update(action.option_strings)
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                options.update(_all_options(subparser))
    return options


def test_cli_accepts_only_secret_environment_name() -> None:
    options = _all_options(build_parser())

    assert "--secret" not in options
    assert "--secret-env" in options


def test_cli_exports_and_validates_blank_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw" / "valid", raw)
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", SECRET)
    sanitized = tmp_path / "sanitized"
    labels = tmp_path / "labels"
    assert (
        main(
            [
                "sanitize",
                "--raw",
                str(raw),
                "--plan",
                str(FIXTURES / "approved-manifest.json"),
                "--output",
                str(sanitized),
                "--secret-env",
                "RS_DATASET_TEST_SECRET",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["export-labels", "--sanitized", str(sanitized), "--output", str(labels)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert (
        captured.out
        == json.dumps(
            {
                "annotation_count": 1,
                "comment_count": 2,
                "output": str(labels.resolve()),
                "status": "ok",
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert captured.out.isascii()

    assert main(["validate-labels", str(labels), "--sanitized", str(sanitized)]) == 0
    validation_summary = json.loads(capsys.readouterr().out)
    assert validation_summary["annotation_count"] == 1
    assert validation_summary["evaluation_eligible_count"] == 0
    assert validation_summary["status"] == "ok"

    with pytest.raises(SystemExit) as error:
        main(["validate-labels", str(labels)])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_cli_reports_only_safe_error_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "raw-private-marker"
    invalid = tmp_path / marker
    invalid.mkdir()

    assert main(["validate-labels", str(invalid), "--sanitized", str(tmp_path / "source")]) == 2
    output = capsys.readouterr().out
    assert marker not in output
    assert json.loads(output) == {"error": "annotation_files_missing", "status": "error"}


@pytest.fixture
def export_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw" / "valid", raw)
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", SECRET)
    sanitized = tmp_path / "sanitized"
    sanitize_root(raw, FIXTURES / "approved-manifest.json", sanitized, "RS_DATASET_TEST_SECRET")
    return sanitized


def _assert_export_error(
    source: Path,
    output: Path,
    capsys: pytest.CaptureFixture[str],
    code: str,
) -> None:
    assert main(["export-labels", "--sanitized", str(source), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == f'{{"error":"{code}","status":"error"}}\n'
    assert captured.out.isascii()
    assert captured.err == ""
    forbidden = [SECRET, str(source), str(output), "synthetic-private-marker"]
    for path in source.rglob("comments.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            comment = json.loads(line)
            forbidden.extend((comment["text"], comment["comment_id"], path.parent.name))
    assert all(value not in captured.out + captured.err for value in forbidden)


@pytest.mark.parametrize(
    "name", ["labels", ".labels.backup", ".labels.staging", ".labels.staging.old-id"]
)
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_cli_export_conflicts_preserve_existing_bytes(
    tmp_path: Path,
    export_source: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    kind: str,
) -> None:
    conflict = tmp_path / name
    if kind == "directory":
        conflict.mkdir()
        sentinel = conflict / "annotation.json"
    else:
        sentinel = conflict
    original = b"synthetic-private-marker human annotation " + SECRET.encode()
    sentinel.write_bytes(original)
    output = tmp_path / "labels"

    _assert_export_error(
        export_source,
        output,
        capsys,
        "label_output_already_exists"
        if name == "labels"
        else "label_output_transaction_already_exists",
    )

    assert sentinel.read_bytes() == original
    assert set(path.name for path in tmp_path.iterdir()) == {"raw", "sanitized", name}
    if name != "labels":
        assert not output.exists()


@pytest.mark.parametrize("operation", ["write", "publish"])
def test_cli_export_failure_retains_stage_and_blocks_retry(
    tmp_path: Path,
    export_source: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    output = tmp_path / "labels"
    staging = tmp_path / ".labels.staging"
    original_write = Path.write_text

    def fail_write(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.is_relative_to(staging):
            path.write_bytes(b"synthetic-private-marker partial annotation")
            raise OSError(f"{SECRET}: {path}: synthetic-private-marker")
        return original_write(path, data, encoding=encoding, errors=errors, newline=newline)

    def fail_publish(path: Path, target: Path) -> Path:
        raise OSError(f"{SECRET}: {path}: {target}: synthetic-private-marker")

    with monkeypatch.context() as fault:
        if operation == "write":
            fault.setattr(Path, "write_text", fail_write)
        else:
            fault.setattr(Path, "rename", fail_publish)
        _assert_export_error(
            export_source,
            output,
            capsys,
            f"label_output_{'write' if operation == 'write' else 'commit'}_failed",
        )

    files_before = {
        path.relative_to(staging): path.read_bytes()
        for path in staging.rglob("*")
        if path.is_file()
    }
    assert files_before
    assert not output.exists()
    _assert_export_error(export_source, output, capsys, "label_output_transaction_already_exists")
    assert {
        path.relative_to(staging): path.read_bytes()
        for path in staging.rglob("*")
        if path.is_file()
    } == files_before
    assert not output.exists()
    assert not (tmp_path / ".labels.backup").exists()


def test_cli_export_unsupported_platform_writes_nothing(
    tmp_path: Path,
    export_source: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "absent-parent" / "labels"
    original_files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(label_module, "_WINDOWS_CREATE_ONLY", False)

    _assert_export_error(export_source, output, capsys, "label_create_only_unsupported_platform")

    assert not output.parent.exists()
    assert set(path.name for path in tmp_path.iterdir()) == {"raw", "sanitized"}
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == original_files


def test_cli_export_has_no_create_only_or_recovery_flag() -> None:
    parser = build_parser()
    subparsers = next(
        action.choices for action in parser._actions if isinstance(action.choices, dict)
    )
    assert _all_options(subparsers["export-labels"]) == {"-h", "--help", "--sanitized", "--output"}


@pytest.mark.skipif(os.name != "nt", reason="junction regression requires Windows")
@pytest.mark.parametrize("name", [".labels.backup", ".labels.staging", ".labels.staging.old-id"])
def test_cli_export_rejects_transaction_junction_without_touching_destination(
    tmp_path: Path,
    export_source: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    external = tmp_path / "synthetic-private-marker"
    external.mkdir()
    sentinel = external / "annotation.json"
    original = b"synthetic-private-marker human annotation"
    sentinel.write_bytes(original)
    junction = tmp_path / name
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("temporary junction creation unavailable")

    _assert_export_error(export_source, tmp_path / "labels", capsys, "label_output_path_invalid")

    assert junction.is_junction()
    assert sentinel.read_bytes() == original
    assert not (tmp_path / "labels").exists()
