# 9.2 Single-node serving under an SLA: task guide

## Goal

Replay a fixed bursty trace of mixed long and short requests on one 9070 XT. Keep p99 time to first token below 2000 ms and the completion rate at or above 99%, and within those limits make goodput as high as possible. The thresholds are draft values; `task/task.yaml` is authoritative.

## Teaching decision points

- A better median does not mean the SLA is met; look at p99.
- When the measured knee is lower than predicted, what explains it: KV preemption, scheduling overhead or clock throttling?
- Measure a candidate with a small simulator margin on hardware before deciding; the final choice needs margin.

## Starting candidates

- `baseline`: the control, max concurrency 32, no chunked prefill.
- `chunked_256`: changes only chunked prefill, to test "long prefills block decode".
- `low_concurrency`: only lowers max concurrency to 8, to test "overall overload".

## Key metrics and charts to check

- `ttft_ms.p99` (key metric), `goodput_tok_s` (objective), `completion_rate`.
- In a round's bar chart, read how far p99 is from the SLA line; in the request timeline, check whether long requests block short ones.
- When comparing simulator and hardware results, read the per-request ratios: did all requests shift, or only the slowest few?

## Common discrepancies

- Measured knee below the prediction: KV preemption or swapping when VRAM runs short, continuous-batching scheduling overhead, clock throttling.
- With 12 requests, p99 is close to the maximum and varies a lot; a single run does not show run-to-run variation.
- The holdouts switch to short-prompt and long-prompt distributions to check whether the chosen max concurrency is still safe.
