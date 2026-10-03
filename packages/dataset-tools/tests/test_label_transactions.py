"""用合成材料验证合作事务竞态和可观测的 Windows 路径跳转。"""

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread

import pytest

import requirementseeker_dataset.labels as label_module
from requirementseeker_dataset.contracts import SamplingManifest
from requirementseeker_dataset.labels import LabelValidationError, export_labels
from requirementseeker_dataset.sanitize import sanitize_root

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows rename/junction")


@pytest.fixture
def sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw" / "valid", raw)
    root = tmp_path / "sanitized"
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", "synthetic-secret-at-least-32-bytes")
    sanitize_root(raw, FIXTURES / "approved-manifest.json", root, "RS_DATASET_TEST_SECRET")
    return root


def _files(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        pytest.skip(f"Windows junction creation unavailable (exit {result.returncode})")
    assert link.is_junction()


def test_real_windows_rename_publishes_new_target(sanitized: Path, tmp_path: Path) -> None:
    output = tmp_path / "labels"
    result = export_labels(sanitized, output)
    assert len(result.output_files) == len(result.annotations) == 1
    assert all(path.is_file() and path.is_relative_to(output) for path in result.output_files)
    assert all(item.need_signal == "unlabeled" for item in result.comments)
    assert not (tmp_path / ".labels.staging").exists()


@pytest.mark.parametrize("target_kind", ["file", "empty_directory", "manual_directory"])
def test_real_rename_preserves_target_created_after_last_check(
    sanitized: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    output = tmp_path / "labels"
    stage = tmp_path / ".labels.staging"
    rename = Path.rename
    staged: dict[Path, bytes] = {}

    def appear_then_rename(path: Path, target: Path) -> Path:
        nonlocal staged
        assert path == stage and target == output
        staged = _files(stage)
        assert len(staged) == 1
        if target_kind == "file":
            target.write_bytes(b"synthetic manual target")
        else:
            target.mkdir()
            if target_kind == "manual_directory":
                (target / "annotation.json").write_bytes(b"synthetic manual annotation")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", appear_then_rename)
    with pytest.raises(LabelValidationError, match="^label_output_commit_failed$"):
        export_labels(sanitized, output)
    assert _files(stage) == staged
    if target_kind == "file":
        assert output.read_bytes() == b"synthetic manual target"
    elif target_kind == "empty_directory":
        assert output.is_dir() and list(output.iterdir()) == []
    else:
        assert _files(output) == {Path("annotation.json"): b"synthetic manual annotation"}


def test_concurrent_loser_cannot_write_or_delete_owned_stage(
    sanitized: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / ".labels.staging"
    output = tmp_path / "labels"
    acquired, release = Event(), Event()
    mkdir = Path.mkdir

    def pause_owner(
        path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        if path == stage:
            (stage / "owner-sentinel").write_bytes(b"synthetic winner")
            acquired.set()
            assert release.wait(10), "owner release timed out"

    monkeypatch.setattr(Path, "mkdir", pause_owner)
    with ThreadPoolExecutor(max_workers=1) as executor:
        winner = executor.submit(export_labels, sanitized, output)
        try:
            assert acquired.wait(10), "stage acquisition timed out"
            before = _files(stage)
            entries = sorted(stage.rglob("*"))
            with pytest.raises(
                LabelValidationError, match="^label_output_transaction_already_exists$"
            ):
                export_labels(sanitized, output)
            assert stage.is_dir() and _files(stage) == before
            assert sorted(stage.rglob("*")) == entries
            assert not output.exists()
        finally:
            release.set()
        result = winner.result(timeout=10)
    assert result.output_files[0].is_file()
    assert (output / "owner-sentinel").read_bytes() == b"synthetic winner"
    assert not stage.exists()


def test_stale_precheck_loser_acquires_empty_stage_but_cannot_write(
    sanitized: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / ".labels.staging"
    output = tmp_path / "labels"
    ready, release = Event(), Event()
    mkdir = Path.mkdir
    main_thread = current_thread()

    def pause_before_acquisition(
        path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if path == stage and current_thread() is not main_thread:
            ready.set()
            assert release.wait(10), "stale caller release timed out"
        mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", pause_before_acquisition)
    with ThreadPoolExecutor(max_workers=1) as executor:
        loser = executor.submit(export_labels, sanitized, output)
        try:
            assert ready.wait(10), "pre-acquisition pause timed out"
            winner = export_labels(sanitized, output)
            published = _files(output)
        finally:
            release.set()
        with pytest.raises(LabelValidationError, match="^label_output_already_exists$"):
            loser.result(timeout=10)
    assert winner.output_files[0].is_file()
    assert _files(output) == published
    assert stage.is_dir() and list(stage.iterdir()) == []


@pytest.mark.parametrize(
    "location", ["input_root", "input_ancestor", "output_ancestor", "backup", "stage", "legacy"]
)
def test_real_junctions_are_rejected_without_changing_external_sentinel(
    sanitized: Path, tmp_path: Path, location: str
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "sentinel").write_bytes(b"synthetic external manual work")
    output = tmp_path / "labels"
    input_root = sanitized
    if location == "input_root":
        shutil.copytree(sanitized, external / "sanitized")
        input_root = tmp_path / "input-link"
        _junction(input_root, external / "sanitized")
    elif location == "input_ancestor":
        shutil.copytree(sanitized, external / "sanitized")
        _junction(tmp_path / "input-parent", external)
        input_root = tmp_path / "input-parent" / "sanitized"
    elif location == "output_ancestor":
        _junction(tmp_path / "output-parent", external)
        output = tmp_path / "output-parent" / "labels"
    else:
        name = {
            "backup": ".labels.backup",
            "stage": ".labels.staging",
            "legacy": ".labels.staging.old",
        }[location]
        _junction(tmp_path / name, external)
    before = _files(external)
    code = "label_path_invalid" if location.startswith("input") else "label_output_path_invalid"
    with pytest.raises(LabelValidationError, match=f"^{code}$"):
        export_labels(input_root, output)
    assert _files(external) == before
    assert not output.exists()


def test_junction_appearing_during_input_read_is_rejected_before_comments_read(
    sanitized: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = next(sanitized.rglob("sampling-manifest.json"))
    directory = manifest_path.parent
    external = tmp_path / "external"
    external.mkdir()
    (external / "comments.jsonl").write_bytes(b"synthetic external sentinel")
    read_manifest = label_module._read_sampling_manifest

    def redirect_after_manifest(path: Path) -> SamplingManifest:
        manifest = read_manifest(path)
        directory.rename(directory.with_name("parked-input"))
        _junction(directory, external)
        return manifest

    monkeypatch.setattr(label_module, "_read_sampling_manifest", redirect_after_manifest)
    with pytest.raises(LabelValidationError, match="^label_path_invalid$"):
        export_labels(sanitized, tmp_path / "labels")
    assert _files(external) == {Path("comments.jsonl"): b"synthetic external sentinel"}
    assert not (tmp_path / ".labels.staging").exists()


@pytest.mark.parametrize("boundary", ["write", "publish"])
def test_junction_appearing_at_output_boundary_preserves_external_sentinel(
    sanitized: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    stage = tmp_path / ".labels.staging"
    parked = tmp_path / "parked-stage"
    output = tmp_path / "labels"
    external = tmp_path / "external"
    external.mkdir()
    (external / "sentinel").write_bytes(b"synthetic external manual work")
    before = _files(external)
    rename = Path.rename
    mkdir = Path.mkdir
    commit = label_module._commit_label_root
    mkdir_depth = 0

    def redirect_stage() -> None:
        rename(stage, parked)
        _junction(stage, external)

    def redirect_after_mkdir(
        path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        nonlocal mkdir_depth
        mkdir_depth += 1
        try:
            mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        finally:
            mkdir_depth -= 1
        if mkdir_depth == 0 and path.name.startswith("video_") and path.is_relative_to(stage):
            redirect_stage()

    def redirect_before_commit(staging: Path, target: Path) -> None:
        redirect_stage()
        commit(staging, target)

    if boundary == "write":
        monkeypatch.setattr(Path, "mkdir", redirect_after_mkdir)
    else:
        monkeypatch.setattr(label_module, "_commit_label_root", redirect_before_commit)
    with pytest.raises(LabelValidationError, match="^label_output_path_invalid$"):
        export_labels(sanitized, output)
    assert _files(external) == before
    assert stage.is_junction() and parked.is_dir()
    assert not output.exists()
    if boundary == "publish":
        assert len(_files(parked)) == 1


@pytest.mark.parametrize("operation", ["stage_mkdir", "annotation_mkdir", "annotation_write"])
def test_output_permission_failures_use_fixed_write_code_and_preserve_stage(
    sanitized: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    stage = tmp_path / ".labels.staging"
    mkdir = Path.mkdir
    write_text = Path.write_text

    def fail_mkdir(
        path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if (operation == "stage_mkdir" and path == stage) or (
            operation == "annotation_mkdir" and path.name.startswith("video_")
        ):
            raise PermissionError("SYNTHETIC_PRIVATE_OUTPUT_PATH")
        mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    def fail_write(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if path.name == "annotation.json":
            raise PermissionError("SYNTHETIC_PRIVATE_OUTPUT_PATH")
        return write_text(path, data, *args, **kwargs)  # type: ignore[arg-type]

    if operation == "annotation_write":
        monkeypatch.setattr(Path, "write_text", fail_write)
    else:
        monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(LabelValidationError, match="^label_output_write_failed$"):
        export_labels(sanitized, tmp_path / "labels")
    assert not (tmp_path / "labels").exists()
    assert stage.exists() == (operation != "stage_mkdir")
