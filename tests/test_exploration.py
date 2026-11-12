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


@pytest.mark.parametrize("task,candidate_id", [("9.1", "fp8_3b"), ("9.2", "baseline"), ("9.3", "fp8_single")])
def test_every_task_can_complete_simulation_measurement_notes_and_conclusion_via_api(workspace_path, task, candidate_id):
    """The full workbench lifecycle includes every validation split and a truthful report."""
    course = Course(workspace_path)
    course.ensure("fixture")
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        session = web.post(f"/api/workbench/tasks/{task}/open", json={}).json()["session"]
        prefix = f"/api/workbench/sessions/{session}"
        def action(name, body):
            result = web.post(f"{prefix}/{name}", json=body).json()
            assert result["ok"], result
            return result
        def view():
            return web.get(f"/api/workbench/tasks/{task}").json()
        def finished(ids):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                runs = [row for row in view()["runs"] if row["id"] in ids]
                if len(runs) == len(ids) and all(row["status"] in {"succeeded", "failed", "cancelled", "timed_out"} for row in runs):
                    assert all(row["status"] == "succeeded" and row["verdict"] == "constraints_met" for row in runs), runs
                    return runs
                time.sleep(.1)
            pytest.fail(f"Runs did not finish: {ids}")
        initial = view()
        candidate = next(item for item in initial["templates"] if item["id"] == candidate_id)
        if task == "9.2":
            candidate["id"] = "c16"
            candidate["strategy"]["serving"]["max_concurrency"] = 16
            candidate["rationale"] = "Reduce concurrency to 16 to retain headroom under the latency limit."
        workloads = ["development", *initial["overview"]["holdouts"]]
        experiment = action("create", {"title": f"Validate {candidate['id']} on task {task}",
                            "question": "Does the configuration meet every task constraint across workloads?",
                            "candidates": [candidate], "seeds": [42, 43], "workloads": workloads})
        simulation = action("simulate", {"workflow": experiment["id"], "revision": experiment["revision"], "confirm": True})
        sim_runs = finished(simulation["runs"])
        points = [{"candidate": candidate["id"], "workflow": experiment["id"], "workload": workload,
                   "runs": 2 if workload == "development" else 1,
                   "rationale": "Check repeatability and transfer to every held-out workload"} for workload in workloads]
        hardware = []
        # Separate workload submissions mirror the page's measurement composer.
        for point in points:
            preview = action("prepare_measurement", {"points": [point]})
            assert "plan_hash" in preview, preview
            submitted = action("measure", {"measurement": preview["id"], "revision": preview["plan_hash"], "confirm": True})
            hardware.extend(finished(submitted["run_ids"]))
        refs = [row["key"] for row in [*sim_runs, *hardware]]
        note = action("note", {"text": "All requested workloads passed in this demonstration. These synthetic results do not establish real GPU performance.", "refs": refs})
        assert note["refs"] == refs
        conclusion = action("conclude", {"candidate": f"{experiment['id']}/{candidate['id']}", "confirm": True})
        final = view()
        assert final["conclusions"][-1]["conclusion"] == "mock_only", conclusion
        assert len(final["notes"]) == 1
        assert final["budget"]["used"] == len(workloads) + 1
        assert final["budget"]["remaining"] >= 0
        for rows in (sim_runs, hardware):
            assert {row["split"] for row in rows} == {"development", "repeat", "holdout"}
            assert {row["holdout"] for row in rows if row["split"] == "holdout"} == set(workloads[1:])
        report = web.get(f"/api/sessions/{session}/records/report").json()
        assert report["ok"] and candidate["id"] in report["markdown"]
        assert (workspace_path / session / "report.md").is_file()
        print(json.dumps({"task": task, "candidate": candidate, "budget": final["budget"],
                          "conclusion": final["conclusions"][-1]["conclusion"],
                          "runs": [{key: row.get(key) for key in ("id", "role", "split", "holdout", "key_value", "verdict")} for row in final["runs"]]}))


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


def test_assistant_cannot_bypass_execution_tools_or_write_student_notes(workspace_path):
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
    with pytest.raises(LabError):
        exploration.action(root, "measure", {"confirm": True}, author="assistant")


@pytest.mark.parametrize("other_task", ["9.1", "9.2"])
def test_conversation_execution_cannot_target_another_session(workspace_path, other_task):
    course, root = setup(workspace_path)
    other = workspace_path / course.create(other_task)
    experiment = draft(course, other)
    async def scenario():
        runtime = TaskRuntime(root.name, root, Bus(), lambda _: None, opening=False)
        runtime.exploration = True
        tools = LabTools(runtime)
        for action in ("show", "simulate", "measure", "wait", "cancel"):
            output, failed = await tools.execute("Explore", {"action": action, "reference": experiment["key"]})
            assert failed and "this conversation" in output
        output, failed = await tools.execute("Explore", {"action": "list"})
        assert not failed
        listing = json.loads(output)
        assert listing["session"] == root.name and listing["experiments"] == []
        assert [item["id"] for item in listing["sessions"]] == [root.name]
        for body in ({"source": experiment["key"], "candidates": experiment["candidates"]},):
            output, failed = await tools.execute("Explore", {"action": "draft", "draft_json": json.dumps(body)})
            assert failed and "this conversation" in output
    asyncio.run(scenario())
    assert not exploration.task_view(course, other_task)["runs"]


def test_assistant_can_measure_approved_configurations_from_two_experiments_in_one_session(workspace_path):
    from test_codex import until
    course, root = setup(workspace_path)
    first = draft(course, root, title="First comparison")
    candidate = copy.deepcopy(first["candidates"][0])
    candidate["strategy"]["serving"]["max_concurrency"] = 16
    experiments = [first, draft(course, root, title="Second comparison", candidates=[candidate])]
    for experiment in experiments:
        simulate(root, experiment)
        wait(root, "workflow", experiment["id"])
    points = [{"candidate": experiment["candidates"][0]["id"], "workflow": experiment["id"],
               "workload": "development", "runs": 1} for experiment in experiments]
    async def scenario():
        runtime = TaskRuntime(root.name, root, Bus(), lambda _: None, opening=False)
        runtime.exploration = True
        tools = LabTools(runtime)
        invalid = copy.deepcopy(points)
        invalid[1]["workflow"] = "9.2-2/r001"
        output, failed = await tools.execute("Explore", {"action": "measure", "reference": experiments[0]["key"], "points": invalid})
        assert failed and not runtime.cards
        pending = asyncio.create_task(tools.execute("Explore", {"action": "measure", "reference": experiments[0]["key"], "points": points}))
        await until(lambda: bool(runtime.cards))
        card = next(iter(runtime.cards.values()))
        assert card["data"]["summary"]["hardware_runs"] == 2
        assert {point["workflow"] for point in card["data"]["summary"]["points"]} == {experiment["id"] for experiment in experiments}
        preview = await asyncio.to_thread(exploration.action, root, "check_measurement", {"measurement": card["data"]["summary"]["measurement"]})
        assert {point["workflow"] for point in preview["points"]} == {experiment["id"] for experiment in experiments}
        assert {point["candidate"] for point in preview["points"]} == {candidate["id"]}
        runtime.resolve(card["id"], {"allow": True})
        output, failed = await pending
        assert not failed, output
        return json.loads(output)
    result = asyncio.run(scenario())
    wait(root, "measurement", result["measurement"])
    approved = exploration.action(root, "check_measurement", {"measurement": result["measurement"]})
    assert {point["workflow"] for point in approved["points"]} == {experiment["id"] for experiment in experiments}
    assert {point["candidate"] for point in approved["points"]} == {candidate["id"]}
    hardware = [run for run in exploration.session_view(root)["runs"] if run["role"] == "hardware"]
    assert len(hardware) == 2
    assert {run["config"].split("/")[1] for run in hardware} == {experiment["id"] for experiment in experiments}


def test_draft_configuration_can_be_inspected_and_attached_without_approval(workspace_path):
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    candidate = experiment["candidates"][0]
    reference = f"{experiment['key']}/{candidate['id']}"
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        record = web.get(f"/api/sessions/{root.name}/records/{experiment['id']}/{candidate['id']}").json()
        assert record["ok"] and record["draft"] and record["configuration"] == candidate
        assert not record["runs"] and record["preview"] == "not_evaluated"
        result = web.post(f"/api/workbench/sessions/{root.name}/chat", json={"text": "Explain these settings.", "refs": [reference]}).json()
        assert result["ok"] and result["refs"] == [reference]
    current = exploration.session_view(root)["experiments"][0]
    assert current["editable"] and not current["approved"] and not current["runs"]


def test_default_named_experiments_need_no_research_question_before_execution(workspace_path):
    course, root = setup(workspace_path)
    first = draft(course, root, title="Untitled experiment", question="")
    simulate(root, first)
    wait(root, "workflow", first["id"])
    second = draft(course, root, title="Untitled experiment", question="")
    preview = prepare(root, second)
    measure(root, preview)
    wait(root, "measurement", preview["id"])
    view = exploration.session_view(root)
    assert all(item["status"] == "succeeded" for item in view["runs"])
    assert view["budget"]["used"] == 1


def test_report_keeps_authored_words_and_notes_without_rendering_action_envelopes(workspace_path):
    from mlab.core.student import log_student
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    reference = experiment["key"]
    literal_json = '{"action": "This JSON is my own example", "body": {"question": "Why?"}}'
    log_student(root, literal_json, source="web-chat", kind="chat")
    log_student(root, "The smaller configuration is worth checking.", source="web-form", kind="review")
    log_student(root, json.dumps({"action": "measure", "allow": False, "reason": "Check the workload first.",
                                 "plan_hash": "private-implementation-marker"}), source="web-card", kind="execution")
    note = ("The second run may expose queueing. " + "Observe the tail carefully. " * 180).strip()
    exploration.action(root, "note", {"text": note, "refs": [reference]})
    audit_path = root / ".mlab/student.jsonl"
    before = audit_path.read_bytes()
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        preview = web.get(f"/api/sessions/{root.name}/records/report").json()
        assert preview["ok"] and not preview["written"]
        markdown = preview["markdown"]
        assert literal_json in markdown and "The smaller configuration is worth checking." in markdown
        assert "Check the workload first." in markdown and "private-implementation-marker" not in markdown
        assert note in markdown and f"`{reference}`" in markdown
        assert '\"action\": \"note\"' not in markdown and '\"action\": \"create\"' not in markdown
        assert audit_path.read_bytes() == before
        result = web.post(f"/api/workbench/sessions/{root.name}/report", json={}).json()
        assert result["ok"] and (root / "report.md").read_text() == markdown
    exported = load(root / "report.json")
    assert exported["student_notes"][0]["text"] == note
    assert any(message["source"] == "web-action" for message in exported["student_messages"])
    assert audit_path.read_bytes().startswith(before)


@pytest.mark.parametrize("task,payload,ids", [
    ("9.1", {"template_ids": ["int4_7b", "fp8_3b"]}, ["int4_7b", "fp8_3b"]),
    ("9.2", {"variants": [
        {"template_id": "baseline", "id": "c16", "changes": [{"field": "serving.max_concurrency", "value": "16"}]},
        {"template_id": "baseline", "id": "c20", "changes": [{"field": "serving.max_concurrency", "value": "20"}]}]}, ["c16", "c20"]),
    ("9.3", {"variants": [
        {"template_id": "fp8_single", "id": "fp8_single", "changes": []},
        {"template_id": "fp8_single", "id": "int4_control", "changes": [{"field": "deployments.0.weight_precision", "value": "INT4"}]}]}, ["fp8_single", "int4_control"]),
])
def test_structured_example_drafts_copy_complete_candidates_and_only_requested_changes(workspace_path, task, payload, ids):
    from mlab.core.workspace import explicit_candidate
    course, root = setup(workspace_path, task)
    templates = {item["id"]: item for item in exploration.task_view(course, task)["templates"]}
    async def scenario():
        runtime = TaskRuntime(root.name, root, Bus(), lambda _: None, opening=False)
        runtime.exploration = True
        output, failed = await LabTools(runtime).execute("Explore", {"action": "draft", "title": "Controlled comparison", **payload})
        assert not failed, output
        return json.loads(output)
    result = asyncio.run(scenario())
    assert [item["id"] for item in result["candidates"]] == ids
    assert result["editable"] and not result["runs"] and result["author"] == "assistant"
    if task == "9.1":
        assert result["candidates"] == [explicit_candidate(templates[item]) for item in ids]
    elif task == "9.2":
        assert [item["strategy"]["serving"]["max_concurrency"] for item in result["candidates"]] == [16, 20]
        assert all(item["strategy"]["deployments"] == templates["baseline"]["strategy"]["deployments"] for item in result["candidates"])
    else:
        first, second = result["candidates"]
        assert first == explicit_candidate(templates["fp8_single"])
        assert second["strategy"]["deployments"][0]["weight_precision"] == "INT4"
        expected = copy.deepcopy(first["strategy"])
        expected["deployments"][0]["weight_precision"] = "INT4"
        assert second["strategy"] == expected
    assert not exploration.task_view(course, task)["runs"]


def test_structured_variants_cannot_change_task_rules_or_bypass_parameter_validation(workspace_path):
    course, root = setup(workspace_path)
    async def scenario():
        runtime = TaskRuntime(root.name, root, Bus(), lambda _: None, opening=False)
        runtime.exploration = True
        tools = LabTools(runtime)
        for field, value in [("constraints.0.value", "9999"), ("serving.max_concurrency", "10000"), ("deployments.9.weight_precision", "INT4")]:
            output, failed = await tools.execute("Explore", {"action": "draft", "title": "Invalid change", "variants": [
                {"template_id": "baseline", "id": "invalid", "changes": [{"field": field, "value": value}]}]})
            assert failed, output
        output, failed = await tools.execute("Bash", {"command": "mlab list"})
        assert failed and "template_ids" in output and "mlab task show" in output
    asyncio.run(scenario())
    assert not exploration.task_view(course, "9.2")["experiments"]


def test_exploration_activity_persists_only_allowed_actions_in_chat_history(workspace_path):
    _, root = setup(workspace_path)
    runtime = TaskRuntime(root.name, root, Bus(), None, opening=False)
    actions = ["list", "show", "draft", "simulate", "measure", "wait", "cancel"]
    invalid = ["delete", ["simulate"], {"action": "measure"}, None]
    for index, action in enumerate(actions + invalid):
        runtime.event({"type": "tool", "id": str(index), "name": "Explore", "action": "PRIVATE_OUTER_ACTION",
                       "input": {"action": action, "draft_json": "PRIVATE_DRAFT", "unexpected": "PRIVATE_FIELD"}})
    runtime.event({"type": "tool_result", "id": "0", "text": "Task available", "is_error": False})
    runtime.event({"type": "tool", "name": "Read",
                   "input": {"action": "show", "file_path": "task/task.yaml", "unexpected": "PRIVATE_FIELD"}})

    # Recreate the runtime through the API to verify that the safe activity survives disk persistence.
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        history = web.get(f"/api/sessions/{root.name}/chat").json()
    assert history["ok"]
    entries = history["entries"]
    assert [entry.get("action") for entry in entries] == actions + [None] * (len(invalid) + 1)
    assert entries[0]["result"] == "Task available" and entries[0]["action"] == "list"
    assert all(set(entry) <= {"id", "at", "role", "text", "tool", "action", "result", "is_error"} for entry in entries)
    assert "PRIVATE_" not in json.dumps(entries)
    assert "PRIVATE_" not in runtime.chat.path.read_text()


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


@pytest.mark.parametrize("previous_revision,changed_policy", [(None, "instructions"), ("obsolete", "tools")])
def test_exploration_reconnect_migrates_changed_policies_and_resumes_matching_policies(workspace_path, monkeypatch, previous_revision, changed_policy):
    from test_codex import make_runtime
    from mlab.web import codex_client
    course, root = setup(workspace_path)
    original = draft(course, root)
    runtime = make_runtime(root)
    message = runtime.add("student", "Keep this experiment and our earlier discussion.")
    runtime.meta.update({"session": "test-codex-session", "backend": "codex", "model": codex_client.MODEL,
                         "interaction": "exploration", "input_tokens": 77, "codex_usage_seen": {"inputTokens": 77}})
    if previous_revision is not None:
        runtime.meta["exploration_revision"] = previous_revision
    runtime.save_meta()

    async def connect():
        current = make_runtime(root)
        current.exploration = True
        client = current.factory(current)
        methods = []
        request = client.server.request
        async def tracked_request(method, params):
            if method.startswith("thread/"):
                methods.append(method)
            return await request(method, params)
        client.server.request = tracked_request
        try:
            await client.connect(current.meta["session"])
            assert current.meta["input_tokens"] == 77
            assert "codex_usage_seen" not in current.meta
            assert message in current.chat.read()
            assert load(current.meta_path)["exploration_revision"] == current.meta["exploration_revision"]
            return methods, current.meta["exploration_revision"]
        finally:
            await client.disconnect()

    async def scenario():
        methods, revision = await connect()
        assert methods == ["thread/start"] and re.fullmatch(r"sha256:[a-f0-9]{64}", revision)
        assert await connect() == (["thread/resume"], revision)
        if changed_policy == "instructions":
            monkeypatch.setattr(codex_client, "EXPLORATION", codex_client.EXPLORATION + "Keep comparisons focused.\n")
        else:
            tools = copy.deepcopy(codex_client.TOOLS)
            tools[0]["inputSchema"]["properties"]["title"]["description"] += " Use a short name."
            monkeypatch.setattr(codex_client, "TOOLS", tools)
        methods, updated_revision = await connect()
        assert methods == ["thread/start"] and updated_revision != revision
        assert await connect() == (["thread/resume"], updated_revision)

    asyncio.run(scenario())
    assert exploration.task_view(course, "9.2")["experiments"] == [original]


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


@pytest.mark.parametrize("decision", ["allow", "deny", "followup", "changed", "interrupt"])
def test_conversation_executes_requested_simulations_and_confirms_exact_hardware_inputs(workspace_path, decision):
    from mlab.core.io import atomic
    course = Course(workspace_path)
    course.ensure("fixture")
    app = create_app(workspace_path, "fake", opening=False)
    with TestClient(app) as web:
        session = web.post("/api/workbench/tasks/9.2/open", json={}).json()["session"]
        root = workspace_path / session
        def history():
            return web.get(f"/api/sessions/{session}/chat").json()
        def until(predicate):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                result = history()
                if predicate(result):
                    return result
                time.sleep(.02)
            pytest.fail(f"Conversation did not reach expected state: {history()}")
        def say(text, refs=None):
            return web.post(f"/api/workbench/sessions/{session}/chat", json={"text": text, "refs": refs or []}).json()

        assert say("What is simulation?")["ok"]
        until(lambda result: result["agent"]["status"] == "idle" and sum(entry["role"] == "agent" for entry in result["entries"]) >= 1)
        assert not exploration.task_view(course, "9.2")["experiments"]
        assert say("Run a simulation for this task.")["ok"]
        until(lambda result: result["agent"]["status"] == "idle" and sum(entry["role"] == "agent" for entry in result["entries"]) >= 2)
        view = exploration.task_view(course, "9.2")
        assert len(view["experiments"]) == 1 and len(view["runs"]) == 1
        assert view["runs"][0]["status"] == "succeeded" and view["budget"]["used"] == 0
        assert say("Run a sandbox measurement for this experiment.")["ok"]
        waiting = until(lambda result: any(card["kind"] == "execution" for card in result["agent"]["cards"]))
        card = waiting["agent"]["cards"][0]
        summary = card["summary"]
        sidebar = next(item for item in web.get("/api/course").json()["sessions"] if item["id"] == session)
        assert sidebar["status"] == "waiting_approval" and sidebar["pending_count"] == 1 and sidebar["queued"] == 0
        assert summary["hardware_runs"] == 1 and summary["hardware_budget"]["remaining"] == 8
        assert summary["configs"][0]["id"] == view["experiments"][0]["candidates"][0]["id"]
        assert summary["points"][0]["workflow"] == view["experiments"][0]["id"]
        assert summary["points"][0]["seed"] == 42
        assert not any(run["role"] == "hardware" for run in exploration.task_view(course, "9.2")["runs"])
        route = f"/api/sessions/{session}/execution/{card['id']}"
        if decision == "followup":
            reply = say("Explain the result before running anything else.", [f"{session}/r001"])
            assert reply["ok"] and reply["status"] == "answered_card"
        elif decision == "interrupt":
            assert web.post(f"/api/sessions/{session}/agent/interrupt", json={}).json()["ok"]
        else:
            if decision == "changed":
                points = root / "measurements" / summary["measurement"] / "points.yaml"
                payload = load(points)
                payload["points"][0]["runs"] = 2
                atomic(points, payload)
            assert web.post(route, json={"allow": decision != "deny"}).json()["ok"]
        completed = until(lambda result: result["agent"]["status"] == "idle" and sum(entry["role"] == "agent" for entry in result["entries"]) >= 3)
        assert not completed["agent"]["cards"]
        assert not web.post(route, json={"allow": True}).json()["ok"]
        final = exploration.task_view(course, "9.2")
        hardware = [run for run in final["runs"] if run["role"] == "hardware"]
        assert len(hardware) == (1 if decision == "allow" else 0)
        assert final["budget"]["used"] == len(hardware)
        if decision == "allow":
            assert hardware[0]["status"] == "succeeded"
            assert any(hardware[0]["key"] in entry.get("refs", []) for entry in completed["entries"] if entry["role"] == "agent"), completed["entries"]
        assert not any(entry.get("card") in {"approval", "question"} for entry in completed["entries"])


def test_new_conversation_has_task_context_but_never_imports_previous_records(workspace_path, monkeypatch):
    from mlab.web.fake_client import FakeAgentClient
    course, first = setup(workspace_path)
    second = workspace_path / course.create("9.2")
    empty = workspace_path / course.create("9.2")
    for root in (first, second):
        experiment = draft(course, root, title=f"Private experiment in {root.name}")
        simulate(root, experiment)
        wait(root, "workflow", experiment["id"])
    first_experiment = exploration.session_view(first)["experiments"][0]
    preview = prepare(first, first_experiment)
    measure(first, preview)
    wait(first, "measurement", preview["id"])
    exploration.action(first, "note", {"text": "A previous observation", "refs": [f"{first.name}/sim_0001"]})
    exploration.action(first, "conclude", {"candidate": f"r001/{first_experiment['candidates'][0]['id']}", "confirm": True})
    captured = []
    original = FakeAgentClient.turn
    async def capture(self, prompt):
        captured.append((self.root, prompt))
        async for event in original(self, prompt):
            yield event
    monkeypatch.setattr(FakeAgentClient, "turn", capture)
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        assert web.post(f"/api/workbench/sessions/{empty.name}/open", json={}).json()["session"] == empty.name
        endpoint = f"/api/workbench/sessions/{empty.name}/chat"
        for body in ({"text": "Explain @sim_0001", "refs": []},
                     {"text": "Explain this", "refs": [f"{first.name}/sim_0001"]},
                     {"text": f"Explain @{first.name}/sim_0001", "refs": []}):
            assert not web.post(endpoint, json=body).json()["ok"]
        assert not web.get(f"/api/sessions/{empty.name}/chat").json()["entries"]
        response = web.post(endpoint, json={"text": "Why can queueing increase latency?", "refs": []}).json()
        assert response["ok"] and response["refs"] == []
        deadline = time.monotonic() + 10
        while not captured and time.monotonic() < deadline:
            time.sleep(.01)
        assert captured and captured[0][0] == empty
        context = json.loads(captured[0][1].split("student instructions):\n", 1)[1])
        assert context["task"]["task"] == "9.2" and context["task"]["constraints"]
        assert context["evidence"] == [] and first.name not in captured[0][1]
        messages = web.get(f"/api/sessions/{empty.name}/chat").json()["entries"]
        assert any(entry["role"] == "student" and entry["refs"] == [] for entry in messages)
        assert not web.get(f"/api/sessions/{first.name}/chat").json()["entries"]
        isolated = web.get(f"/api/workbench/sessions/{empty.name}").json()
        assert [item["id"] for item in isolated["sessions"]] == [empty.name]
        for field in ("experiments", "runs", "measurements", "notes", "conclusions"):
            assert isolated[field] == []
        assert isolated["budget"]["used"] == 1
        prior = web.get(f"/api/workbench/sessions/{first.name}").json()
        assert len(prior["experiments"]) == 1 and prior["notes"] and prior["measurements"] and prior["conclusions"]
        assert all(run["session"] == first.name for run in prior["runs"])
        assert prior["budget"] == isolated["budget"]
        assert web.post(f"/api/workbench/sessions/{first.name}/open", json={}).json()["session"] == first.name
        own = web.post(f"/api/workbench/sessions/{first.name}/chat", json={"text": "Explain @sim_0001", "refs": []}).json()
        assert own["ok"] and own["refs"] == [f"{first.name}/sim_0001"]
        report = web.post(f"/api/workbench/sessions/{empty.name}/report", json={}).json()
        assert report["ok"] and report["runs"] == 0 and report["conclusion"] is None
        assert web.get(f"/api/sessions/{empty.name}/records/report").json()["written"]


def test_new_conversation_keeps_the_previous_client_discussion_and_experiments(workspace_path):
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    app = create_app(workspace_path, "fake", opening=True)
    with TestClient(app) as web:
        opened = web.post(f"/api/workbench/sessions/{root.name}/open", json={}).json()
        assert opened["ok"] and opened["agent"]["interaction"] == "exploration"
        assert web.post(f"/api/sessions/{root.name}/agent/open", json={}).json()["ok"]
        assert not web.get(f"/api/sessions/{root.name}/chat").json()["entries"]
        state = web.get(f"/api/sessions/{root.name}/page").json()["state"]
        assert state["form"] is None and state["step"] is None and not state["quick_replies"]
        assert web.post(f"/api/workbench/sessions/{root.name}/chat", json={"text": "Explain the latency constraint."}).json()["ok"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            previous = web.get(f"/api/sessions/{root.name}/chat").json()
            if previous["agent"]["status"] == "idle" and any(entry["role"] == "agent" for entry in previous["entries"]):
                break
            time.sleep(.01)
        old_runtime = app.state.runtimes[root.name]
        old_client = old_runtime.client
        assert old_client is not None
        before = exploration.task_view(course, "9.2")
        created = web.post("/api/sessions", json={"task": "9.2"}).json()
        assert created["ok"] and created["session"] != root.name
        assert web.post(f"/api/workbench/sessions/{created['session']}/open", json={}).json()["session"] == created["session"]
        assert web.post(f"/api/workbench/sessions/{root.name}/open", json={}).json()["session"] == root.name
        assert app.state.runtimes[root.name] is old_runtime and old_runtime.client is old_client
        assert web.get(f"/api/sessions/{root.name}/chat").json()["entries"] == previous["entries"]
        assert web.get(f"/api/sessions/{created['session']}/chat").json()["entries"] == []
        after = web.get("/api/workbench/tasks/9.2").json()
        assert after["budget"] == before["budget"] and after["experiments"] == [experiment]
        assert [session["id"] for session in after["sessions"]] == [root.name, created["session"]]
        clean = web.get(f"/api/workbench/sessions/{created['session']}").json()
        assert clean["experiments"] == [] and clean["budget"] == before["budget"]
        sidebar = next(item for item in web.get("/api/course").json()["sessions"] if item["id"] == root.name)
        assert sidebar["title"] == "Explain the latency constraint."
        assert sidebar["last_message_seq"] > 0 and sidebar["last_message_at"] and sidebar["last_message_role"] == "agent"
        assert sidebar["interaction"] == "exploration" and sidebar["badge"]["text"] != "Hypothesis needed"


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


def test_saved_run_settings_remain_available_without_a_completed_result(workspace_path, monkeypatch):
    from mlab.backends.local import LocalBackend
    from mlab.web import views
    monkeypatch.setattr(LocalBackend, "submit", lambda *args, **kwargs: None)
    course, root = setup(workspace_path)
    held_out = exploration.session_view(root)["overview"]["holdouts"][0]
    experiment = draft(course, root, seeds=[42, 43], workloads=["development", held_out])
    submitted = simulate(root, experiment)
    lab = Lab(root)
    try:
        run = next(lab.store.get("run", identifier) for identifier in submitted["runs"]
                   if lab.store.get("run", identifier).get("holdout") == held_out
                   and load(root / lab.store.get("run", identifier)["folder"] / "spec.json")["execution"]["seed"] == 43)
        path = root / run["folder"] / "spec.json"
        spec = load(path)
        for status in ("queued", "cancelled", "failed"):
            run["status"] = status
            lab.store.put("run", run["id"], run)
            lab.store.commit()
            record = views.run_view(lab, run["id"])
            assert record["seed"] == 43 and record["strategy"] == spec["strategy"]
            assert record["status"] == status and "metrics" not in record
        spec["execution"]["seed"] = 999
        path.write_text(json.dumps(spec))
        with pytest.raises(LabError, match="run spec changed"):
            views.run_view(lab, run["id"])
    finally:
        lab.close()
