# Response shape examples

These are **mock protocol examples**, not results of the three course tasks and
not output from the external simulator. No performance claim is implied by the
illustrative timestamps or capacity. Every response explicitly says `mock` and
uses `EXAMPLE_ONLY` model/catalog identities.

Use [schema-dag.request.json](schema-dag.request.json) for all three responses:

- [Success](schema-dag.response-success.json): parent ends at 10 ms, a 100 ms
  dependency gap releases the child at 110 ms, and the child ends at 120 ms.
- [Blocked dependency](schema-dag.response-blocked.json): parent fails, child
  remains in the result with null activity times and names the failed parent.
- [Unknown observations](schema-dag.response-unknown.json): collection failure
  retains both IDs while leaving unknown counts/times null instead of inventing
  zero output or success.

The three `task-9.x.request.json` files remain exact exports of the existing task
baselines and workload rows. Their model aliases and `catalog_version` are still
pending agreement. Structural validation does not establish server support.

Regenerate the materials from the lab repository root:

```bash
.venv/bin/python scripts/export_simulator_requests.py
.venv/bin/python scripts/export_simulator_contract.py
.venv/bin/python -m pytest -q tests/test_simulator_data_contract.py
```

The JSON Schemas check field shapes. The negotiation-only `contract.py` adds
cross-field checks for identities, placement, output completeness and causal
release times. It contains no performance prediction or scheduler implementation.
