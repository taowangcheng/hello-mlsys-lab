"""Student-led experiments, immutable evidence and task-wide resource admission."""
import asyncio
import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

from mlab.core.io import LabError, load
from mlab.core.workspace import Lab
from mlab.web import exploration
from mlab.web.agent import TaskRuntime
from mlab.web.bus import Bus
from mlab.web.codex_client import LabTools
from mlab.web.course import Course
from mlab.web.server import create_app


def setup(root, task="9.2"):
    course = Course(root)
    course.ensure("fixture")
    session = course.create(task)
    return course, root / session


def draft(course, root, **changes):
    view = exploration.task_view(course, root.name.split("-")[0])
    preferred = {"9.1": "int4_7b", "9.2": "baseline", "9.3": "fp8_single"}[view["task"]]
    candidate = next(item for item in view["templates"] if item["id"] == preferred)
    return exploration.action(root, "create", {"title": "Explore the baseline", "question": "What changes the tail?",
                              "candidates": [candidate], "seeds": [42], "workloads": ["development"], **changes})


def simulate(root, experiment):
    return exploration.action(root, "simulate", {"workflow": experiment["id"], "revision": experiment["revision"], "confirm": True})


def wait(root, kind, identifier):
    lab = Lab(root, workflow=identifier if kind == "workflow" else None,
              measurement=identifier if kind == "measurement" else None)
    try:
        result = lab.wait(20) if kind == "workflow" else lab.measure_wait(20)
        assert result["status"] == "completed", result
    finally:
        lab.close()


def prepare(root, experiment, repeats=1, **changes):
    body = {"workflow": experiment["id"], "revision": experiment["revision"], "confirm_config": True,
            "points": [{"candidate": experiment["candidates"][0]["id"], "workflow": experiment["id"],
                        "workload": "development", "runs": repeats, "rationale": "Check the simulator against measurement"}], **changes}
    result = exploration.action(root, "prepare_measurement", body)
    if result.get("status") == "simulating":
        wait(root, "measurement", result["id"])
        result = exploration.action(root, "check_measurement", {"measurement": result["id"]})
    return result


def measure(root, preview, **changes):
    return exploration.action(root, "measure", {"measurement": preview["id"], "revision": preview["plan_hash"], "confirm": True, **changes})


@pytest.mark.parametrize("task", ["9.1", "9.2", "9.3"])
def test_students_can_run_multiple_experiments_without_a_decision_or_assistant(workspace_path, task):
    course, root = setup(workspace_path, task)
    first, second = draft(course, root), draft(course, root, title="Another question", seeds=[43])
    assert second["id"] != first["id"]
    for experiment in (second, first):
        simulate(root, experiment)
        wait(root, "workflow", experiment["id"])
    view = exploration.task_view(course, task)
    assert len(view["runs"]) == 2 and not view["conclusions"]
    assert all(item["status"] == "completed" for item in view["experiments"])
    assert view["budget"]["used"] == 0
    preview = prepare(root, first)
    measure(root, preview)
    wait(root, "measurement", preview["id"])
    assert exploration.task_view(course, task)["budget"]["used"] == 1


def test_a_student_can_arrange_measurement_directly_from_a_draft(workspace_path):
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    preview = prepare(root, experiment)
    view = exploration.task_view(course, "9.2")
    assert view["budget"]["used"] == 0
    assert view["runs"] and all(row["role"] == "simulator" for row in view["runs"])
    assert view["experiments"][0]["runs"] == []
    with pytest.raises(LabError, match="Confirm"):
        measure(root, preview, confirm=False)
    measure(root, preview)
    wait(root, "measurement", preview["id"])
    assert exploration.task_view(course, "9.2")["budget"]["used"] == 1


def test_invalid_or_stale_edits_preserve_the_saved_draft(workspace_path):
    course, root = setup(workspace_path)
    original = draft(course, root)
    changed = copy.deepcopy(original)
    changed["candidates"][0]["strategy"]["serving"]["max_concurrency"] = 10000
    with pytest.raises(LabError):
        exploration.action(root, "save", {**changed, "workflow": original["id"]})
    with pytest.raises(LabError):
        exploration.action(root, "create", changed)
    current = exploration.task_view(course, "9.2")["experiments"]
    assert len(current) == 1 and current[0]["revision"] == original["revision"]
    saved = exploration.action(root, "save", {**original, "workflow": original["id"], "title": "A new question"})
    with pytest.raises(LabError) as conflict:
        exploration.action(root, "save", {**original, "workflow": original["id"]})
    assert conflict.value.code == "E_CONFLICT"
    simulate(root, saved)
    wait(root, "workflow", saved["id"])
    with pytest.raises(LabError):
        exploration.action(root, "save", {**saved, "workflow": saved["id"]})
    cloned = exploration.action(root, "create", {**saved, "source": saved["key"]})
    assert cloned["source"] == saved["key"] and cloned["editable"]


def test_invalid_measurement_leaves_no_record_or_approved_configuration(workspace_path):
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    with pytest.raises(LabError):
        prepare(root, experiment, points=[{"candidate": "missing", "workflow": experiment["id"], "runs": 1, "rationale": "Check it"}])
    view = exploration.task_view(course, "9.2")
    assert not view["measurements"] and not view["runs"] and view["experiments"][0]["editable"]


def test_replaying_a_request_never_repeats_execution_or_spending(workspace_path):
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    preview = prepare(root, experiment)
    first = measure(root, preview, request_id="hardware-request")
    second = measure(root, preview, request_id="hardware-request")
    assert first == second
    wait(root, "measurement", preview["id"])
    assert exploration.task_view(course, "9.2")["budget"]["used"] == 1
    with pytest.raises(LabError):
        measure(root, preview, request_id="hardware-request", confirm=False)
    run = first["run_ids"][0]
    assert exploration.action(root, "cancel", {"run": run})["status"] == "succeeded"


def test_an_approved_measurement_can_be_reviewed_and_retried_after_admission_failure(workspace_path, monkeypatch):
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    preview = prepare(root, experiment)
    def fail_admission(*args, **kwargs):
        raise LabError("E_BUDGET", "Another measurement reserved the remaining capacity")
    with monkeypatch.context() as patch:
        patch.setattr(Lab, "launch", fail_admission)
        with pytest.raises(LabError):
            measure(root, preview)
    current = exploration.task_view(course, "9.2")
    assert current["measurements"][0]["status"] == "measure_approved"
    assert current["budget"]["used"] == 0
    reopened = exploration.action(root, "check_measurement", {"measurement": preview["id"]})
    assert reopened["plan_hash"] == preview["plan_hash"] and reopened["points"] == preview["points"]
    measure(root, reopened)
    wait(root, "measurement", preview["id"])
    assert exploration.task_view(course, "9.2")["budget"]["used"] == 1


def test_mutations_require_explicit_targets_even_when_a_current_record_exists(workspace_path):
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    for action in ("save", "simulate", "check_measurement", "measure", "cancel", "conclude"):
        with pytest.raises(LabError) as caught:
            exploration.action(root, action, {"revision": experiment["revision"], "confirm": True})
        assert caught.value.code == "E_SCHEMA"
    assert not exploration.task_view(course, "9.2")["runs"]


def test_shared_budget_is_atomic_across_sessions(workspace_path):
    course, first = setup(workspace_path)
    second = workspace_path / course.create("9.2")
    a, b = draft(course, first), draft(course, second)
    for count in (4, 2):
        preview = prepare(first, a, count)
        measure(first, preview)
        wait(first, "measurement", preview["id"])
    previews = [prepare(first, a, 2), prepare(second, b, 2)]
    barrier = threading.Barrier(2)
    def submit(index):
        barrier.wait(timeout=10)
        try:
            return measure([first, second][index], previews[index])
        except LabError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(submit, [0, 1]))
    assert outcomes.count("E_BUDGET") == 1, outcomes
    for root, preview, outcome in zip([first, second], previews, outcomes):
        if isinstance(outcome, dict):
            wait(root, "measurement", preview["id"])
    assert exploration.task_view(course, "9.2")["budget"] == {"total": 8, "used": 8, "remaining": 0}


def test_history_and_comparison_span_sessions_but_not_tasks(workspace_path):
    course, first = setup(workspace_path)
    second = workspace_path / course.create("9.2")
    other = workspace_path / course.create("9.1")
    keys = []
    for root in (first, second, other):
        experiment = draft(course, root)
        run = simulate(root, experiment)["runs"][0]
        wait(root, "workflow", experiment["id"])
        keys.append(f"{root.name}/{run}")
    result = exploration.compare(course, keys[:2])
    assert len(result["rows"]) == 2 and all(check["same"] for check in result["checks"])
    assert len(exploration.task_view(course, "9.2")["experiments"]) == 2
    for invalid in ([keys[0], keys[2]], [keys[0], keys[0]]):
        with pytest.raises(LabError):
            exploration.compare(course, invalid)


def test_assistant_can_propose_but_cannot_execute_or_write_student_notes(workspace_path):
    course, root = setup(workspace_path)
    example = draft(course, root)
    async def scenario():
        runtime = TaskRuntime(root.name, root, Bus(), lambda _: None, opening=False)
        runtime.exploration = True
        tools = LabTools(runtime)
        output, failed = await tools.execute("Explore", {"action": "draft", "draft_json": json.dumps(example)})
        assert not failed
        assert json.loads(output)["author"] == "assistant"
        for name, body in [("Write", {"file_path": "analysis/note.md", "content": "My conclusion"}),
                           ("Bash", {"command": "mlab workflow submit"}),
                           ("Bash", {"command": "mlab measure submit"}),
                           ("AskUserQuestion", {"questions": []})]:
            assert (await tools.execute(name, body))[1]
    asyncio.run(scenario())
    assert not exploration.task_view(course, "9.2")["runs"]
    with pytest.raises(LabError):
        exploration.action(root, "note", {"text": "A student observation"}, author="assistant")


def test_codex_switches_to_exploration_tools_and_creates_an_editable_proposal(workspace_path):
    from test_codex import make_runtime, until
    course, root = setup(workspace_path)
    proposal = draft(course, root)
    (root / ".mlab/test-proposal.json").write_text(json.dumps(proposal))
    async def scenario():
        runtime = make_runtime(root)
        try:
            await runtime.say("hello")
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle"
            runtime.exploration = True
            await runtime.restart()
            await runtime.say("explore proposal")
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle" and not runtime.cards
            assert runtime.meta["interaction"] == "exploration"
            messages = runtime.chat.read()
            assert any(entry.get("is_error") and "mlab workflow submit" in entry["text"] for entry in messages)
        finally:
            await runtime.stop()
    asyncio.run(scenario())
    assert load(root / ".mlab/codex/test-tools.json") == ["Bash", "Explore", "Read"]
    view = exploration.task_view(course, "9.2")
    assert len(view["experiments"]) == 2 and not view["runs"]
    assert view["experiments"][-1]["author"] == "assistant" and view["experiments"][-1]["editable"]
    assert not (root / "analysis/note.md").exists()


def test_workbench_api_is_usable_without_a_model(workspace_path):
    course = Course(workspace_path)
    course.ensure("fixture")
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        session = web.post("/api/workbench/tasks/9.2/open", json={}).json()["session"]
        view = web.get("/api/workbench/tasks/9.2").json()
        prefix = f"/api/workbench/sessions/{session}"
        experiment = web.post(prefix + "/create", json={"title": "Independent experiment", "candidates": view["templates"][:1]}).json()
        assert experiment["ok"]
        denied = web.post(prefix + "/simulate", json={"workflow": experiment["id"], "revision": experiment["revision"]}).json()
        assert not denied["ok"]
        result = web.post(prefix + "/simulate", json={"workflow": experiment["id"], "revision": experiment["revision"], "confirm": True}).json()
        assert result["ok"]
        wait(workspace_path / session, "workflow", experiment["id"])
        assert web.get("/api/workbench/tasks/9.2").json()["runs"]
        assert 'workbench/assets/' in web.get("/").text
        assert 'app.js' in web.get("/classic").text
        assert not web.get(f"/api/sessions/{session}/chat").json()["entries"]


def test_html_style_nonce_is_fresh_and_chat_rejects_wrong_task_or_unknown_evidence(workspace_path):
    course, root = setup(workspace_path)
    course.create("9.1")
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        first, second = web.get("/"), web.get("/")
        nonces = [re.search(r'name="csp-style-nonce" content="([^"]+)"', response.text)[1] for response in (first, second)]
        assert nonces[0] != nonces[1]
        for response, nonce in zip((first, second), nonces):
            assert f"'nonce-{nonce}'" in response.headers["content-security-policy"]
            assert "script-src 'self';" in response.headers["content-security-policy"]
            assert "unsafe-inline" not in response.headers["content-security-policy"]
        for refs in (["9.1-1/overview"], ["9.2-99/overview"], ["9.2-1/sim_9999"], "9.2-1/overview"):
            result = web.post("/api/workbench/sessions/9.2-1/chat", json={"text": "Explain this", "refs": refs}).json()
            assert not result["ok"] and result["error"]["code"] == "E_SCHEMA"
        assert not web.get("/api/sessions/9.2-1/chat").json()["entries"]


def test_course_storage_scope_is_stable_and_differs_between_directories(workspace_path):
    scopes = []
    for name in ("first", "second", "first"):
        root = workspace_path / name
        Course(root).ensure("fixture")
        with TestClient(create_app(root, "fake", opening=False)) as web:
            scope = web.get("/api/course").json()["storage_id"]
            assert re.fullmatch(r"[a-f0-9]{24}", scope)
            scopes.append(scope)
    assert scopes[0] == scopes[2] and scopes[0] != scopes[1]


def test_legacy_sdk_cannot_start_from_the_workbench_but_direct_controls_work(workspace_path, monkeypatch):
    from mlab.web import server, sdk_client
    course, root = setup(workspace_path)
    calls = []
    def forbidden_client(runtime):
        calls.append(runtime)
        raise AssertionError("Legacy SDK must not start in the workbench")
    monkeypatch.setattr(sdk_client, "factory", lambda provider: forbidden_client)
    with TestClient(create_app(workspace_path, "sdk", opening=False)) as web:
        web.post("/api/workbench/tasks/9.2/open", json={})
        reply = web.post("/api/workbench/sessions/9.2-1/chat", json={"text": "Run an experiment"}).json()
        assert not reply["ok"] and reply["error"]["code"] == "E_BACKEND_UNAVAILABLE"
        template = exploration.task_view(course, "9.2")["templates"][0]
        created = web.post("/api/workbench/sessions/9.2-1/create", json={"title": "Student-controlled", "candidates": [template]}).json()
        assert created["ok"]
        submitted = web.post("/api/workbench/sessions/9.2-1/simulate", json={
            "workflow": created["id"], "revision": created["revision"], "confirm": True}).json()
        assert submitted["ok"]
        wait(root, "workflow", created["id"])
    runtime = TaskRuntime(root.name, root, Bus(), lambda _: None, opening=False)
    runtime.exploration = True
    with pytest.raises(LabError, match="Codex"):
        server.agent_factory("sdk")(runtime)
    assert not calls
