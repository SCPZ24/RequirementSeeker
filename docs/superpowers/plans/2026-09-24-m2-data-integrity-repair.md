# M2 Data Integrity Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the three PR #6 review blockers without losing the 24 historical videos or pretending unknown duplicate counts are zero.

**Architecture:** Collector persists a versioned, provenance-aware duplicate count; Dataset carries known or unknown count into a versioned sampling manifest; Agent treats unknown provenance as degraded at best. Dataset also redacts explicit private handles and requires sanitized-source equality before labels can become evaluation-eligible. Keep the three Python packages independent and exchange only validated JSON.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, Ruff, strict mypy, uv, PowerShell 7.

---

Work only in the existing `codex/dataset-preparation-pr` worktree. Use synthetic fixtures; do not modify `.local-data/`, real labels, secrets, or the 24 source videos. The approved design is `docs/superpowers/specs/2026-09-24-m2-data-integrity-repair-design.md`. Run commands from the repository root with `pwsh`; package-relative `--project` paths below are intentional. For every task, observe the new regression fail before changing implementation, then commit only the named files. Existing test fixtures that represent trusted *new* manifests must be explicitly upgraded to `1.1`; retain separate `1.0` compatibility fixtures.

## File map

| Responsibility | Files |
| --- | --- |
| Raw record schema and pilot accounting | `packages/collector/src/requirementseeker_collector/contracts.py`, `runner.py`; `packages/collector/tests/contract/test_contracts.py`, `tests/test_runner.py` |
| Strict raw input and sampling output | `packages/dataset-tools/src/requirementseeker_dataset/source.py`, `contracts.py`, `sanitize.py`; `packages/dataset-tools/tests/test_source.py`, `test_contracts.py`, `test_sanitize.py` |
| Agent consumption and quality | `packages/agent/src/requirementseeker_agent/contracts/sampling.py`, `sampling/policy.py`; `packages/agent/tests/contract/test_sampling.py`, `sampling/test_policy.py`, `pipeline/test_m2.py` |
| Privacy rules | `packages/dataset-tools/src/requirementseeker_dataset/text.py`, `sanitize.py`; `packages/dataset-tools/tests/test_text.py`, `test_sanitize.py` |
| Label provenance and CLI | `packages/dataset-tools/src/requirementseeker_dataset/labels.py`, `cli.py`, `README.md`; `packages/dataset-tools/tests/test_labels.py`, `test_cli.py` |
| Cross-package checks and handoff | Tests above, `docs/HANDOFF.md`, `docs/execution/2026-09-24.md`, `docs/execution/README.md` (handoff files are local-only if ignored) |

### Task 1: Persist Collector provenance and count only accepted repeated IDs

- [ ] **Step 1: Write failing contract and pilot tests.** In `packages/collector/tests/contract/test_contracts.py`, use the existing valid `CollectionRecord` fixture/payload to assert: absent version/count parses as `(None, None)`; version `1.1` accepts count `0` and `None`; a count without version and an unknown version fail. In `packages/collector/tests/test_runner.py`, add a test using the existing `request`, `browser_result`, `comment`, and `validate_generation` helpers:

```python
def test_pilot_persists_accepted_duplicate_observations(tmp_path: Path) -> None:
    first = browser_result(comments=[comment("c1"), comment("c1", rank=2), comment("c2")])
    assert run_pilot(request(), first, output_root=tmp_path).status == "success"
    target = tmp_path / "raw" / "bilibili" / "BVfake"
    _, _, initial = validate_generation(target)
    assert (initial.collection_schema_version, initial.exact_duplicate_count) == ("1.1", 1)

    second = browser_result(comments=[comment("c1"), comment("c3")])
    assert run_pilot(request(), second, output_root=tmp_path).status == "success"
    _, _, accumulated = validate_generation(target)
    assert accumulated.exact_duplicate_count == 2
```

Also test an ID rejected by target selection does not add to the count; a merge-conflicting ID does not count; and a legacy three-file generation followed by a new run stays unknown. Construct the legacy case by removing only the two new keys from the synthetic `collection.json` before the second run; do not use production data. Check existing target-selection behavior before fixing test expectations.

- [ ] **Step 2: Verify RED.** Run `uv run --offline --locked --project packages/collector pytest packages/collector/tests/contract/test_contracts.py packages/collector/tests/test_runner.py -q`. Expected: new assertions fail on missing fields/count; pre-existing tests remain green.

- [ ] **Step 3: Implement the minimal schema.** Add to `CollectionRecord` in `contracts.py`:

```python
collection_schema_version: Literal["1.1"] | None = None
exact_duplicate_count: NonNegativeInt | None = None
```

Extend its existing `integrity` validator with `if self.collection_schema_version is None and self.exact_duplicate_count is not None: raise ValueError("duplicate_count_without_version")`. The absent pair is legacy; `1.1/null` explicitly means unknown. Ensure both `run_pilot` and `collect_from_page` write `collection_schema_version="1.1"`; only `run_pilot` may write a known number.

- [ ] **Step 4: Implement pilot accounting without changing merge or selection.** Preserve the third value from `validate_generation(target)` as `previous_collection`. Immediately after `merge_comments(previous, selected)`, calculate:

```python
conflict_ids = {item.raw_comment_id for item in merged.conflicts}
accepted_ids = {item.raw_comment_id for item in selected} - conflict_ids
observations = sum(
    item.raw_comment_id in accepted_ids for item in browser_result.comments
)
previous_ids = {item.raw_comment_id for item in previous}
new_duplicates = observations - len(accepted_ids) + len(accepted_ids & previous_ids)
exact_duplicate_count = (
    new_duplicates
    if previous_collection is None
    else None
    if previous_collection.exact_duplicate_count is None
    else previous_collection.exact_duplicate_count + new_duplicates
)
```

Initialize `previous_collection: CollectionRecord | None = None` beside `previous`. Set the value in the `CollectionRecord(...)` constructor, with version `1.1`. This counts repeated stable IDs even when metadata differs, excludes selection-dropped IDs and identity conflicts, and retains unknown historical provenance. For `collect_from_page`, set version `1.1`, count `None`.

- [ ] **Step 5: Verify GREEN and commit.** Run `uv run --offline --locked --project packages/collector pytest packages/collector/tests/contract/test_contracts.py packages/collector/tests/test_runner.py -q`; expected: all pass. Run `uv run --offline --locked --project packages/collector ruff check packages/collector/src packages/collector/tests` and `uv run --offline --locked --project packages/collector mypy packages/collector/src`; expected: exit 0. Commit the four named files with `git commit -m "fix(collector): persist accepted duplicate provenance"`.

### Task 2: Carry known/unknown counts through Dataset SamplingManifest 1.1

- [ ] **Step 1: Write failing tests.** In `test_source.py`, assert a legacy `collection.json` parses with no count and a `1.1` file parses with `0` or positive count; reject a count with missing version. In `test_sanitize.py`, extend `test_sanitize_emits_m2_sampling_manifest`: legacy fixture yields version `1.1`, `exact_duplicate_count is None`, and `collected_total == len(candidate_comment_ids)`. Copy the synthetic raw fixture and set `collection_schema_version="1.1", exact_duplicate_count=2`; assert the output count is `2` and collected total is unique count plus `2`. In `test_contracts.py`, cover `1.0` integer compatibility and `1.1` nullable count, but reject `1.0/null`.

- [ ] **Step 2: Verify RED.** Run `uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests/test_source.py packages/dataset-tools/tests/test_contracts.py packages/dataset-tools/tests/test_sanitize.py -q`; expected: new tests fail because `1.1` is not accepted and history is reported as zero.

- [ ] **Step 3: Update strict input and output contracts.** In `CollectionInput`, add the two fields and `duplicate_count_without_version` validator from Task 1. In Dataset `SamplingManifest`, use:

```python
sampling_schema_version: Literal["1.0", "1.1"]
exact_duplicate_count: NonNegativeInt | None
```

In `integrity`, reject `1.0/null`; calculate `known_exact = self.exact_duplicate_count or 0` only for the structural bound `known_exact + normalized_duplicate_count <= collected_total`. Never serialize an unknown count as `0`.

- [ ] **Step 4: Replace the invented count source.** In `_sampling_manifest`, remove the scan of `collection_errors` for `exact_duplicate_merged` and set:

```python
exact_duplicates = bundle.collection.exact_duplicate_count
known_exact = exact_duplicates if exact_duplicates is not None else 0
```

Build `SamplingManifest(sampling_schema_version="1.1", collected_total=len(comments) + known_exact, exact_duplicate_count=exact_duplicates, ...)`. Leave candidate IDs, strata, author counts and normalized duplicates based on sanitized unique comments.

- [ ] **Step 5: Verify GREEN and commit.** Run the three focused pytest files, Ruff and strict mypy for `packages/dataset-tools`; expected: exit 0. Commit the six named source/test files with `git commit -m "fix(dataset): preserve duplicate-count uncertainty"`.

### Task 3: Prevent Agent from rating unknown provenance usable

- [ ] **Step 1: Write failing tests.** Change trusted synthetic builders in `packages/agent/tests/sampling/test_policy.py` and `pipeline/test_m2.py` to emit `1.1` with known `0`; retain or add explicit legacy `1.0` tests. Add:

```python
def test_unknown_duplicate_provenance_is_never_usable() -> None:
    trusted = make_manifest(make_comments(100))
    for version, exact in [("1.0", 0), ("1.1", None)]:
        candidate = trusted.model_copy(
            update={"sampling_schema_version": version, "exact_duplicate_count": exact}
        )
        quality = assess_quality(candidate)
        assert quality.status == "degraded"
        assert quality.duplicate_rate is None
    assert assess_quality(trusted).status == "usable"
```

In `tests/contract/test_sampling.py`, assert `1.1/null` and `1.0/int` parse, `1.0/null` fails, and impossible known counts still fail.

- [ ] **Step 2: Verify RED.** Run `uv run --offline --locked --project packages/agent pytest packages/agent/tests/contract/test_sampling.py packages/agent/tests/sampling/test_policy.py packages/agent/tests/pipeline/test_m2.py -q`; expected: new contract/quality assertions fail.

- [ ] **Step 3: Update Agent contract and policy.** Match Dataset's `Literal["1.0", "1.1"]` and nullable count, with the same structural `known_exact` bound and rejection of `1.0/null`. Change `SamplingQuality.duplicate_rate` to `float | None`. In `assess_quality`, calculate:

```python
trusted = (
    manifest.sampling_schema_version == "1.1"
    and manifest.exact_duplicate_count is not None
)
duplicate_rate = (
    (manifest.exact_duplicate_count + manifest.normalized_duplicate_count)
    / max(manifest.collected_total, 1)
    if trusted and manifest.exact_duplicate_count is not None
    else None
)
```

Require `duplicate_rate is not None` in the `usable` branch before its `<= 0.25` comparison. Preserve existing degraded/insufficient thresholds and `plan_sampling` behavior.

- [ ] **Step 4: Verify GREEN and commit.** Run the focused tests, Ruff and strict mypy for `packages/agent`; expected: exit 0. Commit the five named files with `git commit -m "fix(agent): degrade unknown duplicate provenance"`.

### Task 4: Redact explicit private handles and version the rule

- [ ] **Step 1: Write failing privacy tests.** Add parametrized cases to `packages/dataset-tools/tests/test_text.py` for `微信号：abcde12345`, `QQ号：123456789`, `联系 @private_123`, and an email beside an `@handle`; assert the explicit values never occur in `TextResult.text`, the handle count is correct, and email is still `[EMAIL]`. Add a synthetic sanitization test to `test_sanitize.py` that puts such a handle in a comment, then checks `comments.jsonl`, `sanitization-report.json` and exported blank `annotation.json` for absence of the value; assert report rules version is `pii-v2`.

- [ ] **Step 2: Verify RED.** Run `uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests/test_text.py packages/dataset-tools/tests/test_sanitize.py -q`; expected: explicit-handle assertions fail.

- [ ] **Step 3: Make the narrow regex change.** Replace `PRIVATE_HANDLE_PATTERN` in `text.py` with this pattern, retaining the existing `RULES` order (email before handle):

```python
PRIVATE_HANDLE_PATTERN = re.compile(
    r"(?i)(?:"
    r"(?<![a-z0-9_])(?:微信号?|qq号?|wx|vx|v信|私信)[:：\s]*[a-z0-9_-]{5,32}(?![a-z0-9_-])"
    r"|(?<![\w.+-])@[a-z0-9_][a-z0-9_-]{1,31}(?![a-z0-9_-])"
    r")"
)
```

The tests must confirm both existing `微信 abc_123` and new explicit forms, plus an ordinary `qq` word and email. In `sanitize.py`, change only `_RULES_VERSION = "pii-v2"`. Do not echo candidate text in review reasons.

- [ ] **Step 4: Verify GREEN and commit.** Run focused tests, Ruff and strict mypy; expected: exit 0. Commit `text.py`, `sanitize.py`, `test_text.py`, `test_sanitize.py` with `git commit -m "fix(dataset): redact explicit private handles"`.

### Task 5: Require sanitized-source equality for evaluation eligibility

- [ ] **Step 1: Write failing tests.** Reuse `_export` and `_completed` in `test_labels.py`. For a completed annotation, write its JSON to the exported path and assert `validate_label_root(label_root, sanitized_root)` yields one eligible item. Then parametrize four mutations: `comments=[]`, one removed comment, one duplicated/added ID, and changed `text`; each must raise `LabelValidationError` with a fixed code, not leak the changed text. Add a missing-video case and a synthetic two-video extra-video case. Check second annotation and adjudication against the source as well as the primary. Update `test_cli.py` to call `validate-labels <labels> --sanitized <sanitized>`; assert invoking without `--sanitized` fails argument parsing and never reports eligibility. Change the existing single-file dispute test to assert `validate_labels(...).evaluation_eligible is False` even when structure and adjudication are valid.

- [ ] **Step 2: Verify RED.** Run `uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests/test_labels.py packages/dataset-tools/tests/test_cli.py -q`; expected: root API signature/source checks and single-file eligibility assertions fail.

- [ ] **Step 3: Make single-file validation structural only.** Keep `validate_labels` responsible for existing completeness, independence and adjudication checks, but make both return sites `LabelValidationResult(evaluation_eligible=False)`. Reject `is_complete=True` with zero comments via `_fields_complete` returning false when `not annotation.comments`. Root validation alone may count eligible annotations after source equality succeeds.

- [ ] **Step 4: Validate the complete source set.** Change signature to `validate_label_root(label_root: Path, sanitized_root: Path)`. Add a focused private helper to `labels.py`:

```python
def _source_comments(sanitized_root: Path) -> dict[tuple[str, str], list[SanitizedComment]]:
    if _is_path_redirect(sanitized_root) or not sanitized_root.is_dir():
        raise LabelValidationError("sanitized_root_invalid")
    sources: dict[tuple[str, str], list[SanitizedComment]] = {}
    for path in sorted(sanitized_root.rglob("sampling-manifest.json")):
        relative = path.relative_to(sanitized_root)
        if len(relative.parts) != 3 or any(_is_path_redirect(part) for part in (path.parent, path.parent.parent, path)):
            raise LabelValidationError("sampling_manifest_directory_mismatch")
        manifest = _read_sampling_manifest(path)
        key = (manifest.platform, manifest.video_id)
        if relative.parts[:2] != key or key in sources:
            raise LabelValidationError("sampling_manifest_directory_mismatch")
        comments = _read_comments(path.parent / "comments.jsonl")
        ids = [item.comment_id for item in comments]
        if not ids:
            raise LabelValidationError("label_source_empty")
        if ids != manifest.candidate_comment_ids or len(ids) != len(set(ids)):
            raise LabelValidationError("sampling_manifest_comment_mismatch")
        sources[key] = comments
    if not sources:
        raise LabelValidationError("sampling_manifest_missing")
    return sources
```

Before existing annotation validation, build `sources = _source_comments(sanitized_root)`, reject label-root redirects, and derive each label key from `path.relative_to(label_root).parts[:2]`. Require exactly three relative parts, no redirect in any path component, and the set of label keys to equal `set(sources)`; otherwise raise `LabelValidationError("label_source_set_mismatch")`. In the existing per-file loop, after `_read_annotation`, require `annotation.video_id == key[1]` and:

```python
expected = [(item.comment_id, item.text) for item in sources[key]]
actual = [(item.comment_id, item.text) for item in annotation.comments]
if actual != expected:
    raise LabelValidationError("label_source_comment_mismatch")
```

Existing `validate_labels` already compares secondary and adjudication comments to primary. Count eligibility with `eligible += annotation.is_complete` only after all checks. Return only fixed error codes; never include text or paths in exceptions. If existing path-validation helpers impose stricter symlink checks, reuse them instead of weakening them.

- [ ] **Step 5: Wire CLI and documentation.** In `cli.py`, add `validate.add_argument("--sanitized", type=Path, required=True)` and call `validate_label_root(args.label_root, args.sanitized)`. Update `packages/dataset-tools/README.md` command example and state that single-file checks do not grant evaluation eligibility. Do not regenerate or overwrite existing labels.

- [ ] **Step 6: Verify GREEN and commit.** Run focused tests, Ruff and strict mypy for `packages/dataset-tools`; expected: exit 0. Commit `labels.py`, `cli.py`, `README.md`, `test_labels.py`, `test_cli.py` with `git commit -m "fix(dataset): verify labels against sanitized source"`.

### Task 6: Full gates, synthetic interoperability, and review handoff

- [ ] **Step 1: Verify the cross-package JSON chain.** The contract tests added in Tasks 1–3 must use `model_dump_json()`/`model_validate_json()` round trips, not direct model reuse: a `CollectionRecord` `1.1/2` payload is accepted by `CollectionInput`; a Dataset `SamplingManifest` `1.1/2` payload is accepted by Agent `SamplingManifest`; legacy missing fields become `1.1/null` on Dataset output and yield `degraded` or `insufficient` in Agent. Put one serialized payload shared across the two test suites under `packages/dataset-tools/tests/fixtures/collection-1.1.json` only if existing synthetic fixtures cannot express the case. Run `uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests/test_source.py packages/dataset-tools/tests/test_sanitize.py -q` and `uv run --offline --locked --project packages/agent pytest packages/agent/tests/contract/test_sampling.py packages/agent/tests/sampling/test_policy.py -q`; expected: all pass. Do not add a runtime dependency between packages.

- [ ] **Step 2: Run every package gate.** Execute the following from the repository root:

```powershell
uv sync --offline --locked --project packages/collector
uv run --offline --locked --project packages/collector pytest packages/collector/tests -q
uv run --offline --locked --project packages/collector ruff check packages/collector/src packages/collector/tests
uv run --offline --locked --project packages/collector ruff format --check packages/collector/src packages/collector/tests
uv run --offline --locked --project packages/collector mypy packages/collector/src
uv build --offline --project packages/collector
uv sync --offline --locked --project packages/dataset-tools
uv run --offline --locked --project packages/dataset-tools pytest packages/dataset-tools/tests -q
uv run --offline --locked --project packages/dataset-tools ruff check packages/dataset-tools/src packages/dataset-tools/tests
uv run --offline --locked --project packages/dataset-tools ruff format --check packages/dataset-tools/src packages/dataset-tools/tests
uv run --offline --locked --project packages/dataset-tools mypy packages/dataset-tools/src
uv build --offline --project packages/dataset-tools
uv sync --offline --locked --project packages/agent
uv run --offline --locked --project packages/agent pytest packages/agent/tests -q
uv run --offline --locked --project packages/agent ruff check packages/agent/src packages/agent/tests
uv run --offline --locked --project packages/agent ruff format --check packages/agent/src packages/agent/tests
uv run --offline --locked --project packages/agent mypy packages/agent/src
uv build --offline --project packages/agent
```

Expected: zero failures, no lock changes. If package scripts or build output paths differ, inspect the package `pyproject.toml` before adjusting commands; never claim a gate ran if it did not.

- [ ] **Step 3: Read-only historical audit.** With an explicit, verified local path to the existing 24-video source, read records only; assert all 24 remain present, old collections parse with unknown count, and no raw text or IDs are printed. Do not run `sanitize_root` against current `sanitized/` or `export_labels` against current `labels/`; do not migrate in place. If the local path is unavailable, report that audit as not run instead of substituting synthetic evidence.

- [ ] **Step 4: Review diff and update handoff.** Run `git diff --check`, `git status --short`, and inspect the diff for unrelated changes, raw identifiers, secrets, and generated data. Update local `docs/HANDOFF.md` and `docs/execution/2026-09-24.md` with commits, gate results, historical audit status, unresolved risks, and the next action; update the execution index only if a new daily log is created. Request an independent code review per project practice; resolve findings before pushing.

- [ ] **Step 5: Publish only after review.** Commit any final test/doc changes. Push `codex/dataset-preparation-pr`, verify PR #6 head and checks, and attach the PR to this task if not already attached. Keep PR #6 open until the three blockers are demonstrably resolved, full gates pass, and review is clean. Do not merge merely because the branch is mechanically mergeable. If a migration of real local copies is later approved, record destination, preservation method, and verification in a separate task.
