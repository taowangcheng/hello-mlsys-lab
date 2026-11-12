# 9.3 Deep-research DAG cluster: task guide

## Goal

Given a frozen deep-research trace (several subagents whose requests go to a 32B or a 7B model and depend on each other), change only the infra strategy so that all requests complete and the total wall-clock time (`makespan_s`) is as short as possible. Request contents cannot change.

## Teaching decision points

- Run the 32B model with TP=2 across two GPUs, or in FP8 or INT4 on one GPU and give the freed GPU to more 7B replicas?
- Where is the critical path? `critical_path_s` is the lower bound on total wall-clock time; queueing pushes the actual wall-clock time above it.
- Compare at least 3 candidates; simulator predictions are frozen before hardware measurement.

## Starting candidates

- `tp2_bf16`: 32B BF16 with TP across two GPUs and 7B as two single-GPU replicas; checks whether communication overhead cancels out the gain.
- `fp8_single`: 32B FP8 on one GPU, which frees a GPU for one more 7B replica.
- `int4_single`: 32B INT4 on one GPU, with the most KV headroom, but dequantization may slow the critical path.

## Key metrics and charts to check

- `makespan_s` (key metric), `critical_path_s`, `completion_rate`.
- A run's request timeline has one lane per model and replica and marks the critical path; look for the chain that is waiting.
- The holdouts change one configuration dimension (concurrency halved) and add a second DAG from the same distribution, to guard against tuning for a single trace.

## Common discrepancies

- Cross-GPU communication overhead, scheduling overhead, KV preemption and swapping, and clock throttling all make measured wall-clock time longer than simulated.
- When real hardware can measure only part of the configuration, mark the rest as extrapolated; it is not a measured conclusion.
