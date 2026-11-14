"""Exercise the public HTTP adapter through normal workbench execution records."""
import hashlib
import json

import pytest

from mlab.backends import local, simulator_backend
from mlab.backends.simulator_http import SimulatorTransportError, adapt_response
from mlab.core.io import ROOT, LabError, hashed, load
from mlab.core.workspace import Lab
from mlab.web import exploration
from mlab.web.course import Course
from mlab.worker import work


def setup(tmp_path, monkeypatch, task="9.2"):
    root = tmp_path / "course"
    course = Course(root)
    course.ensure("mlsys", simulator_profile="protocol-v1.20")
    session = course.create(task)
    workspace = root / session
    view = exploration.task_view(course, task)
    preferred = {"9.1": "bf16_1_7b", "9.2": "baseline", "9.3": "replica_3"}[task]
    candidate = next(item for item in view["templates"] if item["id"] == preferred)
    experiment = exploration.action(workspace, "create", {"title": "Public API exercise", "question": "What does the evidence support?",
        "candidates": [candidate], "seeds": [42], "workloads": ["development"]})
    monkeypatch.setattr(local, "launch", lambda *args: 123456789)
    exploration.action(workspace, "simulate", {"workflow": experiment["id"], "revision": experiment["revision"], "confirm": True})
    return workspace


@pytest.mark.parametrize("task,count", [("9.1", 6), ("9.2", 12), ("9.3", 10)])
def test_profile_worker_preserves_http_evidence_and_unknown_metrics(tmp_path, monkeypatch, task, count):
    source = ROOT / "tasks"
    before = {str(path): hashed(path) for path in source.rglob("*") if path.is_file()}
    root = setup(tmp_path, monkeypatch, task)
    captured = []

    def simulate(client, raw):
        request = json.loads(raw)
        captured.append(request)
        response = load(ROOT / "integrations/mlsys-simulator/evidence/agent-v1.20/responses" / f"task-{task}-protocol.json")
        response.update(input_sha256="sha256:" + hashlib.sha256(raw).hexdigest(), client_request_id=request["client_request_id"])
        response["effective_config"] = {"infra": request["infra"], "strategy": request["strategy"]}
        return {"result": adapt_response(raw, response), "response_bytes": json.dumps(response).encode(), "headers": {}}

    monkeypatch.setattr(simulator_backend.SimulatorHTTPClient, "simulate", simulate)
    monkeypatch.setattr("mlab.worker.memory", lambda *args: pytest.fail("HTTP simulation must not use the mock memory model"))
    work(root, "sim_0001")
    lab = Lab(root)
    try:
        result, spec = lab.result("sim_0001")
        assert result["status"] == "succeeded"
        assert result["evidence_level"] == "simulated"
        assert result["backend"]["version"] == "1.20.0"
        assert result["requests"]["completed"] == count
        assert result["coverage"]["in_domain"] is None
        from mlab.web.views import run_requests
        timeline = run_requests(lab, "sim_0001")
        assert timeline["path_kind"] == "completion_chain"
        assert len(timeline["rows"]) == count
        assert result["metrics"]["critical_path_s"] is None
        assert result["quality"]["status"] == "not_evaluated"
        run = lab.store.get("run", "sim_0001")
        folder = root / run["folder"]
        manifest = load(folder / "manifest.json")
        assert manifest["provenance"]["calibration"]["kind"] == "uncalibrated"
        assert manifest["effective_config"] == spec["strategy"]
        for name in ("simulator-request.json", "simulator-response.json"):
            assert manifest["sha256"][name] == hashed(folder / name)
        assert all(item["model"].startswith("qwen3-") for item in captured[0]["strategy"]["deployments"])
        artifacts = local.LocalBackend(root, "mlsys").fetch({"run_id": run["id"]}, tmp_path / "export")
        assert "simulator-response.json" in artifacts
        (folder / "simulator-response.json").write_text("{}")
        with pytest.raises(LabError, match="artifact changed"):
            lab.result(run["id"])
    finally:
        lab.close()
    assert before == {str(path): hashed(path) for path in source.rglob("*") if path.is_file()}


def test_lost_response_is_unknown_and_not_retried(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    attempts = []

    def unavailable(client, raw):
        attempts.append(raw)
        raise SimulatorTransportError("Remote execution is unknown; no retry was attempted.")

    monkeypatch.setattr(simulator_backend.SimulatorHTTPClient, "simulate", unavailable)
    work(root, "sim_0001")
    lab = Lab(root)
    try:
        result, _ = lab.result("sim_0001")
        assert len(attempts) == 1 and result["failure_class"] == "lost"
        assert result["requests"] == {"total": 12, "completed": 0, "failed": 0, "unknown": 12}
        assert result["metrics"]["completion_rate"] is None
    finally:
        lab.close()


def test_cancelled_run_does_not_dispatch_or_accept_late_evidence(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    backend = local.LocalBackend(root, "mlsys")
    backend.cancel({"run_id": "sim_0001"})
    monkeypatch.setattr(simulator_backend.SimulatorHTTPClient, "simulate", lambda *args: pytest.fail("Cancelled work was dispatched"))
    work(root, "sim_0001")
    run = backend.poll({"run_id": "sim_0001"})
    folder = root / run["folder"]
    before = {path.name: hashed(path) for path in folder.iterdir()}
    local.finalize(root, run["id"], "succeeded", None, [], {}, [], {}, wire={"simulator-response.json": b"{}"})
    assert before == {path.name: hashed(path) for path in folder.iterdir()}
    assert "remote execution may continue" in (folder / "logs.txt").read_text()


def test_original_course_models_are_not_silently_replaced(tmp_path):
    course = Course(tmp_path / "course")
    course.ensure("mlsys")
    root = course.root / course.create("9.1")
    lab = Lab(root)
    try:
        baseline = load(root / "task/baselines/baseline.yaml")
        from mlab.contracts import Candidate
        from mlab.core.io import dump
        spec = lab.make_spec(dump(Candidate.model_validate(baseline)), 42, 0, "development")
        with pytest.raises(LabError, match="No explicit simulator catalog identity"):
            simulator_backend.request_bytes(root, spec, "unsupported", lab.config["simulator_service"])
    finally:
        lab.close()


def test_unreleased_dag_rows_have_no_fabricated_timeline(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch, "9.3")
    lab = Lab(root)
    try:
        run = lab.store.get("run", "sim_0001")
        spec = load(root / run["folder"] / "spec.json")
        requests = local.load_requests(root, spec)
        from mlab.contracts import RequestObservation
        rows = [RequestObservation(request_id=row["request_id"], ready_ms=None, first_token_ms=None,
            finished_ms=None, output_tokens=0, output_tokens_target=row["output_tokens_target"],
            status="not_started", service_ms=None, depends_on=row.get("depends_on", [])).model_dump() for row in requests]
        local.finalize(root, run["id"], "failed", "oom", rows, {}, ["Test deployment OOM"], {})
        from mlab.web.views import run_requests
        timeline = run_requests(lab, run["id"])
        assert timeline["critical_path"] == []
        assert all(row["ready"] is row["finish"] is row["ttft"] is None for row in timeline["rows"])
    finally:
        lab.close()


def test_modeled_request_failure_can_be_checked_on_sandbox_after_confirmation(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    lab = Lab(root)
    try:
        run = lab.store.get("run", "sim_0001")
        spec = load(root / run["folder"] / "spec.json")
        from mlab.contracts import RequestObservation
        rows = [RequestObservation(request_id=row["request_id"], ready_ms=None, first_token_ms=None,
            finished_ms=None, output_tokens=0, output_tokens_target=row["output_tokens_target"],
            status="not_started", service_ms=None).model_dump() for row in local.load_requests(root, spec)]
        local.finalize(root, run["id"], "failed", "request_failure", rows, {}, ["MEMORY_NO_PROGRESS"], {})
        experiment = exploration.experiment(lab, lab.store.get("workflow", "r001"), root.name)
    finally:
        lab.close()
    preview = exploration.action(root, "prepare_measurement", {"workflow":"r001", "revision":experiment["revision"],
        "confirm_config":True, "points":[{"workflow":"r001", "candidate":"baseline", "workload":"development", "runs":1,
                                        "rationale":"Check the modeled failure without changing this configuration"}]})
    assert preview["points"][0]["sim_status"] == "failed"
    assert preview["points"][0]["sim_failure_class"] == "request_failure"
    assert preview["points"][0]["simulated"] is None
    assert preview["hardware_budget"]["used"] == 0
    summary = exploration.execution_summary(root, preview["id"])
    assert summary["points"][0]["sim_failure_class"] == "request_failure"
    executed = exploration.action(root, "measure", {"measurement":preview["id"], "revision":preview["plan_hash"], "confirm":True})
    work(root, executed["run_ids"][0])
    lab = Lab(root)
    try:
        assert len([run for run in lab.store.all("run") if run["role"] == "simulator"]) == 1
        result, _ = lab.result("hw_0001")
        assert result["status"] == "succeeded" and result["evidence_level"] == "mock"
        aligned = lab.alignment(lab.store.get("measurement", preview["id"]))
        assert aligned["rows"][0]["simulated"] is None
        assert aligned["rows"][0]["gap_pct"] is None
    finally:
        lab.close()


def test_terminal_unknown_prediction_does_not_loop_as_simulating(tmp_path, monkeypatch):
    root = setup(tmp_path, monkeypatch)
    local.finalize(root, "sim_0001", "failed", "lost", [], {}, ["Unknown response"], {})
    lab = Lab(root)
    try:
        experiment = exploration.experiment(lab, lab.store.get("workflow", "r001"), root.name)
    finally:
        lab.close()
    preview = exploration.action(root, "prepare_measurement", {"workflow":"r001", "revision":experiment["revision"],
        "confirm_config":True, "points":[{"workflow":"r001", "candidate":"baseline", "workload":"development", "runs":1,
                                        "rationale":"Inspect the interrupted prediction"}]})
    local.finalize(root, preview["run_ids"][0], "failed", "lost", [], {}, ["Unknown response"], {})
    with pytest.raises(LabError, match="Simulator prediction is unavailable"):
        exploration.action(root, "check_measurement", {"measurement":preview["id"]})


def test_validation_matrix_distinguishes_unassessed_incomplete_and_violated():
    from types import SimpleNamespace
    from mlab.core.configs import validation
    lab = SimpleNamespace(task={"holdouts":["long"]})
    rows = [dict(role="simulator", split="development", holdout=None, status="succeeded", verdict="not_evaluated"),
            dict(role="simulator", split="repeat", holdout=None, status="succeeded", verdict="constraints_met"),
            dict(role="simulator", split="holdout", holdout="long", status="failed", verdict="not_evaluated")]
    matrix, missing = validation(lab, rows)
    assert matrix["simulator"] == {"development":"not_evaluated", "repeat":True, "holdout":{"long":"incomplete"}}
    assert missing["simulator"] == ["development", "holdout:long"]
    rows.append(dict(role="simulator", split="development", holdout=None, status="succeeded", verdict="constraints_violated"))
    assert validation(lab, rows)[0]["simulator"]["development"] is False
