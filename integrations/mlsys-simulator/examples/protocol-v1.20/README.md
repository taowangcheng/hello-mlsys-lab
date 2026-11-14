# Explicit protocol test configurations

These requests are copied byte-for-byte from the simulator team's published
1.20 examples at commit `feda6677b90a6f50f35ac78c1d5eabff986ed5b7`.
[sources.json](sources.json) records the source, hashes and every field changed
relative to the original course baselines.

- [9.1](task-9.1.protocol.request.json): Qwen3-1.7B/BF16 on one RX9070XT.
- [9.2](task-9.2.protocol.request.json): Qwen3-1.7B/BF16 on one RX9070XT.
- [9.3](task-9.3.protocol.request.json): Qwen3-4B and Qwen3-0.6B/BF16 on four RX9070XT devices.

Request IDs, lengths, arrivals and dependency gaps are retained. Model references
change explicitly with the selected deployments. These are not results for the
original 3B/7B/32B, FP8 or W7900D course configurations. Their source-workload
hashes record provenance; response input hashes bind the actual transmitted bytes.

See the [independent HTTP and lab-client verification](../../REVIEW-v1.20.zh-CN.md).
