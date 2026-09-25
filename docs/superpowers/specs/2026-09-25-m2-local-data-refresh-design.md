# M2 Local Data Refresh Design

## Scope and decision

Regenerate the approved 24-video local dataset with the merged Dataset Tools contract, while preserving every existing raw, sanitized, and label artifact. This is a local data operation, not a new model evaluation or an invitation to infer human labels. Use sibling output directories under the existing ignored `.local-data/m2-real/` root: `sanitized-v2/` and `labels-v2/`. Do not rename, overwrite, remove, or symlink the existing `raw/`, `sanitized/`, or `labels/` directories. Do not commit any real data or secrets.

The user approved the sibling-directory approach on 2026-09-25. The preflight read-only audit found 24 old sampling manifests, 24 primary annotation files, zero completed or partially filled primary annotations, and no secondary/adjudication files. The two proposed new directories and their backup siblings did not exist. These observations must be rechecked immediately before execution; they are not permissions to overwrite if state changes.

## Inputs and ownership

- Read only the existing approved manifest `.local-data/m2-real/candidates/approved-manifest.json` and raw source tree `.local-data/m2-real/raw/`. Verify the approved manifest SHA-256 is `9A50A0EAA9FED72C23E2340B0A0C2B32D4E7F316A8EDC263981450E70515D542` before processing. Its 24 entries, not every raw directory, define the batch. The two historical pilot directories remain excluded.
- Use the existing stable `HMAC_SECRET_KEY` value from `HKEY_CURRENT_USER\Environment` only within the generating process. Pass only the environment-variable name to `rs-dataset`; do not print, persist, log, or put the value in a command argument. Never create a new key for this refresh.
- Run the merged Dataset Tools from a clean branch based on `upstream/main`, with its lock file unchanged. Generated data stays ignored and local. The existing `sanitized/` and `labels/` are immutable comparison baselines, not output targets.

## Data flow

1. Preflight: verify exact source and destination paths, manifest hash, source/readability, no path redirects, enough free space, no `sanitized-v2/` or `labels-v2/` target or corresponding `.backup`/staging transaction, and no human edits in the old label tree. Record aggregate counts and content-tree hashes for `raw/`, `sanitized/`, and `labels/` without displaying identifiers or text. The tree digest sorts slash-normalized relative paths ordinally, then hashes UTF-8 records of relative path, NUL, uppercase per-file SHA-256, and LF. Abort on any mismatch or unexpected target.
2. Generate `sanitized-v2/` once from `raw/` and the approved manifest using `rs-dataset sanitize --create-only` and the stable HMAC environment name. On Windows, the sanitizer stages output and publishes it with a directory rename that fails if a target appeared after preflight; that target must remain untouched. This create-only mode fails closed on other platforms. Never direct it at the existing `sanitized/`; the default replacement mode would delete its temporary backup after success.
3. Validate the new sanitized tree before label export: 24 sampling manifests, 5,090 sanitized comments, zero replacement candidates, and a 10/7/7 split. Every manifest must be schema `1.1` with `exact_duplicate_count: null` for these historical sources; every sanitization report must identify `pii-v2`. Compare pseudonymous video and ordered comment IDs to the old output to catch an accidental key change, while allowing intentionally changed redacted text. Scan for known raw-ID/secret leakage and residual recognized private-handle/address patterns without printing matched values. A zero scan count is a bounded check, not proof against all novel PII forms.
4. Only after sanitized validation succeeds, export new blank templates to `labels-v2/`. Validate the complete root against `sanitized-v2/` with the source-aware command. Require 24 primary annotation files, 5,090 explicit `unlabeled` comments, no completed/evaluation-eligible files, and no secondary/adjudication files. Do not copy old annotation files: although presently blank, their text may differ from the new sanitized source.
5. Recompute old-tree hashes and confirm `raw/`, `sanitized/`, and `labels/` are byte-for-byte unchanged. Record aggregate results, code revision, manifest hash, output paths, and any limitations in the local execution log and handoff. Only then designate `sanitized-v2/` plus `labels-v2/` as the materials for subsequent human labeling; no path rename or automatic promotion occurs.

## Failure behavior and boundaries

- Any failed preflight, generation, privacy scan, count, pseudonym, contract, or source-equality check stops the process. Do not continue to label export or declare the new data ready. Preserve existing directories untouched; leave any newly created incomplete `*-v2` output or staging artifact for inspection rather than automatically deleting it.
- `--create-only` never replaces an existing target or recovers a backup into the target. An existing target or backup at preflight, or a target appearing at commit, stops publication. The default sanitizer mode retains its existing replacement behavior for other workflows.
- If the old label tree now contains a human edit, stop and redesign the handoff of that work instead of discarding or silently copying it. If pseudonymous IDs differ, stop and investigate secret continuity; do not force a mapping.
- If a recognized privacy candidate remains, report only a count and error category. Review its handling in a separate controlled step; do not paste real comments, raw IDs, pseudonymous IDs, or secret values into chat, terminal output, Git, or logs.
- This refresh creates safe blank materials, not gold labels. The existing acceptance gate still requires at least 12 human-initially-labeled pilot videos for limited model smoke testing and 24 independently labeled/adjudicated videos for full semantic evaluation.

## Verification

Run the Dataset Tools package tests and lint/type/format gates before the real operation. Verify transaction preconditions and read-only baseline hashes, then execute one generation and one export to absent sibling paths. Use the package's structural validators plus independent aggregate and leak checks above. Confirm post-run baseline hashes match pre-run values and inspect Git status to ensure no real artifact entered version control. Log the exact checks actually run and their counts; do not claim an unrun gate passed.
