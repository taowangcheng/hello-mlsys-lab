"""Two flywheels on fixture and the hardware stand-in: measurements, their comparison with the simulator, conclusions."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from mlab.core.io import ROOT, LabError, atomic, load
from mlab.core.workspace import Lab
from mlab.core.reporting import build_report
from mlab.backends.local import load_requests, standin
from test_workflow import decided, ready


def sweep(lab, concurrencies=(16, 20, 24)):
    """A second round whose candidates differ from low_concurrency only in max_concurrency."""
    lab.new_workflow(example=True)
    folder = lab.folder()
    atomic(folder / "hypothesis.md", "## Hypothesis\n\nToo much concurrency is the main cause.\n\n## What result would prove me wrong\n\nConcurrency 24 also meets the SLA.\n", text=True)
    base = load(folder / "candidates/low_concurrency.yaml")
    names = []
    for value in concurrencies:
        candidate = json.loads(json.dumps(base))
        candidate.update(id=f"c{value}", rationale=f"concurrency {value}")
        candidate["strategy"]["serving"]["max_concurrency"] = value
        atomic(folder / f"candidates/c{value}.yaml", candidate)
        names.append(f"candidates/c{value}.yaml")
    plan = load(folder / "plan.yaml")
    plan["candidates"] = names
    atomic(folder / "plan.yaml", plan)


def two_rounds(lab, choice="c20"):
    decided(lab)
    sweep(lab)
    decided(lab, choice)


def points(lab, entries):
    name = lab.measurement()["id"]
    atomic(lab.mfolder() / "points.yaml", {"schema": "mlab.v1", "measurement": name, "points": [
        {"workload": "development", "runs": 1, "rationale": "the student's reason", **entry} for entry in entries]})


def measured(lab, entries):
    """Create, check, approve, submit and wait for one measurement."""
    lab.measure_new()
    points(lab, entries)
    checked = lab.measure_check()
    if checked.get("status") == "simulating":
        assert lab.measure_wait(20)["status"] == "completed"
        checked = lab.measure_check()
    lab.measure_approve(checked["plan_hash"])
    lab.measure_submit()
    assert lab.measure_wait(20)["status"] == "completed"
    return checked


def snapshot(root):
    # Sizes and times only: opening SQLite's files in this process would drop the locks its connections hold.
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


def test_measurement_comparison_and_conclusion(workspace_path):
    lab = ready(workspace_path)
    try:
        two_rounds(lab)
        checked = measured(lab, [{"candidate": "c20", "rationale": "my pick, but the simulation is only 3% under the SLA"},
                                 {"candidate": "low_concurrency", "rationale": "a low-concurrency control"}])
        assert [(p["candidate"], p["sim_margin_pct"]) for p in checked["points"]] == [("c20", 3.0), ("low_concurrency", 30.7)]
        assert checked["metric"] == "ttft_ms.p99" and lab.hardware_budget() == {"used": 2, "remaining": 6, "total": 8}
        # Nothing waits on a comparison: next only says where the pairs are.
        state = lab.next()
        assert state["measurement_stage"] == "measured" and "mlab align m001" in state["next_actions"][0]["say"]
        before = snapshot(lab.root)
        aligned = lab.align()
        assert snapshot(lab.root) == before
        rows = {r["candidate"]: r for r in aligned["rows"]}
        assert rows["c20"]["verdict_flip"] and rows["c20"]["beyond_noise"] and rows["c20"]["shift"] == "all"
        assert rows["low_concurrency"]["beyond_noise"] and not rows["low_concurrency"]["verdict_flip"]
        assert rows["c20"]["gap_pct"] > rows["low_concurrency"]["gap_pct"] > 5
        detail = {r["candidate"]: r for r in lab.alignment(lab.measurement())["rows"]}
        assert detail["c20"]["slowest_ratio"]["median"] > detail["low_concurrency"]["slowest_ratio"]["median"]
        measured(lab, [{"candidate": "c16", "runs": 2, "rationale": "final validation"},
                       {"candidate": "c16", "workload": "short_prompt_distribution", "rationale": "holdout"},
                       {"candidate": "c16", "workload": "long_prompt_distribution", "rationale": "holdout"}])
        assert lab.hardware_budget()["used"] == 6
        assert lab.align("m001")["rows"] == aligned["rows"]
        assert not [r for r in lab.align()["rows"] if r["verdict_flip"]]
        # The second round selected c20, which violates the SLA on hardware; the student concludes on c16 instead.
        assert lab.conclusion_for(lab.current()["selection"], lab.store.all("run"))[0] == "unresolved"
        closed = lab.conclude("r002/c16")
        assert closed["conclusion"] == "mock_only" and closed["selection"] == "r002/c16" and "hardware stand-in" in closed["reason"]
        assert lab.next()["selection"] == "r002/c16"
        text = Path(build_report(lab)["result_path"]).read_text(encoding="utf-8")
        assert "## Measurement m001" in text and "### Hardware points" in text and "### Simulation vs. hardware" in text and "calibrat" not in text.lower()
    finally:
        lab.close()


def test_hardware_conflict_with_the_selection_is_unresolved(workspace_path):
    lab = ready(workspace_path)
    try:
        two_rounds(lab)
        measured(lab, [{"candidate": "c20", "rationale": "check it"}])
        run = lab.align()["rows"][0]["run"]
        closed = lab.conclude()
        assert closed["conclusion"] == "unresolved" and run in closed["reason"]
    finally:
        lab.close()


def test_align_needs_hardware_results(workspace_path):
    lab = ready(workspace_path)
    try:
        decided(lab)
        lab.measure_new()
        with pytest.raises(LabError) as error:
            lab.align()
        assert error.value.code == "E_NEEDS_APPROVAL"
        with pytest.raises(LabError) as error:
            lab.align("m009")
        assert error.value.code == "E_SCHEMA"
    finally:
        lab.close()


def test_hardware_budget_and_point_rules(workspace_path):
    lab = ready(workspace_path)
    try:
        with pytest.raises(LabError) as error:
            lab.measure_new()
        assert error.value.code == "E_NEEDS_APPROVAL"
        decided(lab)
        lab.measure_new()
        points(lab, [{"candidate": "low_concurrency", "runs": 4}, {"candidate": "baseline"}])
        with pytest.raises(LabError) as error:
            lab.measure_check()
        assert error.value.code == "E_BUDGET"
        points(lab, [{"candidate": "low_concurrency"}, {"candidate": "low_concurrency"}])
        with pytest.raises(LabError, match="repeats another point"):
            lab.measure_check()
        points(lab, [{"candidate": "missing"}])
        with pytest.raises(LabError, match="not a candidate"):
            lab.measure_check()
        points(lab, [{"candidate": "low_concurrency", "rationale": " "}])
        with pytest.raises(LabError, match="rationale"):
            lab.measure_check()
        lab.task["budget"]["hardware"]["runs_total"] = 1
        points(lab, [{"candidate": "low_concurrency", "runs": 2}])
        with pytest.raises(LabError) as error:
            lab.measure_check()
        assert error.value.code == "E_BUDGET" and "1 of 1" in str(error.value)
        with pytest.raises(LabError) as error:
            lab.measure_new()
        assert error.value.code == "E_NEEDS_APPROVAL"
    finally:
        lab.close()


def test_environment_failures_use_no_budget_and_rerun(workspace_path):
    lab = ready(workspace_path)
    try:
        decided(lab)
        config = load(workspace_path / ".mlab/config.json")
        atomic(workspace_path / ".mlab/config.json", {**config, "hardware_failure": "gpu_busy"})
        measured(lab, [{"candidate": "low_concurrency"}])
        state = lab.next()
        assert state["measurement_stage"] == "measure_retry" and state["budget"]["hardware"]["used"] == 0
        assert lab.align()["rows"][0]["hw"] is None
        atomic(workspace_path / ".mlab/config.json", config)
        assert lab.measure_submit()["created"] == 1
        assert lab.measure_wait(20)["status"] == "completed"
        assert lab.next()["measurement_stage"] == "measured" and lab.hardware_budget()["used"] == 1
        assert lab.align()["rows"][0]["hw"] is not None
    finally:
        lab.close()


def test_standin_is_deterministic_with_designed_discrepancies(workspace_path):
    lab = ready(workspace_path)
    try:
        decided(lab)
        frozen = load(lab.folder() / "frozen_plan.json")
        chunked = next(c for c in frozen["candidates"] if c["id"] == "chunked_256")
        spec = lab.make_spec(chunked, 42, 0, "development")
        assert "calibration" not in spec
        requests = load_requests(lab.root, spec)
        first, observed = standin(spec, requests, 4.0)
        again, _ = standin(spec, requests, 4.0)
        repeat, _ = standin(lab.make_spec(chunked, 42, 1, "repeat"), requests, 4.0)
        assert first == again and first != repeat
        assert observed["effective_config"]["serving"]["chunked_prefill_tokens"] == 512
        measured(lab, [{"candidate": "chunked_256"}])
        aligned = lab.align()
        assert aligned["rows"][0]["config_diff"] == ["serving.chunked_prefill_tokens: 256 → 512"]
        manifest = load(lab.root / lab.store.get("run", aligned["rows"][0]["run"])["folder"] / "manifest.json")
        assert manifest["role"] == "hardware" and "stand-in" in manifest["environment"]["kind"]
    finally:
        lab.close()


def test_prompt_hook_logs_verbatim_and_never_blocks(workspace_path):
    lab = ready(workspace_path)
    lab.close()
    def hook(payload, cwd):
        return subprocess.run([sys.executable, "-m", "mlab", "hook", "user-prompt"], input=payload, capture_output=True,
                              encoding="utf-8", cwd=cwd)
    done = hook(json.dumps({"prompt": "I think it is overload", "session_id": "s1", "cwd": str(workspace_path)}), ROOT)
    assert done.returncode == 0 and done.stdout == ""
    assert hook("not json", workspace_path).returncode == 0
    assert hook(json.dumps({"prompt": "outside"}), ROOT).returncode == 0
    lines = [json.loads(line) for line in (workspace_path / ".mlab/student.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["text"] for line in lines] == ["I think it is overload"]
    lab = Lab(workspace_path)
    try:
        assert "I think it is overload" in Path(build_report(lab)["result_path"]).read_text(encoding="utf-8")
    finally:
        lab.close()
