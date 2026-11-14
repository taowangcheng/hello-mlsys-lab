"""Independent HTTP captures remain valid through the lab's normal result adapter."""
import hashlib
import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from mlab.backends.simulator_http import (
    SimulatorAPIError, SimulatorHTTPClient, SimulatorProtocolError, SimulatorTransportError, adapt_response, decode_response,
)
from mlab.contracts import RequestObservation
from mlab.contracts.simulator import check_response
from mlab.core.io import ROOT
from mlab.core.metrics import calculate


DIRECTORY = ROOT / "integrations/mlsys-simulator"
EVIDENCE = DIRECTORY / "evidence/agent-v1.20"
REPORT = json.loads((EVIDENCE / "report.json").read_text())


def pair(name):
    raw = (EVIDENCE / "requests" / f"{name}.json").read_bytes()
    payload = json.loads((EVIDENCE / "responses" / f"{name}.json").read_text())
    return raw, payload


@pytest.mark.parametrize("case", REPORT["cases"], ids=lambda case: case["name"])
def test_saved_http_evidence_is_complete_and_valid(case):
    assert case["passed"] and not case["failures"]
    raw_response = (EVIDENCE / case["response"]).read_bytes()
    assert case["response_sha256"] == "sha256:" + hashlib.sha256(raw_response).hexdigest()
    if case["request"]:
        raw = (EVIDENCE / case["request"]).read_bytes()
        assert case["request_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        if case.get("source"):
            assert json.loads(raw)["workload"]["source_sha256"] == "sha256:" + hashlib.sha256((EVIDENCE / case["source"]).read_bytes()).hexdigest()
        if case["http_status"] == 200:
            check_response(raw, json.loads(raw_response))
            adapted = adapt_response(raw, json.loads(raw_response))
            assert len(adapted["observations"]) == len(json.loads(raw)["workload"]["requests"])
            assert adapted["evidence_level"] == "simulated"


@pytest.mark.parametrize("task,count", [("9.1", 6), ("9.2", 12), ("9.3", 10)])
def test_three_workloads_have_computable_metrics_without_fake_service_times(task, count):
    raw, response = pair(f"task-{task}-protocol")
    result = adapt_response(raw, response)
    assert result["requests"] == {"total": count, "completed": count, "failed": 0}
    assert result["metrics"]["completion_rate"] == 1
    assert result["metrics"]["ttft_ms"]["p50"] is not None
    assert result["metrics"]["throughput_tok_s"] == sum(row["output_tokens"] for row in response["requests"]) / json.loads(raw)["evaluation"]["duration_s"]
    assert result["metrics"]["critical_path_s"] is None
    assert result["metrics"]["makespan_s"] > 0
    assert "critical_path_s" in result["unsupported_metrics"]
    assert all(row["service_ms"] is None for row in result["observations"])
    assert result["coverage"]["in_domain"] is None
    assert result["calibration"]["kind"] == "uncalibrated"


def test_blocked_rows_and_initialization_failure_are_not_filled_with_zero_times():
    result = adapt_response(*pair("dag-request-failure"))
    child = next(row for row in result["observations"] if row["request_id"] == "child")
    assert child["status"] == "blocked" and child["ready_ms"] is None
    assert child["finished_ms"] is None and child["blocked_by"] == ["parent"]
    assert result["metrics"]["completion_rate"] == 1 / 3
    assert result["failure_class"] == "request_failure"
    oom = adapt_response(*pair("deployment-oom"))
    assert oom["failure_class"] == "oom" and oom["native_failure_class"] == "modeled_oom"
    assert oom["metrics"]["vram_peak_gib"] is None
    assert all(row["ready_ms"] is None for row in oom["observations"])


def test_unknown_observations_do_not_become_failed_counts_or_zero_throughput():
    row = RequestObservation(request_id="unknown", ready_ms=None, first_token_ms=None, finished_ms=None,
                             output_tokens=None, output_tokens_target=2, status="unknown", service_ms=None).model_dump()
    metrics, counts, notes = calculate([row], {"duration_s": 1, "constraints": []})
    assert counts == {"total": 1, "completed": 0, "failed": 0, "unknown": 1}
    assert metrics["completion_rate"] is None and metrics["throughput_tok_s"] is None
    assert metrics["ttft_ms"]["p50"] is None and metrics["critical_path_s"] is None
    assert any("unknown" in note for note in notes)


def test_metrics_accept_unordered_dependencies_and_known_service_times():
    parent = RequestObservation(request_id="parent", ready_ms=0, first_token_ms=2, finished_ms=10,
        output_tokens=2, output_tokens_target=2, status="completed", service_ms=10).model_dump()
    child = RequestObservation(request_id="child", ready_ms=110, first_token_ms=115, finished_ms=120,
        output_tokens=2, output_tokens_target=2, status="completed", service_ms=10,
        depends_on=["parent"], gap_after_deps_ms=100).model_dump()
    metrics, _, _ = calculate([child, parent], {"duration_s": 1, "constraints": []})
    assert metrics["critical_path_s"] == metrics["makespan_s"] == 0.12


def test_device_peaks_are_preserved_without_double_counting_pool_reservations():
    raw, response = pair("task-9.3-protocol")
    adapted = adapt_response(raw, response)
    assert adapted["metrics"]["vram_peak_gib"] == max(row["physical_peak_bytes"] for row in response["per_device"]) / 2**30
    assert adapted["metrics"]["per_device"] == response["per_device"]
    for original, row in zip(response["requests"], adapted["observations"]):
        assert row["device_ids"] == original["device_ids"]
        assert row["replica_index"] == original["replica_index"]


def test_wire_hash_and_evidence_are_checked_before_adaptation():
    raw, response = pair("task-9.2-protocol")
    with pytest.raises(ValueError, match="input bytes"):
        adapt_response(raw + b"\n", response)
    response["evidence_level"] = "mock"
    with pytest.raises(ValueError, match="real simulated"):
        adapt_response(raw, response)


def test_busy_and_network_failures_are_not_retried_or_replaced_with_mock_results():
    raw, _ = pair("task-9.2-protocol")
    client = SimulatorHTTPClient(timeout_s=1)
    opener = Mock()
    client._opener = opener
    opener.open.side_effect = HTTPError("http://127.0.0.1:8877/v1/simulate", 429, "Busy",
        {"Retry-After": "1"}, io.BytesIO(b'{"error":{"code":"AGENT_BUSY","message":"Busy"}}'))
    with pytest.raises(SimulatorAPIError) as found:
        client.simulate(raw)
    assert found.value.status == 429 and found.value.headers["retry-after"] == "1"
    assert opener.open.call_count == 1
    opener.reset_mock()
    opener.open.side_effect = URLError("Connection lost")
    with pytest.raises(SimulatorTransportError, match="unknown"):
        client.simulate(raw)
    assert opener.open.call_count == 1
    assert opener.open.call_args.args[0].data is raw


def test_invalid_service_reply_is_preserved_for_attribution():
    raw, payload = pair("task-9.2-protocol")
    payload["input_sha256"] = "sha256:" + "0" * 64
    body = json.dumps(payload).encode()
    response = io.BytesIO(body)
    response.status, response.headers = 200, {"Content-Type": "application/json"}
    client = SimulatorHTTPClient()
    client._opener = Mock()
    client._opener.open.return_value = response
    with pytest.raises(SimulatorProtocolError, match="input bytes") as found:
        client.simulate(raw)
    assert found.value.raw == body
    assert client._opener.open.call_count == 1


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'])
def test_invalid_response_json_is_rejected(raw):
    with pytest.raises(ValueError):
        decode_response(raw)


@pytest.mark.parametrize("task", ["9.1", "9.2", "9.3"])
def test_protocol_variants_only_change_declared_configuration_fields(task):
    variants = DIRECTORY / "examples/protocol-v1.20"
    sources = json.loads((variants / "sources.json").read_text())
    record = next(record for record in sources["records"] if record["task"] == task)
    raw = (variants / record["file"]).read_bytes()
    assert record["sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    original = json.loads((DIRECTORY / "examples" / f"task-{task}.request.json").read_text())
    protocol = json.loads(raw)
    for a, b in zip(original["workload"]["requests"], protocol["workload"]["requests"], strict=True):
        assert {key: value for key, value in a.items() if key != "target_model"} == {key: value for key, value in b.items() if key != "target_model"}
    assert original["workload"]["source_sha256"] == protocol["workload"]["source_sha256"]
    assert record["changes_from_course_baseline"]
