"""Page views read every record of all three tasks without changing anything; confirmation cards say what would fail."""
import json
import pytest
from mlab.core import student
from mlab.core.configs import catalog
from mlab.core.io import TERMINAL, LabError, atomic, load
from mlab.core.metrics import get_metric
from mlab.core.workspace import Lab
from mlab.web import approvals, views
from test_flywheels import measured, sweep
from test_history_cli import snapshot, story
from test_workflow import approve, decided, ready


@pytest.mark.parametrize("draft", ["question: missing: quotes\n", "- not a plan mapping\n", ""])
def test_progress_remains_readable_during_invalid_plan_edit(workspace_path, draft):
    lab = ready(workspace_path)
    try:
        path = lab.folder() / "plan.yaml"
        original = path.read_text()
        lab.check()
        path.write_text(draft)
        assert views.task_state(lab)["workflow"] == "r001"
        round_view = views.round_view(lab, "r001")
        assert round_view["plan"]["question"] == ""
        assert round_view["candidates"] == []
        with pytest.raises((LabError, ValueError)):
            lab.check()
        path.write_text(original)
        assert lab.check()["planned_runs"] >= 1
    finally:
        lab.close()


def concluded_and_continued(path):
    """9.2: a measurement where c20 breaks the SLA, a final measurement of c16 and a conclusion on it; then a new round from the second one."""
    lab = story(path)
    measured(lab, [{"candidate": "c16", "runs": 2, "rationale": "final validation"},
                   {"candidate": "c16", "workload": "short_prompt_distribution", "rationale": "holdout"},
                   {"candidate": "c16", "workload": "long_prompt_distribution", "rationale": "holdout"}])
    lab.conclude("r002/c16")
    lab.new_workflow(source="r002")
    return lab


def one_round(path, task):
    """One decided round whose analysis declares a checked number."""
    lab = ready(path, task=task)
    approve(lab)
    lab.submit()
    assert lab.wait(20)["status"] == "completed"
    metric = lab.task["key_metrics"][0]
    values = {run: get_metric(lab.result(run)[0]["metrics"], metric) for run in lab.workflow()["runs"]}
    claimed = next(run for run, value in values.items() if type(value) in {int, float})
    atomic(lab.folder() / "analysis.md", f"{claimed} has the best {metric}.", text=True)
    atomic(lab.folder() / "analysis.claims.json", [{"run_id": claimed, "metric": metric, "value": values[claimed]}])
    atomic(lab.folder() / "decision.md", f"Based on {claimed}.", text=True)
    lab.decide(lab.store.get("run", claimed)["candidate"])
    return lab


def everything(lab):
    shown = {"state": views.task_state(lab), "overview": views.task_overview(lab), "notebook": views.record(lab, "overview"),
             "report": views.record(lab, "report")}
    for config in catalog(lab):
        shown["config:" + config["id"]] = views.record(lab, config["id"])
    for kind in ("workflow", "measurement", "run"):
        for item in lab.store.all(kind):
            shown[item["id"]] = views.record(lab, item["id"])
    for run in lab.store.all("run"):
        if run["status"] in TERMINAL:
            shown["requests:" + run["id"]] = views.run_requests(lab, run["id"])
    for m in lab.store.all("measurement"):
        if m["status"] == "submitted":
            for row in lab.alignment(m)["rows"]:
                shown["pairs:" + row["run_id"]] = views.alignment_pairs(lab, m["id"], row["run_id"])
    development = [r["id"] for r in lab.store.all("run") if r["split"] == "development" and r["role"] == "simulator" and r["status"] == "succeeded"]
    shown["compare"] = views.comparison_view(lab, development[:4])
    for kind in approvals.TITLES:
        shown["card:" + kind] = approvals.summarize(lab, kind, {})
    json.dumps(shown)  # The page receives plain JSON.
    return shown


@pytest.mark.parametrize("task", ["9.1", "9.2", "9.3"])
def test_every_view_reads_without_changing_anything(workspace_path, task):
    lab = concluded_and_continued(workspace_path) if task == "9.2" else one_round(workspace_path, task)
    lab.next()
    lab.close()
    before = snapshot(workspace_path)
    lab = Lab(workspace_path, readonly=True)
    try:
        shown = everything(lab)
    finally:
        lab.close()
    assert snapshot(workspace_path) == before
    assert all(shown[key] for key in shown if not key.startswith("card:")), [key for key in shown if not shown[key]]
    assert all(card["will_fail"] or card["facts"] for key, card in shown.items() if key.startswith("card:"))
    if task == "9.2":
        notebook = shown["notebook"]
        assert [i["kind"] for i in notebook["items"]] == ["round", "round", "measurement", "measurement", "conclusion", "round"]
        assert notebook["validation"]["conclusion"] == "mock_only" and notebook["validation"]["missing"]["hardware"] == []
        assert notebook["selection"] == "r002/c16" and notebook["conclusion"]["conclusion"] == "mock_only" and notebook["conclusion"]["config"] == "r002/c16"
        assert [c["id"] for c in notebook["configs"]] == ["r001/baseline", "r001/chunked_256", "r001/low_concurrency", "r002/c16", "r002/c20", "r002/c24"]
        c16 = shown["config:r002/c16"]
        assert c16["selected"] and c16["missing"]["hardware"] == [] and c16["preview"] == "mock_only" and c16["conclusions"] == ["001"]
        assert c16["chart"]["kind"] == "paired" and len(c16["chart"]["bars"]) == 4
        assert all(r["gap_pct"] is not None and r["simulated"] for r in c16["runs"] if r["role"] == "hardware")
        c20 = shown["config:r002/c20"]
        assert [r["run"] for r in c20["runs"] if r["role"] == "hardware"] == ["hw_0001"] and not c20["selected"]
        assert shown["hw_0001"]["config"] == "r002/c20" and shown["hw_0002"]["config"] == "r001/low_concurrency"
        first = shown["m001"]
        assert first["stage"] == "measured" and len(first["flagged"]) == 2 and len(first["chart"]["bars"]) == 2
        assert all("evidence" not in row for row in first["alignment"]["rows"])
        c20 = next(row for row in first["alignment"]["rows"] if row["candidate"] == "c20")
        pairs = shown["pairs:" + c20["run_id"]]
        assert pairs["shift"] == "all" and sum(p["slowest"] for p in pairs["pairs"]) == max(3, len(pairs["pairs"]) // 4)
        reused = shown["r003"]
        assert reused["source"] == "r002" and reused["editable"] and not reused["candidates_frozen"]
        assert [d["field"] for d in reused["candidates"][1]["diff"]] == ["serving.max_concurrency"]
        assert not shown["r002"]["editable"] and shown["state"]["form"] == "hypothesis" and shown["state"]["conclusion"] == "mock_only"
        assert shown["report"]["markdown"].startswith("# Report: 9.2") and shown["report"]["conclusion"] == "mock_only"
        assert not [line for line in shown["report"]["markdown"].splitlines() if line.startswith("#") and "student" in line.lower()]
    else:
        decided_round = shown["r001"]
        assert decided_round["claims"]["valid"] and decided_round["decision"]["confirmed"] and decided_round["chart"]["bars"]
        timelines = [shown[key] for key in shown if key.startswith("requests:") and shown[key]["rows"]]
        if task == "9.3":
            assert all(t["critical_path"] and t["critical_path"][0] == "plan" for t in timelines)
            assert {"llm-32b", "llm-7b"} <= set(timelines[0]["lanes"])
        else:
            assert all(not t["critical_path"] for t in timelines) and "chat" in timelines[0]["lanes"]
        assert shown["state"]["step"] == "next_step" and shown["state"]["form"] == "next_step"


def test_cards_and_views_follow_the_9_2_flow(workspace_path):
    lab = ready(workspace_path)
    view = Lab(workspace_path, readonly=True)
    try:
        checked = lab.check()
        card = approvals.summarize(view, "workflow_approve", {"plan": checked["plan_hash"]})
        assert card["will_fail"] == "Add your comments before confirming the plan" and card["record"] == "r001"
        student.write_review(lab, [{"candidate": "baseline", "verdict": "keep", "reason": "the control"}])
        assert approvals.summarize(view, "workflow_approve", {"plan": checked["plan_hash"]})["will_fail"] is None
        assert approvals.summarize(view, "workflow_approve", {"plan": "sha256:00000000"})["will_fail"].startswith("The plan has changed")
        shown = views.round_view(view, "r001")
        assert shown["check"] == {"hash": checked["plan_hash"], "stale": False} and shown["editable"]
        diffs = {c["id"]: [d["field"] for d in c["diff"]] for c in shown["candidates"]}
        assert diffs["baseline"] == [] and diffs["chunked_256"] == ["serving.chunked_prefill_tokens"]
        assert diffs["low_concurrency"] == ["serving.max_concurrency"]
        candidate = load(lab.folder() / "candidates/chunked_256.yaml")
        atomic(lab.folder() / "candidates/chunked_256.yaml", {**candidate, "rationale": candidate["rationale"] + " (changed)"})
        assert views.round_view(view, "r001")["check"]["stale"]
        atomic(lab.folder() / "candidates/chunked_256.yaml", candidate)
        lab.approve(checked["plan_hash"])
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        atomic(lab.folder() / "decision.md", "Keep low_concurrency.", text=True)
        assert approvals.summarize(view, "workflow_decide", {"candidate": "low_concurrency"})["will_fail"] == "The decision cites no run of this round"
        student.write_decision(lab, "Based on sim_0003, keep low_concurrency", "low_concurrency")
        assert approvals.summarize(view, "workflow_decide", {"candidate": "c99"})["will_fail"] == "c99 is not an approved candidate of this round"
        assert approvals.summarize(view, "workflow_decide", {"candidate": "low_concurrency"})["will_fail"] is None
        lab.decide("low_concurrency")
        # A confirmed decision is final: the card says so, and mlab points to concluding on another configuration instead.
        assert approvals.summarize(view, "workflow_decide", {"candidate": "chunked_256"})["will_fail"].startswith("The decision of r001 is already confirmed")
        with pytest.raises(LabError) as error:
            lab.decide("chunked_256")
        assert error.value.code == "E_NEEDS_APPROVAL" and "conclude --candidate" in error.value.actions[0]["say"]
        sweep(lab)
        decided(lab, "c20")
        lab.measure_new()
        form = views.measurement_view(view, "m001")
        assert form["editable"] and form["draft"] is None and form["budget"]["remaining"] == 8
        choices = {(c["workflow"], c["candidate"]): c for c in form["choices"]}
        assert choices[("r002", "c20")]["margin_pct"] == 3.0 and choices[("r002", "c20")]["selected"]
        student.write_points(lab, [{"candidate": "c20", "workload": "short_prompt_distribution", "rationale": "check the holdout workload"}])
        draft = views.measurement_view(view, "m001")["draft"]
        assert [r["sim_run"] for r in draft["rows"]] == [None] and draft["hardware_runs"] == 1 and not draft["checked"]
        student.write_points(lab, [{"candidate": "c20", "rationale": "my pick, but the margin is only 3%"},
                                   {"candidate": "low_concurrency", "rationale": "a low-concurrency control"}])
        draft = views.measurement_view(view, "m001")["draft"]
        assert all(r["sim_run"] for r in draft["rows"]) and draft["hardware_runs"] == 2 and not draft["checked"]
        checked = lab.measure_check()
        assert views.measurement_view(view, "m001")["draft"]["checked"]
        card = approvals.summarize(view, "measure_approve", {"plan": checked["plan_hash"]})
        assert card["will_fail"] is None and ["Budget", "Uses 2; 6 left after approval (of 8)"] in card["facts"]
        assert approvals.summarize(view, "measure_approve", {"plan": "deadbeef00"})["will_fail"].startswith("The measurement points changed")
        lab.measure_approve(checked["plan_hash"])
        lab.measure_submit()
        assert lab.measure_wait(20)["status"] == "completed"
        aligned = lab.align()
        shown = views.measurement_view(view, "m001")
        assert shown["stage"] == "measured" and len(shown["flagged"]) == 2 and len(shown["alignment"]["rows"]) == len(aligned["rows"]) == 2
        # Nothing waits on the comparison: the conclusion card is open, for the selection or for another configuration.
        card = approvals.summarize(view, "conclude", {})
        assert dict(card["facts"])["Conclusion level"] == "unresolved" and dict(card["facts"])["Selected configuration"] == "r002/c20" and card["will_fail"] is None
        assert dict(approvals.summarize(view, "conclude", {"candidate": "r002/c16"})["facts"])["Selected configuration"] == "r002/c16"
        validation = views.validation_matrix(view)
        assert validation["conclusion"] == "unresolved" and validation["candidate"] == "c20" and validation["config"] == "r002/c20"
        assert approvals.summarize(view, "conclude", {"candidate": "r009/c1"})["will_fail"].startswith("r009/c1")
    finally:
        view.close()
        lab.close()


@pytest.mark.parametrize("command, kind, plain, args", [
    ("mlab workflow approve --plan sha256:abc12345", "workflow_approve", True, {"plan": "sha256:abc12345"}),
    ("mlab measure approve --plan abc12345", "measure_approve", True, {"plan": "abc12345"}),
    ("mlab measure unlock", "measure_unlock", True, {}),
    ("mlab conclude", "conclude", True, {}),
    ("mlab conclude --candidate r002/c16", "conclude", True, {"candidate": "r002/c16"}),
    ("mlab --workspace . workflow approve --plan abc12345", "workflow_approve", False, {}),
    ("cd . && mlab measure unlock", "measure_unlock", False, {}),
    ("mlab workflow check && mlab workflow approve --plan abc12345", "workflow_approve", False, {}),
    ("echo $(mlab workflow approve --plan abc12345)", "workflow_approve", False, {}),
    ("a=`mlab conclude`", "conclude", False, {}),
    ("bash -c 'mlab conclude'", "conclude", False, {}),
    ("python -m mlab measure approve --plan abc12345", "measure_approve", False, {}),
    ("/opt/venv/bin/mlab workflow unlock", "workflow_unlock", False, {}),
    ("MLAB_X=1 mlab workflow decide", "workflow_decide", False, {}),
    ("mlab workflow approve --plan abc12345 > out.txt", "workflow_approve", False, {}),
    ("mlab workflow check", None, True, {}),
    ("mlab show r002 --part decision", None, True, {}),
    ("mlab history", None, True, {}),
    ("mlab workflow new --from r002", None, True, {}),
    ("mlab align m001", None, True, {}),
])
def test_gated_commands_are_recognised_in_every_form(command, kind, plain, args):
    found = approvals.classify(command)
    assert (found["kind"], found["plain"], found["args"]) == (kind, plain, args)
    assert (found["reason"] is None) == (plain or kind is None)


def test_references_in_chat():
    text = "See @r002 and @sim_0004, also @m001 and @r002/c16, then @sim_0004 again; the address a@b.c does not count, nor @r002/"
    assert views.references(text) == ["r002", "sim_0004", "m001", "r002/c16"]
