"""Negotiation examples preserve course data and reject misleading observations."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import sys

import pytest

from mlab.core.io import ROOT, load
from mlab.core.workspace import TASKS


DIRECTORY = ROOT / "integrations/mlsys-simulator"
spec = importlib.util.spec_from_file_location("tested_simulator_contract", DIRECTORY / "contract.py")
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)


def pair(kind="success"):
    raw = (DIRECTORY / "examples/schema-dag.request.json").read_bytes()
    return raw, load(DIRECTORY / f"examples/schema-dag.response-{kind}.json")


def rebind(request, response):
    raw = json.dumps(request, allow_nan=False).encode()
    response["input_sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    return raw, response


@pytest.mark.parametrize("task", TASKS)
def test_existing_task_rows_and_choices_are_unchanged(task):
    payload = load(DIRECTORY / f"examples/task-{task}.request.json")
    parsed = contract.RunRequest.model_validate(payload)
    folder = ROOT / "tasks" / TASKS[task]
    definition = load(folder / "task.yaml")
    reference = definition["frozen"]["workload_requests"]
    original = (folder / reference["path"]).read_bytes()
    assert payload["workload"]["requests"] == [json.loads(line) for line in original.splitlines() if line]
    assert payload["workload"]["source_sha256"] == "sha256:" + hashlib.sha256(original).hexdigest()
    assert parsed.execution.max_simulated_ms == definition["duration_s"] * 1000
    baseline = load(folder / "baselines/baseline.yaml")
    assert parsed.strategy.model_dump() == contract.Strategy.model_validate(baseline["strategy"]).model_dump()
    assert parsed.catalog_version == "to-be-agreed"
    assert not any(key in json.dumps(payload) for key in ("hidden_size", "compute_tflops", "profile_path"))


@pytest.mark.parametrize("kind", ["success", "blocked", "unknown"])
def test_examples_are_explicit_mock_data_with_complete_rows(kind):
    raw, response = pair(kind)
    result = contract.check_response(raw, response)
    assert result.evidence_level == "mock"
    assert {row.request_id for row in result.requests} == {"parent", "child"}
    assert result.calibration.in_domain is None


def test_relative_gap_is_not_an_absolute_release_time():
    raw, response = pair()
    response["requests"][1].update(ready_ms=100, started_ms=100, first_token_ms=105, finished_ms=110)
    with pytest.raises(ValueError, match="release rule"):
        contract.check_response(raw, response)


def test_sequential_failure_releases_next_request():
    raw, response = pair()
    request = json.loads(raw)
    request["workload"]["mode"] = "sequential"
    for row in request["workload"]["requests"]:
        row.update(arrival_ms=None, depends_on=[], gap_after_deps_ms=0)
    response["requests"][0].update(status="timed_out", output_tokens=1, failure_code="REQUEST_TIMEOUT")
    response["requests"][1].update(ready_ms=10, started_ms=10, first_token_ms=15, finished_ms=20)
    contract.check_response(*rebind(request, response))


def test_open_loop_uses_declared_arrivals_not_prior_completion():
    raw, response = pair()
    request = json.loads(raw)
    request["workload"]["mode"] = "open_loop"
    request["workload"]["requests"][1].update(arrival_ms=2, depends_on=[], gap_after_deps_ms=0)
    response["requests"][1]["ready_ms"] = 2
    raw, response = rebind(request, response)
    contract.check_response(raw, response)
    response["requests"][1]["ready_ms"] = 10
    with pytest.raises(ValueError, match="release rule"):
        contract.check_response(raw, response)


@pytest.mark.parametrize("change", ["missing_row", "duplicate_row", "partial_completed", "wrong_device", "wrong_hash", "after_deadline", "changed_config", "wrong_gap_cause"])
def test_invalid_results_are_rejected(change):
    raw, response = pair("blocked" if change == "wrong_gap_cause" else "success")
    if change == "missing_row":
        response["requests"].pop()
    elif change == "duplicate_row":
        response["requests"].append(deepcopy(response["requests"][0]))
    elif change == "partial_completed":
        response["requests"][0]["output_tokens"] = 1
    elif change == "wrong_device":
        response["requests"][0]["device_ids"] = ["gpu9"]
    elif change == "wrong_hash":
        response["input_sha256"] = "sha256:" + "0" * 64
    elif change == "after_deadline":
        response["requests"][1]["finished_ms"] = 1001
    elif change == "changed_config":
        response["effective_config"]["strategy"]["deployments"][0]["weight_precision"] = "FP8"
    else:
        response["requests"][1]["blocked_by"] = ["child"]
    with pytest.raises(ValueError):
        contract.check_response(raw, response)


def test_unknown_observations_cannot_be_succeeded():
    raw, response = pair("unknown")
    response.update(status="succeeded", effective_config=load(DIRECTORY / "examples/schema-dag.response-success.json")["effective_config"])
    with pytest.raises(ValueError, match="Lost observations"):
        contract.check_response(raw, response)


def test_nullable_diagnostics_require_reasons_and_not_fake_zeros():
    raw, response = pair()
    for row in response["requests"]:
        row["service_ms"] = None
    with pytest.raises(ValueError, match="explicit reason"):
        contract.check_response(raw, response)
    response["unsupported_fields"]["requests.service_ms"] = "Service attribution is not exposed by this provider."
    contract.check_response(raw, response)


def test_single_output_has_one_token_time_and_deadline_completion_is_valid():
    raw, response = pair()
    request = json.loads(raw)
    request["workload"]["requests"][1]["output_tokens_target"] = 1
    request["execution"]["max_simulated_ms"] = 120
    response["requests"][1].update(output_tokens=1, output_tokens_target=1, first_token_ms=120)
    contract.check_response(*rebind(request, response))


def test_numeric_strings_are_not_silently_coerced():
    raw, response = pair()
    request = json.loads(raw)
    request["strategy"]["deployments"][0]["replicas"] = "1"
    with pytest.raises(ValueError):
        contract.check_response(*rebind(request, response))


def test_cycles_and_internal_model_parameters_are_rejected():
    raw, _ = pair()
    request = json.loads(raw)
    request["workload"]["requests"][0].update(arrival_ms=None, depends_on=["child"])
    with pytest.raises(ValueError, match="cycle"):
        contract.RunRequest.model_validate(request)
    request = json.loads(raw)
    request["strategy"]["deployments"][0]["hidden_size"] = 4096
    with pytest.raises(ValueError):
        contract.RunRequest.model_validate(request)


@pytest.mark.parametrize("name,model", [("request", contract.RunRequest), ("response", contract.RunResponse)])
def test_published_schemas_match_the_contract(name, model):
    expected = model.model_json_schema(by_alias=True)
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    assert load(DIRECTORY / f"schemas/{name}.schema.json") == expected
