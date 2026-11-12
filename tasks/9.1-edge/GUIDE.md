# 9.1 On-device assistant: task guide

## Goal

Treat one 16 GB GPU as a scaled-up edge device. Choose a model, weight precision and context length that meet all of these at once: VRAM at most 14 GiB, median time to first token at most 500 ms, decode at least 20 tok/s, and the quality floor. The thresholds are draft values; `task/task.yaml` is authoritative.

## Teaching decision points

- INT4 fits but is not necessarily faster: dequantization has overhead. When it is too slow, accept the lower speed, or run a smaller model in BF16 or FP8?
- Name the tightest constraint (`tightest_constraint`) instead of only checking which configuration passes.
- If quality is not in the table, the verdict is `not_evaluated`, which is not a pass.

## Starting candidates

- `fp8_3b`: switch to a smaller model and keep a higher precision.
- `bf16_7b`: the weights sit right at the 14 GiB limit; this checks the VRAM accounting.
- `int4_7b`: INT4 fits; see whether time to first token or decode speed misses its target first.

## Key metrics and charts to check

- `ttft_ms.p50` (key metric), `vram_peak_gib`, `decode_tok_s`, `tightest_constraint`.
- For a round, read the constraint matrix: for each candidate, the constraint closest to its limit.
- When comparing simulator and hardware results, check VRAM and which settings took effect first, then speed.

## Common discrepancies

- The runtime uses VRAM that the simulator does not count, so a configuration close to the limit may not fit on hardware.
- INT4 dequantization overhead makes decode slower than expected.
- The holdout uses another prompt set with a different length distribution, so the tightest constraint may change.
