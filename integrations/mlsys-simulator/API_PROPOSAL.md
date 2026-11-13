# Minimal API proposal

This is a proposal for agreement, not an implemented simulator endpoint. The
current scope is data interchange for the three existing tasks. It replaces the
earlier asynchronous-first proposal; no task redesign is required.

## First integration endpoint

Proposed path: `POST /v1/simulate`, with a JSON request and a complete JSON
response when execution ends. The simulator team can choose another path.
One execution slot is acceptable. A static, agreed catalog is sufficient for
the first integration; a capabilities endpoint can follow later.

- [Field meanings, units and execution semantics](DATA_CONTRACT.zh-CN.md).
- [Request JSON Schema](schemas/request.schema.json).
- [Response JSON Schema](schemas/response.schema.json).
- [Task 9.1](examples/task-9.1.request.json), [task 9.2](examples/task-9.2.request.json),
  [task 9.3](examples/task-9.3.request.json): existing baseline choices and exact traces.
- [Explicit mock response shapes](examples/RESPONSE_EXAMPLES.md): schema examples
  only, never performance evidence.

The lab backend sends workload rows, hardware/model identifiers and public
strategy settings. The simulator owns geometry, calibration and execution.
The response contains complete per-request outcomes, placement, resource data,
effective configuration and provenance. Task verdicts, quality evidence, shared
metric aggregation and reports remain on the lab side.

`workload.source_sha256` identifies the original JSONL file for provenance.
`input_sha256` in the response hashes the exact received UTF-8 request body
bytes, without JSON reserialization. It binds the result to the submitted data;
it does not imply idempotency. The synchronous client does not automatically
retry after an uncertain connection timeout.

`evaluation.duration_s` is the existing metric denominator, `execution.max_simulated_ms`
is the simulated clock cutoff, and `evaluation.timeout_s` is a wall-time execution
limit. They are distinct even when examples derive their initial values from the
same current task duration. No implicit workload regeneration, state reuse,
precision fallback or client-side DAG dispatch is permitted.

Return HTTP 200 for a handled experiment outcome, including modeled OOM; use
400/422 for invalid or unsupported input, 429 for a busy service, and 5xx for a
service failure. Errors have `code`, `message` and an optional `field`. A network
failure is not evidence that execution failed or was cancelled.

Poll/cancel/idempotency, durable job IDs, streaming progress and separate large
artifact downloads can be added after the data contract works. The previous
lifecycle proposal remains available in Git history at lab commit `c9df4ad`.

First agree model/catalog identities, per-replica concurrency and KV budget
scope, causal timestamps and available nullable diagnostics. Then run the exact
9.2 trace over HTTP, followed by sequential 9.1 and multi-pool DAG 9.3. Passing
schema checks alone does not complete that integration.
