# Paper samples

The files here show every field of the contract, for reference when implementing `mlab` and the backends.

**All numbers are invented**; every `result.*.json` is marked with the `paper-sample` backend.

| Directory | What it shows |
| --- | --- |
| `9.1-edge/` | `oom` when a configuration does not fit (OOM is itself a definite answer); when every performance constraint is met but quality is not evaluated, `verdict` can only be `not_evaluated`; it becomes `constraints_met` once quality is found in the quality table |
| `9.2-sla/` | A run that completes but violates the SLA (`status=succeeded`, `verdict=constraints_violated`); three CLI outputs: unsupported mechanism, a long run that ends the turn, success |
| `9.3-dag/` | A dependency-driven workload and `makespan_s`; three candidates; a mock result must carry the placeholder notice |
| `capabilities.llm.json` | How a backend declares which mechanisms it supports |
