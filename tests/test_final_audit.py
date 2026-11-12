"""Final-pass regressions for recovery, complete records and source fidelity."""
import asyncio
import json
import copy
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
from starlette.testclient import TestClient

from mlab.core.workspace import Lab
from mlab.core.io import atomic
from mlab.web import exploration, reading
from mlab.web.codex_client import LabTools
from mlab.web.server import create_app
from test_codex import make_runtime
from test_exploration import draft, setup, simulate


def test_report_is_referenceable_before_a_conclusion(workspace_path):
    _, root = setup(workspace_path)
    assert not exploration.session_view(root).get("report_available")
    exploration.action(root, "report", {})
    assert exploration.session_view(root)["report_available"]
    assert not exploration.session_view(root)["conclusions"]
    refs, _ = exploration.conversation_context(root, "Discuss @report", [])
    assert refs == [f"{root.name}/report"]


def test_task_context_distinguishes_numeric_ranges_from_enumerated_choices(workspace_path):
    _, root = setup(workspace_path, "9.1")
    _, context = exploration.conversation_context(root, "Which parameters can I change?", [])
    task = json.loads(context.split("student instructions):\n", 1)[1])["task"]
    assert task["decision_rules"]["deployments.context_tokens"] == {"type": "range", "min": 512, "max": 32768}
    assert task["decision_rules"]["deployments.weight_precision"]["type"] == "enum"


@pytest.mark.parametrize("status", ["queued", "failed"])
def test_pending_and_failed_runs_expose_logs_and_allow_complete_download(workspace_path, monkeypatch, status):
    from mlab.backends.local import LocalBackend
    monkeypatch.setattr(LocalBackend, "submit", lambda *args: None)
    course, root = setup(workspace_path)
    record = draft(course, root)
    run_id = simulate(root, record)["runs"][0]
    text = "Request diagnostics\n" * 4000 + "FINAL_DIAGNOSTIC_LINE\n"
    lab = Lab(root)
    try:
        run = lab.store.get("run", run_id)
        run["status"] = status
        lab.store.put("run", run_id, run)
        lab.store.commit()
        (root / run["folder"] / "logs.txt").write_text(text)
    finally:
        lab.close()
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        record = web.get(f"/api/sessions/{root.name}/records/{run_id}").json()
        assert record["logs"] == text[:2000] and record["logs_truncated"]
        response = web.get(f"/api/sessions/{root.name}/runs/{run_id}/logs")
        assert response.status_code == 200 and response.text == text
        assert "attachment" in response.headers["content-disposition"]
        missing = web.get(f"/api/sessions/{root.name}/runs/sim_9999/logs").json()
        assert not missing["ok"]


def test_snapshot_line_endings_and_large_property_names_remain_readable(workspace_path):
    _, root = setup(workspace_path)
    raw = "line\r\n" * 6000
    source = reading.snapshot(root, raw, "txt")
    assert reading.snapshot(root, raw, "txt") == source
    value = {"k" * 5000: "detail " * 700}
    preview = reading.preview(root, value)
    assert "next" in preview, "Oversized pointers need a readable whole-record continuation"

    async def scenario():
        tools = LabTools(make_runtime(root))
        output, failed = await tools.execute("Read", {"file_path": source, "offset": len(raw) - 30, "limit": 30})
        assert not failed and json.loads(output)["text"] == raw[-30:]
    asyncio.run(scenario())


def test_workspace_reads_never_observe_partially_saved_candidate_files(workspace_path, monkeypatch):
    course, root = setup(workspace_path)
    first = draft(course, root)
    second = copy.deepcopy(first["candidates"][0])
    second["id"] = "additional"
    writing, release, reading_started = threading.Event(), threading.Event(), threading.Event()
    write_inputs = exploration.write_inputs
    def slow_write(folder, plan, candidates, expectation):
        if folder == root / "rounds" / first["id"]:
            atomic(folder / "plan.yaml", plan)
            writing.set()
            assert release.wait(5)
        write_inputs(folder, plan, candidates, expectation)
    monkeypatch.setattr(exploration, "write_inputs", slow_write)
    def inspect():
        reading_started.set()
        return exploration.session_view(root)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(exploration.action, root, "save", {**first, "workflow": first["id"], "candidates": [*first["candidates"], second]})
        try:
            assert writing.wait(5)
            shown = pool.submit(inspect)
            assert reading_started.wait(5)
            with pytest.raises(TimeoutError):
                shown.result(timeout=.1)
        finally:
            release.set()
        saved.result(timeout=5)
        view = shown.result(timeout=5)
    assert [record["id"] for record in view["experiments"]] == [first["id"]]
    assert len(view["experiments"][0]["candidates"]) == 2


def test_changed_agent_policy_retains_prior_discussion_without_cross_session_memory(workspace_path):
    course, root = setup(workspace_path)
    runtime = make_runtime(root)
    for index in range(40):
        runtime.add("student", f"Earlier question {index}")
        runtime.add("agent", f"Earlier explanation {index}")
    runtime.add("student", "Remember that my comparison label is MEMORY-ORBIT-42.")
    runtime.add("agent", "I will use that label for your comparison.")
    runtime.meta.update(session="test-codex-session", backend="codex", model="gpt-6-luna",
                        interaction="exploration", exploration_revision="outdated")
    runtime.save_meta()

    async def connect(target, previous):
        current = make_runtime(target)
        current.exploration = True
        client = current.factory(current)
        instructions = []
        request = client.server.request
        async def observed(method, params):
            if method == "thread/start":
                instructions.append(params["baseInstructions"])
            return await request(method, params)
        client.server.request = observed
        try:
            await client.connect(previous)
            return instructions[0]
        finally:
            await client.disconnect()

    async def scenario():
        restored = await connect(root, runtime.meta["session"])
        assert "MEMORY-ORBIT-42" in restored
        assert "historical" in restored and ".mlab/evidence/" in restored
        snapshots = [json.loads(path.read_text()) for path in (root / ".mlab/evidence").glob("*.json")]
        assert any(len(item.get("messages", [])) == 82 for item in snapshots)
        fresh = course.root / course.create("9.2")
        assert "MEMORY-ORBIT-42" not in await connect(fresh, None)
    asyncio.run(scenario())
