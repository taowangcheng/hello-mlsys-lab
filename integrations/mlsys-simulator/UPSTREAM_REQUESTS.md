# Requested simulator API changes

This note is for coordination between the simulator team and the learning-lab
team. It records observed compatibility gaps, not a request to copy lab
implementation details into the simulator. Endpoint spelling is negotiable.

Baseline inspected: `mlsys-simulator` **1.19.0**, commit
`fc6379ff66dd58362a06cb27ae0f1366347e5df9`. The checkout and upstream service were
not modified. [HTTP evidence](evidence/http-report.json) accompanies the note.

## P0: accept exact workload records through a service API

The existing `/api/simulate` accepts counts, one input/output length and an
arrival generator. It rejects an exact request list. Please expose an execution
endpoint accepting inline records or a separately uploaded immutable workload.
Do not require client-local filesystem paths.

Each request needs its original ID, input tokens, forced output target and,
where relevant, arrival time, target model, dependencies and dependency gap.
The service must preserve IDs and return one terminal record for every offered
request, including rejected, blocked and unfinished requests.

Task 9.2 already supplies 12 mixed-length, timed requests. Generating an
approximately similar Poisson or periodic cohort would change the experiment.
The native Python `Experiment.workload` already accepts explicit rows; this
mainly needs a suitable HTTP entry point.

Acceptance: submit [the 9.2 example](examples/task-9.2.request.json) after agreeing
the model catalog; verify exact IDs, input lengths, targets and arrival times
against the response. Changing only JSON field names is acceptable; regenerating
the workload is not.

## P0: define sequential and DAG timing explicitly

**Task 9.1:** replay the supplied ordered list with one request released when the
previous request terminates. Preserve varying input and output lengths. Please
support a fixed-stream sequential mode or an equivalent public, documented
closed-loop API. The existing HTTP arrival modes are only periodic/Poisson/burst.
The native finite closed-loop API generates a stream from `input_lengths` with
one common output length; its public schema does not accept our fixed list.
Turning the list into a failure-blocking DAG would change sequential failure
semantics and is not our proposed integration.

**Task 9.3:** expose heterogeneous model pools and dependencies through HTTP, and
add a relative dependency delay:

```text
ready(child) = max(finished(parent) for parent in parents) + gap_after_deps_ms
```

Root nodes retain their declared arrival times. A failed parent must not release
its children as if it succeeded; return blocked records and their cause. Define
how absolute releases interact with relative gaps if both are supplied.

The native `simulate_dag` API already handles multiple pools, replicas and causal
dependencies. However, `DAGNode.release_ms` is an absolute lower bound and the
current formula is `max(release_ms, parent completion)`. It cannot represent
"100 ms after the last parent finishes". Our 9.3 trace contains those relative
gaps. Adding an absolute 100 ms release would erase them after a longer parent.

Acceptance: a parent finishing at 1000 ms with a 100 ms dependency gap releases
the child at 1100 ms. The result must keep the same behavior when the parent's
service time changes. No client-side event driver, dummy nodes or iterative
replays should be needed.

## P0: keep model/hardware resolution on the service

Please return supported model IDs, hardware/infra IDs, precisions, mechanisms
and a catalog revision from a capabilities endpoint. The service owns model
geometry, memory planning, calibration and nominal hardware rates. The agent
must not supply hidden size, layer counts, FLOP rates, fit coefficients or private
engine configuration in order to run an experiment.

This is partly **our catalog issue**, not solely an upstream defect:

- `llm-1.7b` and `llm-4b` identify Qwen3 variants in our catalog, but their exact
  revisions still need agreement.
- `llm-0.5b` and `llm-3b` are size placeholders, not complete model identities.
- `llm-7b` and `llm-32b` are also not uniquely identified real models. Upstream
  course examples use explicitly declared teaching macro geometries.
- Our hardware catalog has VRAM but no calibrated compute/bandwidth values.
  Upstream RX9070XT course rates are nominal; W7900D teaching assumptions must
  not be described as measured device rates.

Both teams must agree the catalog or explicitly name teaching profiles. The lab
will then update its task choices. Unsupported IDs should be rejected rather
than inferred from a parameter-count label. Nominal predictions should remain
available only with clear `uncalibrated` provenance; unknown calibrated-domain
coverage must not be reported as in-domain.

## P1: expose capabilities and lifecycle for remote execution

The current service binds only loopback, has one synchronous execution slot,
rejects concurrent requests with `429 WEB_BUSY`, and uses a 20-second wall limit.
It does not expose durable job IDs, polling, cancellation or artifact retrieval.
The observed 429 response contains no `Retry-After` header.

Please provide an agreed service endpoint and:

- Submit with an idempotency key; retries must not create duplicate runs.
- Poll a stable job ID; distinguish execution status from constraint satisfaction.
- Cancel a job, with immutable terminal state and artifacts.
- Fetch complete request/event/log artifacts after completion; support large
  records without UI-style thinning or truncation.
- Declare concurrency and request/time limits; queue jobs or return a retryable
  busy response with `Retry-After`. One execution slot is acceptable.
- Expose software, API, catalog and calibration versions in result provenance.

HTTP need only be reachable from the **lab backend**. The browser/agent need not
connect directly to the simulator; permissive browser CORS is not required.
Deployment/authentication can follow the service environment's normal policy.

## P1: publish complete observations and metric semantics

Please return request timestamps, actual and target token counts, status/error,
and per-device memory with explicit units. A normal transport adapter can rename
`arrival_ms`/`completion_ms` to our fields; no engine emulation is needed.

| Item | Current difference | Proposed responsibility |
| --- | --- | --- |
| TTFT | Native aggregate uses completed requests; lab includes any request with a first token | Return all raw request timestamps; lab recomputes its aggregate |
| Throughput | Native default divides all emitted tokens by elapsed simulation time; lab divides completed output tokens by declared task duration | Keep native metrics labeled; lab recomputes its metric from the complete ledger |
| Goodput | Eligibility and denominator must match the task's declared SLA policy | Return raw timing/counts and policy metadata; lab evaluates task constraints |
| DAG critical path | Native value follows latest-parent completion and includes waiting; lab requires a service-time lower bound excluding queueing | Provide defined per-request `service_ms`, or report this metric as unavailable; never rename native makespan to the lab's critical path |
| Memory | Reserved physical memory, safety budget and logical KV usage are different quantities | Preserve named per-device byte fields; lab converts bytes to GiB for display |
| Errors | `ok: true` or process exit success need not mean every request completed | Return execution status, failure reason and all request outcomes separately |
| Quality | Simulator does not model semantic quality | Lab retains its explicitly draft quality table; no simulated accuracy claim |

Our task duration and worker wall timeout are distinct. Please agree admission,
drain and simulated-time-limit behavior explicitly; neither team should silently
use one as the other. A failed or truncated request remains in the completion
denominator. Unsupported energy, quality or service-path metrics should be null
with a reason.

## Additional capability limits to negotiate

The current HTTP form caps input at 8192 tokens and output at 512; task 9.3 needs
16384 input and 1024 output tokens. Its 64 KiB HTTP body limit may also need
adjustment or workload upload support for larger traces.

TP/PP, replicas, prefix caching, swap and DAG support exist to varying degrees in
the core, but are not all exposed by the HTTP form. Expose only validated
combinations. Our policy names such as `short_first`, `critical_path` and
`restart` require explicit agreed meanings; they must not be silently mapped to
FIFO or arbitrary numeric priorities. Prefix groups alone do not establish a
token-identical prefix: both teams need to agree prefix length/content identity
before enabling that mechanism.

## Work retained on the lab side

We will implement HTTP transport, error/status translation, retries consistent
with server idempotency, artifact persistence and hash checking, shared metric
evaluation, UI history/references/comparisons, and Agent tools. We will update
our model aliases after catalog agreement. We will not port scheduling, invoke
private simulator modules, synthesize missing performance data, modify frozen
traces, or patch the simulator to make a demonstration pass.

The immediate next joint milestone is **one unchanged 9.2 workload over HTTP**
with agreed model IDs and complete records. Then validate 9.1 sequential release
and 9.3 multi-pool DAG timing, including failure and cancellation cases.
