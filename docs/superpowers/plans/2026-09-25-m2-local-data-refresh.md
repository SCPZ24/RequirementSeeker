# M2 Local Data Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Produce and validate a new local 24-video sanitized dataset and blank label set without changing the existing raw or old outputs.

**Architecture:** Run the merged Dataset Tools against the exact approved manifest into absent sibling directories, then validate contracts, counts, pseudonym continuity, privacy candidates, and source-linked blank labels. Keep the existing three data trees as immutable baselines, and stop without promotion whenever a gate fails.

**Tech Stack:** PowerShell 7, Python 3.12, uv, Dataset Tools CLI, Pydantic contracts, SHA-256.

---

Approved design: docs/superpowers/specs/2026-09-25-m2-local-data-refresh-design.md. Work from the existing isolated C:/Users/Fantason/.codex/worktrees/dataset-preparation/RequirementSeeker checkout on codex/m2-data-refresh. This is a local data operation: do not commit or print real text, raw IDs, pseudonymous IDs, or the HMAC value. Do not modify raw/, sanitized/, or labels/. Only scoped sanitizer code, synthetic tests, and documentation belong in Git; results go to the ignored E:/Projects/RequirementSeeker/docs/HANDOFF.md and docs/execution/2026-09-25.md. Use explicit absolute paths in the real operation, not a guessed working directory.

## File and directory map

| Responsibility | Exact location |
| --- | --- |
| Approved input list | E:/Projects/RequirementSeeker/.local-data/m2-real/candidates/approved-manifest.json |
| Immutable source | E:/Projects/RequirementSeeker/.local-data/m2-real/raw/ |
| Immutable comparison baselines | E:/Projects/RequirementSeeker/.local-data/m2-real/sanitized/ and labels/ |
| New output targets | E:/Projects/RequirementSeeker/.local-data/m2-real/sanitized-v2/ and labels-v2/ |
| Existing sanitizer and label validator | packages/dataset-tools/src/requirementseeker_dataset/sanitize.py and labels.py |
| Local-only audit and handoff | E:/Projects/RequirementSeeker/docs/execution/2026-09-25.md and docs/HANDOFF.md |

### Task 1: Freeze the preflight state

- [ ] **Step 1: Verify checkout and package baseline.** From the isolated checkout run the following, one command at a time:

~~~powershell
git status --short --branch
uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests -q
uv run --offline --locked --project packages/dataset-tools ruff check packages/dataset-tools/src packages/dataset-tools/tests
uv run --offline --locked --project packages/dataset-tools ruff format --check packages/dataset-tools/src packages/dataset-tools/tests
uv run --offline --locked --project packages/dataset-tools mypy packages/dataset-tools/src
~~~

Expected: branch codex/m2-data-refresh, no uncommitted changes, 126 tests passing, and the three static gates exit zero. Stop if any command fails.

- [ ] **Step 2: Verify path and manifest preconditions without enumerating identifiers.** Run in PowerShell 7; only the booleans and digest may be displayed:

~~~powershell
$dataRoot = 'E:\Projects\RequirementSeeker\.local-data\m2-real'
$plan = Join-Path $dataRoot 'candidates\approved-manifest.json'
$expected = '9A50A0EAA9FED72C23E2340B0A0C2B32D4E7F316A8EDC263981450E70515D542'
if ((Get-FileHash -LiteralPath $plan -Algorithm SHA256).Hash -ne $expected) { throw 'approved_manifest_hash_changed' }
foreach ($name in @('raw','sanitized','labels')) {
    if (-not (Test-Path -LiteralPath (Join-Path $dataRoot $name) -PathType Container)) { throw 'baseline_directory_missing' }
}
foreach ($name in @('sanitized-v2','labels-v2','.sanitized-v2.backup','.labels-v2.backup')) {
    if (Test-Path -LiteralPath (Join-Path $dataRoot $name)) { throw 'new_target_not_empty' }
}
$staging = @(Get-ChildItem -LiteralPath $dataRoot -Directory | Where-Object { $_.Name -like '.sanitized-v2.staging.*' -or $_.Name -like '.labels-v2.staging.*' })
if ($staging.Count) { throw 'new_target_staging_exists' }
$secretKey = Get-Item -LiteralPath 'HKCU:\Environment'
if ($secretKey.GetValueNames() -notcontains 'HMAC_SECRET_KEY') { throw 'stable_secret_missing' }
'preflight_paths_ok'
~~~

Check the baseline roots and output parent for redirects without printing any child name:

~~~powershell
foreach ($name in @('raw','sanitized','labels','.')) {
    $path = if ($name -eq '.') { $dataRoot } else { Join-Path $dataRoot $name }
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'data_path_redirect' }
}
~~~

Do not inspect or output the secret value.

- [ ] **Step 3: Check that old labels have no human work to transfer.** Run this read-only count; require 24 primary files and all other counts zero. If not, stop and redesign:

~~~powershell
$labelRoot = 'E:\Projects\RequirementSeeker\.local-data\m2-real\labels'
$files = @(Get-ChildItem -LiteralPath $labelRoot -Recurse -File -Filter annotation.json)
$editedFiles = 0
$editedComments = 0
foreach ($file in $files) {
    $a = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json
    if ($null -ne $a.annotator_id -or $a.is_complete -or $a.disputed -or @($a.clusters).Count -or @($a.dispute_reasons).Count) { $editedFiles++ }
    $editedComments += @($a.comments | Where-Object { $_.need_signal -ne 'unlabeled' -or $_.signal_kind -ne 'unlabeled' -or $null -ne $_.normalized_need -or $_.noise_kind -ne 'unlabeled' -or $_.video_reception -ne 'unlabeled' }).Count
}
$extraFiles = @(Get-ChildItem -LiteralPath $labelRoot -Recurse -File | Where-Object { $_.Name -in @('annotation-secondary.json','adjudication.json') }).Count
if ($files.Count -ne 24 -or $editedFiles -or $editedComments -or $extraFiles) { throw 'old_label_work_detected' }
[pscustomobject]@{PrimaryFiles=$files.Count; EditedFiles=$editedFiles; EditedComments=$editedComments; ExtraFiles=$extraFiles}
~~~

- [ ] **Step 4: Record baseline digests.** Run this exact function for raw/, sanitized/, and labels/; record only the three resulting digests and counts in the local execution log. Run the identical block after generation in Task 5 and require equality. Each UTF-8 record is the slash-normalized relative path without a leading separator, NUL, uppercase file SHA-256, then LF; records are sorted with ordinal string comparison. Do not display constituent paths or content:

~~~powershell
function Get-TreeDigest([string]$treePath) {
    $resolved = (Resolve-Path -LiteralPath $treePath).Path
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($resolved)
    $files = [Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
    while ($pending.Count -gt 0) {
        foreach ($child in @(Get-ChildItem -LiteralPath $pending.Pop() -Force)) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'tree_redirect' }
            if ($child.PSIsContainer) {
                $pending.Push($child.FullName)
            } elseif ($child -is [IO.FileInfo]) {
                $relative = [IO.Path]::GetRelativePath($resolved, $child.FullName).Replace('\','/')
                $files.Add($relative, $child.FullName)
            } else {
                throw 'unexpected_tree_entry'
            }
        }
    }
    $aggregate = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
    try {
        foreach ($relative in $files.Keys) {
            $digest = (Get-FileHash -LiteralPath $files[$relative] -Algorithm SHA256).Hash
            $record = [Text.Encoding]::UTF8.GetBytes($relative + [char]0 + $digest + [char]10)
            $aggregate.AppendData($record)
        }
        [pscustomobject]@{
            Count = $files.Count
            Digest = [Convert]::ToHexString($aggregate.GetHashAndReset())
        }
    } finally {
        $aggregate.Dispose()
    }
}
$dataRoot = 'E:\Projects\RequirementSeeker\.local-data\m2-real'
@('raw','sanitized','labels') | ForEach-Object {
    $result = Get-TreeDigest (Join-Path $dataRoot $_)
    [pscustomobject]@{Tree=$_;Count=$result.Count;Digest=$result.Digest}
}
~~~

### Task 2: Generate a new sanitized tree exactly once

- [ ] **Step 1: Recheck that sanitized-v2/ and its backup/staging do not exist.** Reuse Task 1's exact absent-target check immediately before generation. If the target appeared, stop. The `--create-only` commit also rejects a target that appears after this check.

- [ ] **Step 2: Run the CLI with the stable secret scoped to one PowerShell process.** Read the registry value in memory without printing it and remove the process environment variable afterward. Do not redirect a command transcript to a file.

~~~powershell
$dataRoot = 'E:\Projects\RequirementSeeker\.local-data\m2-real'
$secret = (Get-Item -LiteralPath 'HKCU:\Environment').GetValue('HMAC_SECRET_KEY')
if ([Text.Encoding]::UTF8.GetByteCount([string]$secret) -lt 32) { throw 'stable_secret_too_short' }
try {
    $env:HMAC_SECRET_KEY = [string]$secret
    uv run --offline --locked --project packages/dataset-tools rs-dataset sanitize --raw (Join-Path $dataRoot 'raw') --plan (Join-Path $dataRoot 'candidates\approved-manifest.json') --output (Join-Path $dataRoot 'sanitized-v2') --secret-env HMAC_SECRET_KEY --create-only
    if ($LASTEXITCODE -ne 0) { throw 'sanitize_failed' }
} finally {
    Remove-Item Env:\HMAC_SECRET_KEY -ErrorAction SilentlyContinue
    $secret = $null
}
~~~

Expected CLI summary: status ok, 24 sampling manifests, 24 sanitization reports, two excluded raw directories; do not infer readiness from this summary alone. This create-only mode requires Windows; on other platforms it fails before processing because an atomic no-replace directory publish has not been established. A failure leaves the old baseline untouched; inspect any new target/staging safely and do not rerun over it.

### Task 3: Audit the sanitized tree before exporting labels

- [ ] **Step 1: Run the read-only aggregate, contract, pseudonym, and bounded leak audit.** In PowerShell 7 from the repository root, pipe the following script to the Dataset Tools interpreter. It catches parser errors without printing record contents. It must output only a safe JSON result; require status ok, 24 manifests, 5,090 comments, and zero leaks/residual recognized patterns:

~~~powershell
@'
import json
import winreg
from pathlib import Path
from requirementseeker_dataset.contracts import (
    DatasetSplit, SamplingManifest, SanitizationReport, SanitizedComment, SanitizedVideo,
)
from requirementseeker_dataset.source import read_raw_video
from requirementseeker_dataset.text import sanitize_text

root = Path(r"E:\Projects\RequirementSeeker\.local-data\m2-real")
new = root / "sanitized-v2"
old = root / "sanitized"
stage = "start"

def require(ok: bool, code: str) -> None:
    if not ok:
        raise RuntimeError(code)

try:
    stage = "approved_plan"
    approved = json.loads((root / "candidates" / "approved-manifest.json").read_text(encoding="utf-8"))
    require(len(approved["videos"]) == 24, "approved_count")
    stage = "manifest"
    manifests = sorted(new.rglob("sampling-manifest.json"))
    require(len(manifests) == 24, "manifest_count")
    comment_total = 0
    residual = 0
    for path in manifests:
        relative = path.relative_to(new)
        require(len(relative.parts) == 3, "manifest_layout")
        current = SamplingManifest.model_validate_json(path.read_bytes())
        prior = SamplingManifest.model_validate_json((old / relative).read_bytes())
        require(current.sampling_schema_version == "1.1", "schema_version")
        require(current.exact_duplicate_count is None, "historical_duplicate_count")
        require(current.video_id == prior.video_id, "video_pseudonym_changed")
        require(current.candidate_comment_ids == prior.candidate_comment_ids, "comment_pseudonym_changed")
        require(relative.parts[:2] == (current.platform, current.video_id), "manifest_layout")
        report = SanitizationReport.model_validate_json((path.parent / "sanitization.json").read_bytes())
        require(report.rules_version == "pii-v2" and not report.excluded, "sanitization_report")
        video = SanitizedVideo.model_validate_json((path.parent / "video.json").read_bytes())
        require(video.video_id == current.video_id, "video_artifact")
        residual += int(sanitize_text(video.title).text != video.title)
        residual += int(sanitize_text(video.description).text != video.description)
        lines = (path.parent / "comments.jsonl").read_text(encoding="utf-8").splitlines()
        comments = [SanitizedComment.model_validate_json(line) for line in lines]
        require([item.comment_id for item in comments] == current.candidate_comment_ids, "comment_order")
        comment_total += len(comments)
        residual += sum(sanitize_text(item.text).text != item.text for item in comments)
    require(comment_total == 5090, "comment_total")
    require(residual == 0, "recognized_privacy_pattern")
    stage = "split_and_replacements"
    split = DatasetSplit.model_validate_json((new / "dataset-split.json").read_bytes())
    require((len(split.development), len(split.calibration), len(split.holdout)) == (10, 7, 7), "split")
    replacements = json.loads((new / "replacement-candidates.json").read_text(encoding="utf-8"))
    require(replacements["count"] == 0, "replacement_count")
    stage = "raw_identifier_scan"
    raw_ids = set()
    raw_total = 0
    for entry in approved["videos"]:
        bundle = read_raw_video(root / "raw" / entry["platform"] / entry["video_key"])
        require(bundle.collection.collection_schema_version is None and bundle.collection.exact_duplicate_count is None, "legacy_provenance")
        raw_total += len(bundle.comments)
        raw_ids.update((bundle.video.raw_video_id, bundle.video.raw_author_id))
        for item in bundle.comments:
            raw_ids.update((item.raw_comment_id, item.raw_author_id, item.raw_parent_comment_id))
        raw_ids.update(error.raw_comment_id for error in bundle.collection.collection_errors)
    require(raw_total == 5090, "raw_comment_total")
    output = "\n".join(path.read_text(encoding="utf-8") for path in new.rglob("*") if path.is_file())
    tokens = {json.dumps(value, ensure_ascii=mode) for value in raw_ids if value for mode in (False, True)}
    raw_leaks = sum(token in output for token in tokens)
    require(raw_leaks == 0, "raw_identifier_leak")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        secret = str(winreg.QueryValueEx(key, "HMAC_SECRET_KEY")[0])
    require(secret not in output, "secret_leak")
    print(json.dumps({"status": "ok", "manifests": len(manifests), "comments": comment_total,
                      "split": [len(split.development), len(split.calibration), len(split.holdout)],
                      "residual_patterns": residual, "raw_id_leaks": raw_leaks, "secret_leaks": 0}))
except Exception:
    print(json.dumps({"status": "error", "stage": stage}))
    raise SystemExit(2)
'@ | uv run --offline --locked --project packages/dataset-tools python -
if ($LASTEXITCODE -ne 0) { throw 'sanitized_audit_failed' }
~~~

This uses exact JSON string tokens rather than arbitrary substrings for short raw IDs; the previous substring check produced a false alarm. A zero result is a bounded audit, not proof against unknown PII patterns. The in-memory key and identifiers are never printed.

- [ ] **Step 2: Review the aggregate result before proceeding.** If any count, contract, pseudonym, rule-version, or leakage check fails, leave sanitized-v2/ in place for inspection and do not export labels. Do not silently retry or mutate the old sanitized/ tree.

### Task 4: Create and validate blank labels

- [ ] **Step 1: Recheck labels-v2/ and its backup/staging are absent.** If any exists, stop; do not overwrite.

- [ ] **Step 2: Export once and validate against the new source.**

~~~powershell
$dataRoot = 'E:\Projects\RequirementSeeker\.local-data\m2-real'
uv run --offline --locked --project packages/dataset-tools rs-dataset export-labels --sanitized (Join-Path $dataRoot 'sanitized-v2') --output (Join-Path $dataRoot 'labels-v2')
if ($LASTEXITCODE -ne 0) { throw 'export_labels_failed' }
uv run --offline --locked --project packages/dataset-tools rs-dataset validate-labels (Join-Path $dataRoot 'labels-v2') --sanitized (Join-Path $dataRoot 'sanitized-v2')
if ($LASTEXITCODE -ne 0) { throw 'validate_labels_failed' }
~~~

Expected: 24 annotations, 5,090 comments, zero evaluation-eligible files. Independently parse all 24 files with this read-only aggregate check. Require no edited file, no filled comment, and no secondary/adjudication file; stop on any discrepancy and leave the new directory for inspection:

~~~powershell
$root = 'E:\Projects\RequirementSeeker\.local-data\m2-real\labels-v2'
$files = @(Get-ChildItem -LiteralPath $root -Recurse -File -Filter annotation.json)
$comments = 0
$edited = 0
foreach ($file in $files) {
    $a = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json
    if ($a.is_complete -or $null -ne $a.annotator_id -or $a.disputed -or @($a.clusters).Count -or @($a.dispute_reasons).Count) { $edited++ }
    $comments += @($a.comments).Count
    $edited += @($a.comments | Where-Object { $_.need_signal -ne 'unlabeled' -or $_.signal_kind -ne 'unlabeled' -or $null -ne $_.normalized_need -or $_.noise_kind -ne 'unlabeled' -or $_.video_reception -ne 'unlabeled' }).Count
}
$extra = @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object { $_.Name -in @('annotation-secondary.json','adjudication.json') }).Count
if ($files.Count -ne 24 -or $comments -ne 5090 -or $edited -or $extra) { throw 'new_labels_not_blank' }
[pscustomobject]@{Annotations=$files.Count;Comments=$comments;Edited=$edited;ExtraFiles=$extra}
~~~

### Task 5: Confirm immutability and hand off

- [ ] **Step 1: Recompute the raw/, sanitized/, and labels/ tree digests with the exact Task 1 algorithm.** Require all three digests and file counts equal the recorded baselines. Check that no unexpected v2 staging/backup exists. If any change is found, stop and investigate; do not declare readiness or remove anything.

- [ ] **Step 2: Inspect Git state.** Run git status --short --branch in the isolated checkout and root repository. Require no real data staged or tracked. Do not add ignored data to Git. Only scoped sanitizer code, synthetic tests, and documentation may be new tracked content for this stage.

- [ ] **Step 3: Update ignored handoff and daily log.** Record the code revision, manifest hash, new output names, aggregate counts, old/new tree-digest comparison result, privacy-check categories, command gate outcomes, and any limitation. Use apply_patch; never record raw text, IDs, key values, or full candidate paths. Mark sanitized-v2/ and labels-v2/ as the human-labeling materials only if every check above passed. Keep old directories and worktrees intact.

- [ ] **Step 4: Stop at the human-labeling gate.** Do not fill labels automatically, run a real model, claim semantic metrics, or start a 12/24-video evaluation. Present the verified blank materials and the human annotation next step to the user.
