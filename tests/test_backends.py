"""The same public Backend contract tests run against fixture and fake-API LLM."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from pydantic import ValidationError
from mlab.backends.local import LocalBackend, alive, fixture
from mlab.contracts import RunResult
from mlab.core.io import LabError, atomic, hashed, load
from mlab.core.workspace import Lab, initialize


@pytest.fixture(params=["fixture", "llm", "standin"])
def backend_context(request, workspace_path, monkeypatch):
    name = request.param
    selected = request.config.getoption("--backend")
    if selected != "all" and selected != name:
        pytest.skip("backend filter")
    server = None
    if name == "llm":
        class API(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                data = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])
                answer = fixture(data["spec"], data["requests"], 4.3)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": {"content": json.dumps(answer)}}]}).encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), API)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        monkeypatch.setenv("MLAB_LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setenv("MLAB_LLM_API_KEY", "fake-contract-key")
        monkeypatch.setenv("MLAB_LLM_MODEL", "fake-contract-model")
        monkeypatch.setenv("MLAB_LLM_PROTOCOL", "openai")
    initialize(workspace_path, "9.1", "fixture" if name == "standin" else name)
    lab = Lab(workspace_path)
    lab.new_workflow(example=True)
    atomic(lab.folder() / "hypothesis.md", "## Hypothesis\n\nScripted backend conformance check\n\n## What result would prove me wrong\n\n-\n", text=True)
    _, candidates, _ = lab.specifications()
    spec = lab.make_spec(next(c for c in candidates if c["id"] == "fp8_3b"), 42, 0, "development")
    try:
        yield lab, LocalBackend(workspace_path, name), spec
    finally:
        for run in lab.store.all("run"):
            if run["status"] not in {"succeeded", "failed", "cancelled", "timed_out"}:
                LocalBackend(workspace_path, name).cancel({"run_id": run["id"]})
        # A worker still waiting out its delay would write the ledger again after the workspace is removed.
        pids = [run["pid"] for run in lab.store.all("run") if run.get("pid")]
        deadline = time.monotonic() + 15
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.1)
        lab.close()
        if server:
            server.shutdown()
            server.server_close()


def finish(backend, handle):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = backend.poll(handle)
        if state["status"] in {"succeeded", "failed", "cancelled", "timed_out"}:
            return state
        time.sleep(.05)
    raise AssertionError("backend did not terminate")


def test_public_submit_idempotent_fetch_schema_and_null(backend_context):
    lab, backend, spec = backend_context
    assert not lab.store.all("run")
    handle = backend.submit(spec, "contract-key")
    assert backend.submit(spec, "contract-key") == handle
    state = finish(backend, handle)
    assert state["status"] == "succeeded"
    artifacts = backend.fetch(handle, lab.root / "fetched")
    assert set(artifacts) == {"manifest.json", "result.json", "requests.jsonl", "events.jsonl", "logs.txt"}
    result = RunResult.model_validate(load(lab.root / "fetched/result.json"))
    assert result.evidence_level == "mock"
    assert "energy_j_per_req" in result.metrics and result.metrics["energy_j_per_req"] is None
    assert result.unsupported_metrics["energy_j_per_req"]
    changed = json.loads(json.dumps(spec))
    changed["execution"]["seed"] += 1
    with pytest.raises(LabError) as error:
        backend.submit(changed, "contract-key")
    assert error.value.code == "E_FROZEN_CHANGED"


def test_unsupported_and_unknown_fields_rejected_before_submit(backend_context):
    lab, backend, spec = backend_context
    spec["strategy"]["speculative"] = True
    with pytest.raises(LabError) as error:
        backend.submit(spec, "unsupported")
    assert error.value.code == "E_MECHANISM_UNSUPPORTED" and not lab.store.all("run")
    spec["strategy"]["speculative"] = False
    spec["calibration"] = {"version": "v002"}  # The simulator brings its own calibration; a spec cannot carry one.
    with pytest.raises(ValidationError):
        backend.submit(spec, "calibration")
    assert not lab.store.all("run")


def test_cancel_keeps_terminal_artifacts_immutable(backend_context):
    lab, backend, spec = backend_context
    config = load(lab.root / ".mlab/config.json")
    config["fixture_delay_s"] = 2
    atomic(lab.root / ".mlab/config.json", config)
    handle = backend.submit(spec, "cancel-key")
    backend.cancel(handle)
    folder = lab.root / backend.poll(handle)["folder"]
    before = hashed(folder / "result.json")
    backend.cancel(handle)
    assert backend.poll(handle)["status"] == "cancelled"
    assert hashed(folder / "result.json") == before


def test_fetch_rejects_mutated_manifest(backend_context):
    lab, backend, spec = backend_context
    handle = backend.submit(spec, "tamper-key")
    state = finish(backend, handle)
    path = lab.root / state["folder"] / "manifest.json"
    data = load(path)
    data["provenance"]["tampered"] = True
    atomic(path, data)
    with pytest.raises(LabError) as error:
        backend.fetch(handle, lab.root / "fetched")
    assert error.value.code == "E_FROZEN_CHANGED"


def test_fixture_capabilities_do_not_claim_ignored_options(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    lab = Lab(workspace_path)
    try:
        lab.new_workflow(example=True)
        atomic(lab.folder() / "hypothesis.md", "## Hypothesis\n\nScripted fixture mechanism gate\n\n## What result would prove me wrong\n\n-\n", text=True)
        path = lab.folder() / "candidates/baseline.yaml"
        candidate = load(path)
        candidate["strategy"]["serving"]["kv_fraction"] = .5
        atomic(path, candidate)
        with pytest.raises(LabError) as error:
            lab.check()
        assert error.value.code == "E_MECHANISM_UNSUPPORTED"
    finally:
        lab.close()
