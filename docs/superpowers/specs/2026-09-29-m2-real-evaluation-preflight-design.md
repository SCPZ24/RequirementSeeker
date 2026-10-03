# M2 Real Evaluation Preflight Design

## Goal

Add an offline, fail-closed preflight for the future real-model evaluation path without changing the existing `analyze_m2` compatibility contract. The preflight proves that a concrete model revision is frozen before a paid call can start, and the invocation boundary verifies that the provider actually returned the requested model identity before any result may be cached or treated as successful.

This design does not add a provider SDK, perform a real model call, spend API funds, or claim that the local labels are human semantic gold. The current 24-video label root is only structurally evaluation-eligible against its sanitized source.

## Chosen Approach

Introduce a separate real-evaluation preflight layer and strengthen the shared invocation audit boundary.

Two alternatives were rejected:

- Tightening `analyze_m2` globally would break the accepted offline compatibility where `ScenarioModelGateway` may run with `request.model.revision=None`.
- Implementing a provider-specific adapter now would force a provider, SDK, credential, pricing, and model choice that has not been authorized.

The separate preflight keeps the existing M2 library usable while making the future paid path explicit and closed by default.

## Components

### Frozen runtime identity

Add a non-secret gateway identity value containing the adapter's configured provider/model identity and verifiable revision. A production gateway adapter must expose this identity without making a model call. The fake scenario gateway may expose fixture identity for tests, but ordinary `analyze_m2` does not require the revision to be non-null.

The real-evaluation preflight accepts the `AnalysisRequest` and gateway identity/capabilities, then returns a validated frozen identity only when:

- the requested revision is non-null;
- the gateway reports a non-null verifiable revision;
- requested and gateway model names match;
- requested and gateway revisions match;
- required text and structured-output capabilities are available; and
- the gateway's per-call input limit is compatible with the request's configured budget.

Failures use stable non-secret error codes and do not invoke the gateway.

### Invocation response verification

The shared invocation boundary compares every successful provider response with the frozen requested model name and revision when a frozen identity is supplied by the real-evaluation path. A missing or mismatched actual identity is a non-retryable failure. The response is not passed to semantic parsing and is not cached.

The model call did occur, so its usage is still settled conservatively and an error audit is retained. Transport retries remain limited to the existing transport error set; identity failures are never retried.

Ordinary `analyze_m2` calls that do not supply a frozen identity preserve current behavior, including `revision=None` with `ScenarioModelGateway`.

### Cache binding

The real-evaluation path builds cache keys from the validated frozen revision, never from an unresolved or provider-inferred value. Existing cache key fields remain otherwise unchanged. A response identity failure happens before cache insertion.

The initial implementation remains an in-memory semantic cache. Persistent cache storage and cross-process retention are outside this task, but any later implementation must keep the same frozen revision in its key material.

### Audit identity

Extend model invocation audits with four explicit fields:

- `requested_model_name`
- `requested_revision`
- `actual_model_name`
- `actual_revision`

For a provider response, actual fields record the provider-returned identity even when it mismatches and the call fails closed. For pre-response transport/configuration failures, actual fields are null. Existing `model_name` compatibility, if retained, must have one documented meaning and must not substitute for the four explicit fields.

The contract and generated JSON Schema must be updated together. The smallest compatible representation uses nullable fields so historical or pre-response failures remain representable; real-evaluation success separately requires all four values and exact equality.

### Real-evaluation entry point

Add a distinct public entry point that performs preflight before delegating to M2. It must require an explicit frozen runtime identity and must not silently fall back to ordinary `analyze_m2`. This task does not add an authorization flag that implies permission to spend money; the caller still needs separate user authorization before constructing or invoking a production gateway.

## Data Flow

1. The caller constructs an `AnalysisRequest` with a non-null requested model revision.
2. The production gateway exposes its configured model identity, revision, and capabilities without a paid inference.
3. The real-evaluation preflight validates exact identity and capability compatibility and produces a frozen runtime identity.
4. The evaluation entry point runs M2 with that frozen identity.
5. Each provider response is compared with the frozen identity before parsing or caching.
6. The audit records both requested and actual identity; only an exact verified match may proceed to validated results and cache insertion.

## Failure Semantics

- Requested revision absent: fail before any model call.
- Gateway revision absent or unverifiable: fail before any model call.
- Configured gateway identity differs from the request: fail before any model call.
- Provider response omits or changes model/revision: settle usage, record a non-retryable error audit, do not parse or cache.
- Budget, retry, cancellation, and cache behavior otherwise retain the existing M2 semantics.
- No failure output may include credentials, prompts, comment text, raw response bodies, or real label contents.

## Testing

Use strict RED-GREEN tests for each behavior:

- Existing `analyze_m2` with `ScenarioModelGateway` and `revision=None` still completes.
- Real-evaluation preflight rejects requested `revision=None` before gateway invocation.
- Real-evaluation preflight rejects a gateway with no verifiable revision.
- Preflight rejects configured requested/gateway model or revision mismatch.
- Provider response model mismatch and revision mismatch each fail closed, produce no cache entry, and are not retried.
- Successful real-evaluation fixture calls bind cache keys to the frozen revision.
- Success, mismatch, and pre-response failures populate requested/actual audit identity correctly.
- JSON Schema and tracked fixtures remain synchronized with the audit contract.
- Existing budget, retry, cancellation, cache, and full Agent tests remain green.

All tests use deterministic gateways and synthetic inputs. No production SDK, credential, network model call, or fee is permitted.

## Documentation and Tracking

Document the real-evaluation boundary, the need for separate call authorization, and the in-flight cancellation limitation. Describe the 24/24 local labels only as structurally evaluation-eligible, not as 24/24 human semantic gold.

Track `export-labels` separately in [issue #8](https://github.com/SCPZ24/RequirementSeeker/issues/8): remove or redesign legacy backup recovery and failed-staging deletion before any label-refresh plan is rerun. That issue is not bundled into this implementation because it is a separate Dataset Tools transaction contract.

## Out of Scope

- Selecting OpenAI or another provider, model, SDK, endpoint, or price tier.
- Loading API keys or making real model calls.
- Persistent cache implementation.
- Interrupting an already in-flight synchronous provider request.
- Running semantic metrics on the real labels.
- Changing or regenerating labels, sanitized data, or raw data.
- Fixing `export-labels` in this branch.
