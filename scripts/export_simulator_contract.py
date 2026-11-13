#!/usr/bin/env python3
"""Export the proposed data schemas and explicitly mock protocol examples."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from mlab.core.io import ROOT


DIRECTORY = ROOT / "integrations/mlsys-simulator"
spec = importlib.util.spec_from_file_location("simulator_data_contract", DIRECTORY / "contract.py")
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def main():
    for name, model in (("request", contract.RunRequest), ("response", contract.RunResponse)):
        schema = model.model_json_schema(by_alias=True)
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        write(DIRECTORY / "schemas" / (name + ".schema.json"), schema)
    rows = [
        {"request_id": "parent", "input_tokens": 16, "output_tokens_target": 2, "arrival_ms": 0},
        {"request_id": "child", "input_tokens": 32, "output_tokens_target": 2,
         "depends_on": ["parent"], "gap_after_deps_ms": 100},
    ]
    source = "".join(json.dumps(row) + "\n" for row in rows).encode()
    examples = DIRECTORY / "examples"
    (examples / "schema-dag.source.jsonl").write_bytes(source)
    request = contract.RunRequest.model_validate({
        "schema": "mlab.simulator.v1", "client_request_id": "schema-example-only",
        "catalog_version": "EXAMPLE_ONLY", "task": "schema-example", "candidate_id": "example",
        "workload": {"mode": "dag", "requests": rows, "source_sha256": "sha256:" + hashlib.sha256(source).hexdigest()},
        "infra": {"id": "example-infra", "nodes": [{"id": "node0", "devices": [{"id": "gpu0", "hardware": "example-hardware"}]}]},
        "strategy": {"deployments": [{"model": "example-model", "weight_precision": "BF16", "context_tokens": 1024, "devices": ["gpu0"]}]},
        "evaluation": {"constraints": [], "duration_s": 1, "timeout_s": 30, "goodput_policy": "both_thresholds_required"},
        "execution": {"seed": 42, "repeat": 0, "split": "development", "holdout": None,
                      "initial_state": "empty", "warmup": None, "max_simulated_ms": 1000},
    }).model_dump(mode="json", by_alias=True)
    write(examples / "schema-dag.request.json", request)
    raw = (examples / "schema-dag.request.json").read_bytes()
    records = [{"request_id": name, "status": "completed", "ready_ms": start, "started_ms": start,
        "first_token_ms": start + 5, "finished_ms": start + 10, "service_ms": 10,
        "output_tokens": 2, "output_tokens_target": 2, "deployment_model": "example-model",
        "replica_index": 0, "device_ids": ["gpu0"], "failure_code": None, "blocked_by": []}
        for name, start in (("parent", 0), ("child", 110))]
    response = {"schema": "mlab.simulator.v1", "client_request_id": request["client_request_id"],
        "input_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(), "status": "succeeded", "failure_class": None,
        "error": None, "evidence_level": "mock", "backend": {"name": "schema-example-only", "version": "0"},
        "catalog_version": "EXAMPLE_ONLY", "model_revisions": {"example-model": "EXAMPLE_ONLY"},
        "calibration": {"version": None, "kind": "uncalibrated", "in_domain": None,
                        "assumptions": ["Illustrative timestamps only; no simulator or hardware was executed."]},
        "effective_config": {"infra": request["infra"], "strategy": request["strategy"]},
        "requests": records, "per_device": [{"device_id": "gpu0", "capacity_bytes": 16 * 1024**3,
            "physical_peak_bytes": None, "kv_peak_bytes": None, "kv_pool_reserved_bytes": None}],
        "unsupported_fields": {"per_device.physical_peak_bytes": "No resource measurement in a schema example.",
            "per_device.kv_peak_bytes": "No resource measurement in a schema example.",
            "per_device.kv_pool_reserved_bytes": "No resource measurement in a schema example."},
        "logs": ["Protocol example only. These are not performance predictions."]}
    blocked = deepcopy(response)
    blocked.update(status="failed", failure_class="oom", error={"code": "OUT_OF_MEMORY", "message": "Illustrative failure", "field": None})
    blocked["requests"][0].update(status="failed", output_tokens=0, first_token_ms=None, service_ms=None, failure_code="OUT_OF_MEMORY")
    blocked["requests"][1].update(status="blocked", ready_ms=None, started_ms=None, first_token_ms=None,
        finished_ms=10, service_ms=None, output_tokens=0, deployment_model=None, replica_index=None, device_ids=[],
        failure_code="DEPENDENCY_FAILED", blocked_by=["parent"])
    blocked["unsupported_fields"]["requests.service_ms"] = "No service accounting in the illustrative failure."
    unknown = deepcopy(response)
    unknown.update(status="failed", failure_class="backend_error", error={"code": "OBSERVATIONS_LOST", "message": "Illustrative collection failure", "field": None}, effective_config=None)
    for row in unknown["requests"]:
        row.update(status="unknown", ready_ms=None, started_ms=None, first_token_ms=None, finished_ms=None,
            service_ms=None, output_tokens=None, deployment_model=None, replica_index=None, device_ids=[], failure_code="OBSERVATIONS_LOST")
    for name, value in (("success", response), ("blocked", blocked), ("unknown", unknown)):
        validated = contract.check_response(raw, value)
        write(examples / f"schema-dag.response-{name}.json", validated.model_dump(mode="json", by_alias=True))
    for task in ("9.1", "9.2", "9.3"):
        contract.RunRequest.model_validate_json((examples / f"task-{task}.request.json").read_bytes())
    print("Exported request/response schemas and mock response shapes; all three task inputs validate.")


if __name__ == "__main__":
    main()
