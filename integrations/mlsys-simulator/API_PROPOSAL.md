# Proposed service contract

**Draft for agreement, not an implemented upstream API.** Existing simulator
field names are welcome if they preserve the semantics below. The service owns
all translation from these public choices to its engine configuration.

## Operations

| Operation | Proposed HTTP mapping | Required behavior |
| --- | --- | --- |
| Discover | `GET /v1/capabilities?task=9.2` | API/software/catalog versions, supported models/infra/mechanisms and limits |
| Submit | `POST /v1/runs` | Validate, reserve an idempotent job, return 202 and a job ID |
| Poll | `GET /v1/runs/{id}` | Status/progress and original input identity; terminal state is immutable |
| Cancel | `POST /v1/runs/{id}/cancel` | Idempotent; a completed job remains completed |
| Fetch | `GET /v1/runs/{id}/artifacts/{name}` | Stream a complete terminal artifact, with hash/length metadata |

The lab backend makes these requests. The browser and the learning agent keep
using lab tools and records; neither needs a simulator package, local source
checkout, model architecture or calibration coefficients.

Capabilities should distinguish the execution mechanisms from the supported
combinations of model, precision, placement, scheduler and calibration. A
supported `tensor_parallel` label must not imply every TP degree is available
for every model. A serial service is acceptable when its concurrency limit and
retry/queue policy are explicit.

## Submission

The examples are exports of the current task baselines, without changing the
workload. They are intended to make protocol negotiation concrete:
[9.1](examples/task-9.1.request.json), [9.2](examples/task-9.2.request.json),
[9.3](examples/task-9.3.request.json).

| Field | Meaning |
| --- | --- |
| `schema` | `mlab.simulator.v1`; a proposed transport version, separate from engine versions |
| `catalog_version` | An agreed service catalog revision; examples deliberately use `to-be-agreed` |
| `client_request_id` | Stable caller identity for logging; does not authorize changing an existing job |
| `task`, `candidate_id` | Caller metadata; the engine need not know course workflow stages |
| `workload.mode` | `sequential`, `open_loop`, or `dag` |
| `workload.requests` | Exact inline records, in trace/topological order; no client-local paths |
| `workload.requests_sha256` | Hash of the canonical request array, described below |
| `infra` | Named infrastructure/devices using hardware IDs, without compute rates or private profiles |
| `strategy` | Model IDs, precision, context, placement, replicas, parallelism and serving policy |
| `evaluation` | Declared task duration, constraints and separate wall timeout; drain/limit policy needs agreement |
| `execution` | Seed, repeat, split and explicit initial-state/warmup policy |

The transport layer may ignore descriptive request metadata such as `category`
for execution, but must retain it in the workload identity. Execution-relevant
unknown fields or unsupported combinations must produce an explicit error.
Forced output targets belong only to the replay oracle, not a scheduler's view
of future work.

For these examples, canonical JSON is UTF-8, sorted object keys, separators
`,` and `:`, no ASCII escaping and no nonfinite numbers. The hash is prefixed
`sha256:`. A production cross-language API should agree canonicalization (for
example RFC 8785) or define upload-byte hashes before enabling integrity checks;
Python's formatting alone is not a portable canonicalization standard.

`Idempotency-Key` applies to the complete accepted request. A duplicate key with
identical content returns the same job. A key with different content returns a
conflict; it must not overwrite or rerun the original job. The service returns
the accepted request hash, resolved catalog/calibration identity and job ID.
No API key is placed in request JSON, model prompts or recorded artifacts.

## State and errors

```text
queued -> preparing -> running -> collecting -> succeeded
                                     \-------> failed / timed_out / cancelled
```

`succeeded` means execution produced a complete report, not that the task's SLA
or quality requirement was met. Per-request failures can be part of a valid
report. The lab independently computes its verdict from the full ledger.

Schema/unknown-ID errors should return 400 or 422 before creating a runnable
job. Idempotency conflicts use 409. A full service returns 429/503 with a machine
error code and `Retry-After`, or accepts a queued job. An execution failure is a
terminal job outcome with artifacts; it is not a reason to erase the run.

Errors should identify unsupported mechanisms, missing profile/domain coverage,
OOM, timeout and service failure separately. No fallback to mock, random,
smaller-model or modified-workload predictions is permitted.

## Terminal artifacts

The existing lab Backend interface consumes these five artifacts. Their HTTP
transport can be streaming or paginated as long as the final reconstructed
records are complete and their hashes are verifiable.

| Artifact | Required contents |
| --- | --- |
| `manifest.json` | Accepted input/hash, software/API/catalog/calibration versions, seed, resolved configuration identity, artifact hashes, assumptions/domain coverage |
| `result.json` | Execution status/failure, request counts, metric definitions/units, unsupported-metric reasons, `evidence_level: simulated`, calibration status |
| `requests.jsonl` | Every offered request, including failure/blocked/truncated records |
| `events.jsonl` | Complete supported event trace; an empty file with an explicit unsupported reason is acceptable initially |
| `logs.txt` | Diagnostic messages and machine error codes, without secrets |

The manifest itself is hashed in terminal job metadata; other hashes can be
inside the manifest. Terminal data must survive disconnect/retry and must not
change after completion. If event timelines are intentionally thinned, return
an explicit separate display view; do not label it the complete event ledger.

Per-request fields needed by the lab:

```json
{
  "request_id": "r001",
  "ready_ms": 0.0,
  "first_token_ms": 25.0,
  "finished_ms": 55.0,
  "output_tokens": 4,
  "output_tokens_target": 4,
  "status": "completed",
  "service_ms": 50.0,
  "depends_on": [],
  "gap_after_deps_ms": 0.0
}
```

This is an illustrative schema record, not observed performance. All timestamps
use the same simulation origin and milliseconds. Missing first-token timestamps
are null. Preserve blocked/failed reasons in an additional agreed field; do not
invent a successful completion time for unexecuted work. Whether a blocked node
uses a terminal decision timestamp or null needs agreement with the lab's
current non-null observation schema.

`service_ms` excludes queueing and dependency delay; the simulator must document
how batching, preemption and overlap contribute. If unavailable, return null
with a reason and the lab will make the corresponding schema change and leave
`critical_path_s` unavailable. Raw timestamps and counts let the lab compute its
own TTFT/TPOT/throughput definitions without depending on private engine events.

Quality is outside the performance API. The service need not reproduce the
lab's draft quality table, student verdict, conversation or report format.

## Joint acceptance checks

1. Unchanged request IDs, input lengths, output targets and arrival/dependency
   semantics for all three task examples, after resolving catalog IDs.
2. Repeatable results for the same complete input, engine/profile revision and
   seed, with wall-clock metadata kept separate.
3. Single-output requests, OOM, partial output, timed-out parents and blocked
   descendants remain in the ledger and completion denominator.
4. A 100 ms dependency gap is retained when parent service time changes.
5. Duplicate submission creates one job; changed content with the same key
   conflicts; cancellation and lost connections preserve terminal evidence.
6. Busy/queued service behavior matches published concurrency limits.
7. Unsupported mechanisms and unknown model IDs fail explicitly. Unavailable
   metrics have a reason, and nominal simulation is never presented as measured
   hardware or calibrated validation.
