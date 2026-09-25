import json
import shutil
from pathlib import Path

import pytest

import requirementseeker_dataset.labels as label_module
from requirementseeker_dataset.contracts import (
    AdjudicationFile,
    AnnotationFile,
    ClusterLabel,
)
from requirementseeker_dataset.labels import (
    LabelExportResult,
    LabelValidationError,
    export_labels,
    validate_label_root,
    validate_labels,
)
from requirementseeker_dataset.sanitize import sanitize_root

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "local-test-secret-at-least-32-bytes"


def _export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LabelExportResult:
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw" / "valid", raw)
    sanitized = tmp_path / "sanitized"
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", SECRET)
    sanitize_root(
        raw,
        FIXTURES / "approved-manifest.json",
        sanitized,
        "RS_DATASET_TEST_SECRET",
    )
    return export_labels(sanitized, tmp_path / "labels")


def _completed(annotation: AnnotationFile, annotator: str) -> AnnotationFile:
    comments = [
        item.model_copy(
            update={
                "need_signal": "yes",
                "signal_kind": "need",
                "normalized_need": "支持离线处理",
                "noise_kind": "none",
                "video_reception": "positive",
            }
        )
        for item in annotation.comments
    ]
    return annotation.model_copy(
        update={"annotator_id": annotator, "comments": comments, "is_complete": True}
    )


def _write_annotation(path: Path, annotation: AnnotationFile) -> None:
    path.write_text(annotation.model_dump_json(), encoding="utf-8")


def test_root_eligibility_requires_matching_sanitized_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)
    _write_annotation(result.output_files[0], _completed(result.annotations[0], "annotator-one"))
    (tmp_path / "labels" / "README.md").write_text("notes", encoding="utf-8")
    (tmp_path / "sanitized" / "README.md").write_text("notes", encoding="utf-8")
    report_only = tmp_path / "sanitized" / "douyin" / f"video_{'f' * 32}"
    report_only.mkdir(parents=True)
    (report_only / "sanitization.json").write_text("{}", encoding="utf-8")

    validation = validate_label_root(tmp_path / "labels", tmp_path / "sanitized")

    assert validation.annotation_count == 1
    assert validation.evaluation_eligible_count == 1


@pytest.mark.parametrize(
    "mutation", ["empty", "removed", "duplicated", "added", "changed", "reordered"]
)
def test_root_rejects_annotation_comment_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    result = _export(tmp_path, monkeypatch)
    annotation = _completed(result.annotations[0], "annotator-one")
    comments = list(annotation.comments)
    if mutation == "empty":
        comments = []
    elif mutation == "removed":
        comments.pop()
    elif mutation == "duplicated":
        comments.append(comments[0])
    elif mutation == "added":
        comments.append(comments[0].model_copy(update={"comment_id": f"comment_{'f' * 32}"}))
    elif mutation == "changed":
        comments[0] = comments[0].model_copy(update={"text": "changed private text"})
    else:
        comments.reverse()
    _write_annotation(result.output_files[0], annotation.model_copy(update={"comments": comments}))

    with pytest.raises(LabelValidationError) as error:
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")

    expected = {
        "empty": "incomplete_required_fields",
        "duplicated": "duplicate_comment",
    }.get(mutation, "label_source_comment_mismatch")
    assert str(error.value) == expected
    assert "private text" not in str(error.value)


def test_root_rejects_missing_or_extra_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)
    path = result.output_files[0]
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(LabelValidationError, match="annotation_files_missing"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")

    path.write_bytes(original)
    extra = tmp_path / "labels" / "douyin" / f"video_{'f' * 32}" / "annotation.json"
    extra.parent.mkdir(parents=True)
    extra.write_bytes(original)
    with pytest.raises(LabelValidationError, match="label_source_set_mismatch"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


def test_root_rejects_missing_video_from_two_video_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _export(tmp_path, monkeypatch)
    original = next((tmp_path / "sanitized").rglob("sampling-manifest.json"))
    second_id = f"video_{'f' * 32}"
    second = original.parent.with_name(second_id)
    shutil.copytree(original.parent, second)
    manifest = json.loads((second / "sampling-manifest.json").read_text(encoding="utf-8"))
    manifest["video_id"] = second_id
    (second / "sampling-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LabelValidationError, match="label_source_set_mismatch"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


@pytest.mark.parametrize("artifact", ["annotation-secondary.json", "adjudication.json"])
def test_root_rejects_orphan_label_video_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    result = _export(tmp_path, monkeypatch)
    _write_annotation(result.output_files[0], _completed(result.annotations[0], "annotator-one"))
    assert (
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized").evaluation_eligible_count
        == 1
    )
    orphan = tmp_path / "labels" / "douyin" / f"video_{'f' * 32}" / artifact
    orphan.parent.mkdir(parents=True)
    orphan.write_text("{}", encoding="utf-8")

    with pytest.raises(LabelValidationError, match="label_source_set_mismatch"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


@pytest.mark.parametrize("artifact", ["annotation-secondary.json", "adjudication.json"])
def test_root_rejects_reserved_label_artifact_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    result = _export(tmp_path, monkeypatch)
    _write_annotation(result.output_files[0], _completed(result.annotations[0], "annotator-one"))
    assert (
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized").evaluation_eligible_count
        == 1
    )
    result.output_files[0].with_name(artifact).mkdir()

    with pytest.raises(LabelValidationError, match="label_source_set_mismatch"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


def test_root_rejects_orphan_sanitized_video_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)
    _write_annotation(result.output_files[0], _completed(result.annotations[0], "annotator-one"))
    assert (
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized").evaluation_eligible_count
        == 1
    )
    orphan = tmp_path / "sanitized" / "douyin" / f"video_{'f' * 32}" / "comments.jsonl"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("", encoding="utf-8")

    with pytest.raises(LabelValidationError, match="label_source_set_mismatch"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


@pytest.mark.parametrize("artifact", ["video.json", "collection.json"])
def test_root_rejects_orphan_included_video_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    result = _export(tmp_path, monkeypatch)
    _write_annotation(result.output_files[0], _completed(result.annotations[0], "annotator-one"))
    assert (
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized").evaluation_eligible_count
        == 1
    )
    orphan = tmp_path / "sanitized" / "douyin" / f"video_{'f' * 32}" / artifact
    orphan.parent.mkdir(parents=True)
    orphan.write_text("{}", encoding="utf-8")

    with pytest.raises(LabelValidationError, match="label_source_set_mismatch"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


def test_root_rejects_empty_sanitized_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _export(tmp_path, monkeypatch)
    comments = next((tmp_path / "sanitized").rglob("comments.jsonl"))
    comments.write_text("", encoding="utf-8")

    with pytest.raises(LabelValidationError, match="label_source_empty"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


@pytest.mark.parametrize("root_name", ["labels", "sanitized"])
def test_root_rejects_redirected_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_name: str
) -> None:
    _export(tmp_path, monkeypatch)
    alias = tmp_path / "alias"
    alias.mkdir()
    shutil.copytree(tmp_path / "labels", alias / "labels")
    shutil.copytree(tmp_path / "sanitized", alias / "sanitized")
    is_redirect = label_module._is_path_redirect
    monkeypatch.setattr(
        label_module, "_is_path_redirect", lambda path: path == alias or is_redirect(path)
    )
    label_root = alias / "labels" if root_name == "labels" else tmp_path / "labels"
    sanitized_root = alias / "sanitized" if root_name == "sanitized" else tmp_path / "sanitized"

    with pytest.raises(LabelValidationError, match="label_path_invalid"):
        validate_label_root(label_root, sanitized_root)


@pytest.mark.parametrize("root_name", ["labels", "sanitized"])
def test_root_rejects_unlisted_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_name: str
) -> None:
    _export(tmp_path, monkeypatch)
    redirect = tmp_path / root_name / "unlisted"
    redirect.mkdir()
    is_redirect = label_module._is_path_redirect
    monkeypatch.setattr(
        label_module, "_is_path_redirect", lambda path: path == redirect or is_redirect(path)
    )

    with pytest.raises(LabelValidationError, match="label_path_invalid"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


def test_root_rejects_source_text_from_another_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)
    _write_annotation(result.output_files[0], _completed(result.annotations[0], "annotator-one"))
    comments = next((tmp_path / "sanitized").rglob("comments.jsonl"))
    records = [json.loads(line) for line in comments.read_text(encoding="utf-8").splitlines()]
    records[0]["text"] = "different sanitized revision"
    comments.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )

    with pytest.raises(LabelValidationError, match="label_source_comment_mismatch"):
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")


def test_export_contains_text_but_no_semantic_prefill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)

    assert all(item.text and item.need_signal == "unlabeled" for item in result.comments)
    assert result.clusters == []
    assert len(result.annotations) == len(result.output_files) == 1
    rendered = result.output_files[0].read_text(encoding="utf-8")
    assert "BVfake" not in rendered
    assert "comment-1" not in rendered


def test_export_preserves_existing_manual_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)
    annotation_path = result.output_files[0]
    annotation_path.write_text("manual work", encoding="utf-8")

    with pytest.raises(LabelValidationError, match="label_output_already_exists"):
        export_labels(tmp_path / "sanitized", tmp_path / "labels")

    assert annotation_path.read_text(encoding="utf-8") == "manual work"


def test_interrupted_label_publish_restores_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)
    output = tmp_path / "labels"
    backup = output.with_name(".labels.backup")
    output.replace(backup)
    stale = output.with_name(".labels.staging")
    stale.mkdir()
    (stale / "incomplete.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(LabelValidationError, match="label_output_already_exists"):
        export_labels(tmp_path / "sanitized", output)

    assert result.output_files[0].is_file()
    assert not backup.exists()
    assert (stale / "incomplete.txt").read_text(encoding="utf-8") == "keep"


def test_stale_label_staging_does_not_block_new_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw" / "valid", raw)
    sanitized = tmp_path / "sanitized"
    monkeypatch.setenv("RS_DATASET_TEST_SECRET", SECRET)
    sanitize_root(raw, FIXTURES / "approved-manifest.json", sanitized, "RS_DATASET_TEST_SECRET")
    stale = tmp_path / ".labels.staging"
    stale.mkdir()
    (stale / "marker.txt").write_text("keep", encoding="utf-8")

    result = export_labels(sanitized, tmp_path / "labels")

    assert result.output_files[0].is_file()
    assert (stale / "marker.txt").read_text(encoding="utf-8") == "keep"


def test_duplicate_comments_and_cross_video_clusters_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    annotation = _export(tmp_path, monkeypatch).annotations[0]
    duplicate = annotation.model_copy(update={"comments": [annotation.comments[0]] * 2})
    foreign = f"comment_{'f' * 32}"
    cross_video = annotation.model_copy(
        update={
            "clusters": [
                ClusterLabel(
                    cluster_id="cluster-one",
                    normalized_need="离线处理",
                    comment_ids=[foreign],
                )
            ]
        }
    )

    with pytest.raises(LabelValidationError, match="duplicate_comment"):
        validate_labels(duplicate)
    with pytest.raises(LabelValidationError, match="cross_video_cluster_member"):
        validate_labels(cross_video)


def test_duplicate_cluster_membership_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    annotation = _export(tmp_path, monkeypatch).annotations[0]
    comment_id = annotation.comments[0].comment_id
    clusters = [
        ClusterLabel(cluster_id="cluster-one", normalized_need="离线", comment_ids=[comment_id]),
        ClusterLabel(cluster_id="cluster-two", normalized_need="本地", comment_ids=[comment_id]),
    ]

    with pytest.raises(LabelValidationError, match="duplicate_cluster_membership"):
        validate_labels(annotation.model_copy(update={"clusters": clusters}))


def test_claimed_complete_annotation_cannot_keep_unlabeled_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    annotation = (
        _export(tmp_path, monkeypatch)
        .annotations[0]
        .model_copy(update={"annotator_id": "annotator-one", "is_complete": True})
    )

    with pytest.raises(LabelValidationError, match="incomplete_required_fields"):
        validate_labels(annotation)


def test_dispute_requires_two_independent_annotations_and_adjudication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = _export(tmp_path, monkeypatch)
    blank = exported.annotations[0]
    first = _completed(blank, "annotator-one").model_copy(
        update={"disputed": True, "dispute_reasons": ["need_signal_disagreement"]}
    )
    second = _completed(blank, "annotator-two")

    with pytest.raises(LabelValidationError, match="dispute_not_adjudicated"):
        validate_labels(first)

    decision = _completed(blank, "adjudicator-one")
    adjudication = AdjudicationFile(
        adjudication_schema_version="1.0",
        video_id=blank.video_id,
        annotator_ids=["annotator-one", "annotator-two"],
        adjudicator_id="adjudicator-one",
        decision=decision,
    )

    assert (
        validate_labels(first, second=second, adjudication=adjudication).evaluation_eligible
        is False
    )
    _write_annotation(exported.output_files[0], first)
    _write_annotation(exported.output_files[0].with_name("annotation-secondary.json"), second)
    exported.output_files[0].with_name("adjudication.json").write_text(
        adjudication.model_dump_json(), encoding="utf-8"
    )
    assert (
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized").evaluation_eligible_count
        == 1
    )


@pytest.mark.parametrize("changed", ["secondary", "adjudication"])
def test_root_checks_dispute_sources_against_sanitized_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    result = _export(tmp_path, monkeypatch)
    blank = result.annotations[0]
    first = _completed(blank, "annotator-one").model_copy(
        update={"disputed": True, "dispute_reasons": ["need_signal_disagreement"]}
    )
    second = _completed(blank, "annotator-two")
    decision = _completed(blank, "adjudicator-one")
    altered = decision.comments[0].model_copy(update={"text": "different private text"})
    if changed == "secondary":
        second = second.model_copy(update={"comments": [altered, *second.comments[1:]]})
    else:
        decision = decision.model_copy(update={"comments": [altered, *decision.comments[1:]]})
    adjudication = AdjudicationFile(
        adjudication_schema_version="1.0",
        video_id=blank.video_id,
        annotator_ids=["annotator-one", "annotator-two"],
        adjudicator_id="adjudicator-one",
        decision=decision,
    )
    _write_annotation(result.output_files[0], first)
    _write_annotation(result.output_files[0].with_name("annotation-secondary.json"), second)
    result.output_files[0].with_name("adjudication.json").write_text(
        adjudication.model_dump_json(), encoding="utf-8"
    )

    with pytest.raises(LabelValidationError) as error:
        validate_label_root(tmp_path / "labels", tmp_path / "sanitized")

    expected = (
        "independent_annotation_source_mismatch"
        if changed == "secondary"
        else "adjudication_decision_source_mismatch"
    )
    assert str(error.value) == expected
    assert "private text" not in str(error.value)


def test_raw_identifier_like_metadata_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    annotation = _export(tmp_path, monkeypatch).annotations[0]
    cluster = ClusterLabel(
        cluster_id="raw-comment-123",
        normalized_need="离线处理",
        comment_ids=[annotation.comments[0].comment_id],
    )

    with pytest.raises(LabelValidationError, match="raw_identifier_pattern"):
        validate_labels(annotation.model_copy(update={"clusters": [cluster]}))


def test_label_output_must_not_replace_sanitized_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _export(tmp_path, monkeypatch)
    sanitized = tmp_path / "sanitized"

    with pytest.raises(LabelValidationError, match="label_output_must_be_separate"):
        export_labels(sanitized, sanitized)
    assert result.output_files[0].is_file()
