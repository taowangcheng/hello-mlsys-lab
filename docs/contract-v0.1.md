# mlab.v1 contract (v0.1 draft)

This document describes the agreement between `mlab` and the execution backends. The agent and the student deal only with the `mlab` CLI, and a backend deals only with this contract, so the student's workflow stays the same when the backend changes. The first backend is the LLM simulator (see `docs/plan.md` section 6); backends added later implement the same contract.

The implementation defines it with pydantic and exports JSON Schema. Examples of every field are in `examples/` (all numbers are made up).

## 1. What a backend implements

At minimum, a simulator needs only one function, the entry point from part 1 of the implementation plan; an adapter handles the rest:

```python
simulate(workload, infra, strategy, calibration, evaluation, execution)
```

Here `calibration` is provided by the simulator side; `mlab` does not pass it.

A backend can also implement the `Backend` protocol directly:

```python
class Backend(Protocol):
    def capabilities(self, task) -> Capabilities  # roles, mechanisms, metrics
    def submit(self, spec, key) -> Handle         # idempotent, non-blocking
    def poll(self, handle) -> RunState
    def cancel(self, handle) -> None
    def fetch(self, handle, dest) -> Artifacts    # 5 artifacts + sha256
```

## 2. Input

`RunSpec` is the five items of the implementation plan's six-tuple other than `calibration`. Which calibration to use is up to the simulator, and its version is written into `manifest.json`.

| Field | Content | Who can change it |
| --- | --- | --- |
| `workload` | Reference to the workload file and its hash; the mode is `sequential`, `open_loop` or `dag` | Frozen by the task |
| `infra` | Nodes, devices, links | Chosen from the task's `infra_options` |
| `strategy` | Deployment, serving parameters, scheduling, caching, speculative decoding | The agent, within the task's decision space |
| `evaluation` | Constraints, declared evaluation duration, timeout | Frozen by the task |
| `execution` | Seed, repeat index, split, initial state, warm-up | Set by `mlab` |

A strategy describes only a configuration: it has no per-`request_id` fields and cannot read output lengths, so 9.3 scheduling cannot be written as a fixed answer, and an online policy cannot see the future.

## 3. Output

Each run produces 5 files; a failed run must still have `logs.txt` and `result.json`:

| File | Content |
| --- | --- |
| `manifest.json` | Resolved inputs, hashes, seed, versions; for a hardware measurement, also an environment snapshot |
| `result.json` | Status, verdict, metrics, coverage |
| `requests.jsonl` | Per-request timings and status |
| `events.jsonl` | Optional event timeline |
| `logs.txt` | Errors and diagnostics |

### Three orthogonal fields

This is the core of the whole contract.

| Field | Question it answers | Values |
| --- | --- | --- |
| `status` | Did the run itself complete? | `succeeded`, `failed`, `cancelled`, `timed_out` |
| `verdict` | What does it say about the task's constraints? | `constraints_met`, `constraints_violated`, `not_evaluated` |
| `evidence_level` | Where do the numbers come from? | `mock`, `simulated`, `measured` |

`verdict` cannot be written by hand; it is computed from the evidence by these rules, the same at every evidence level. `mock` with `constraints_met` means "met according to placeholder numbers".

1. OOM is a definite answer (it does not fit), so it counts as `constraints_violated`.
2. Other failures say nothing, so they count as `not_evaluated`.
3. A constraint violation takes precedence over "not evaluated".
4. If quality is not evaluated, the result cannot count as `constraints_met`.
5. Constraints whose threshold is not set yet (`tbd`) take no part in the verdict but stay in the result.

Rule 4 closes the "smallest model + lowest precision" loophole: quality is looked up in `quality_table.yaml`, and a missing entry means `not_evaluated`. The table is a handwritten draft that marks, per model and precision, whether the quality floor is met; it is not measured and will be replaced once measured data exists.

### Metric definitions

Shared by all backends and computed from `requests.jsonl` by the shared metrics module. A backend that computes them itself must get the same results.

| Metric | Definition |
| --- | --- |
| `ready_ms` | The timing boundary. For `open_loop`, the scheduled arrival time; for `dag`, the time all dependencies have finished plus `gap_after_deps_ms`; for `sequential`, the time the previous request finished |
| `ttft_ms` | `first_token_ms - ready_ms`, only for requests with a first token |
| `tpot_ms` | `(finished_ms - first_token_ms) / (output_tokens - 1)`, only for requests that completed normally with at least 2 output tokens |
| `e2e_ms` | `finished_ms - ready_ms`, only for requests that completed normally |
| Percentiles | Linear interpolation (the default method of `numpy.percentile`) |
| `completion_rate` | `completed / total`. Failed, rejected, timed-out and truncated requests all stay in the denominator |
| `truncated` | The actual output is shorter than `output_tokens_target`. Replay forces the output length, so this counts as a failure |
| `throughput_tok_s` | Total output tokens of normally completed requests / the declared `duration_s` |
| `goodput_tok_s` | The same, but only for requests whose TTFT and TPOT are both within the task's thresholds. Returns `null` with the reason when the thresholds are not set |
| `decode_tok_s` | The median over requests of `1000 / tpot_ms` |
| `makespan_s` | The last finish time minus the earliest `ready_ms` |
| `critical_path_s` | The largest sum of service times plus gaps along a dependency chain, excluding queueing; a lower bound on wall-clock time |
| `*_gib` | 2^30 bytes. Where documents say GB, GiB is assumed for now |

The denominator of `goodput_tok_s` and `throughput_tok_s` is the duration declared in advance, not how long the run actually took (implementation plan, part 1).

## 4. Refuse what is not supported

A backend declares the mechanisms it supports in `capabilities` (the `Mechanism` enum). Everything else returns `E_MECHANISM_UNSUPPORTED`; a backend must never silently ignore a parameter or finish the run with the wrong model.

The same goes for metrics: an unsupported metric returns `null`, with the reason in `unsupported_metrics`.

A backend with a calibrated range returns `E_OUT_OF_CALIBRATION` by default outside that range; when it extrapolates explicitly, it lists the dimensions in `coverage.extrapolated`.

## 5. Errors

| Exit code | Meaning | Error codes |
| --- | --- | --- |
| 0 | Handled, including a failed run and an unmet SLA | See `status`, `verdict` |
| 2 | Bad input that the agent can fix, at most twice | `E_SCHEMA`, `E_INCOMPARABLE` |
| 3 | Blocked by a rule; handed to the student | `E_OUT_OF_SPACE`, `E_FROZEN_CHANGED`, `E_MECHANISM_UNSUPPORTED`, `E_OUT_OF_CALIBRATION`, `E_NEEDS_APPROVAL`, `E_APPROVAL_STALE`, `E_BUDGET`, `E_REPEAT_LIMIT` |
| 4 | Backend temporarily unavailable | `E_BACKEND_UNAVAILABLE`, `E_MOCK_INVALID` |

A failed run uses `failure_class`: `oom`, `deploy_failed`, `not_ready`, `config_mismatch`, `gpu_busy`, `interference`, `timeout`, `lost`.

## 6. Differences from the implementation plan

This contract extends the implementation plan in these places:

| Difference | Reason |
| --- | --- |
| Adds `verdict`, `evidence_level`, `failure_class` | The implementation plan's "run completed" and "SLA met" must be kept apart; LLM placeholder numbers must not be labeled `simulated` |
| Adds the conclusion level `mock_only` | The highest conclusion possible on LLM placeholder numbers |
| workload adds `depends_on`, `gap_after_deps_ms`, `target_model`, `controller_id`, `subagent_id`, `prefix_group` | 9.3 is dependency-driven, and the implementation plan covers only open-loop arrivals |
| Metrics add `makespan_s`, `critical_path_s`, `decode_tok_s`, `per_device`, `tightest_constraint` | Used by the objectives of 9.1 and 9.3 |
| Real hardware does not expose `deploy start/stop`; a run is atomic | Weak models tend to leave deployments behind. `deployment_id` is recorded in the manifest |
| Adds `quality_table` | Without a table lookup in the simulation phase, the quality floor would mean nothing |

## 7. Acceptance

All backends run the same interface tests: schema, idempotency, cancellation, immutable terminal states, refusal of unsupported mechanisms, `null` with a reason; deterministic backends are also tested for reproducibility with the same input and seed. A backend that passes counts as integrated, and switching backends changes only configuration.

## 8. Configuration and later fields

- The thresholds, traces and quality tables of the three tasks are all `draft`, defined in each `task.yaml`.
- Additions on run failures and metric definitions are in `docs/plan.md` section 5.5.
- The fields added for the two flywheels (backend role, per-role budget, measurement batches, simulation–measurement pairs, and a measurement's environment snapshot and effective configuration) are in `docs/plan.md` section 5.6.
- The fields of `manifest.environment`, and how a simulator states in `manifest.json` which calibration it used, are defined together with the corresponding backends.
