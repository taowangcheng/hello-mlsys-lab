import json
import re
import subprocess
import sys
import time
from pathlib import Path
import pytest
from mlab.core.io import LabError, atomic, load, hashed, ROOT
from mlab.core.workspace import Lab, initialize
from mlab.backends.local import LocalBackend

HYPOTHESIS = ("## Hypothesis\n\nTest student: chunking may reduce short-request latency; overload is an alternative.\n\n"
              "## What result would prove me wrong\n\nIf only lower concurrency lowers p99, chunking is not the cause.\n")
REVIEW = "## Comments on the plan\n\n- Test student: keep all three candidates.\n"


def ready(path, task="9.2", backend="fixture"):
    initialize(path, task, backend)
    lab = Lab(path)
    lab.new_workflow(example=True)
    atomic(lab.folder() / "hypothesis.md", HYPOTHESIS, text=True)
    return lab


def approve(lab):
    checked = lab.check()
    atomic(lab.folder() / "review.md", REVIEW, text=True)
    lab.approve(checked["plan_hash"])


def decided(lab, candidate="low_concurrency"):
    approve(lab)
    lab.submit()
    assert lab.wait(20)["status"] == "completed"
    atomic(lab.folder() / "decision.md", f"Student selects {candidate} using {' '.join(lab.workflow()['runs'])}, with mock limitations.", text=True)
    lab.decide(candidate)


def test_full_workflow_validation_and_second_round(workspace_path):
    lab = ready(workspace_path)
    try:
        decided(lab)
        ids = lab.status()["run_ids"]
        report = lab.compare(ids)
        assert len(report["rows"]) == 3
        for split in ("repeat", "holdout"):
            lab.submit(split, "low_concurrency")
            assert lab.wait(20)["status"] == "completed"
        assert len(lab.store.all("run")) == 6
        assert all(r["id"].startswith("sim_") and r["role"] == "simulator" for r in lab.store.all("run"))
        assert all(lab.result(r["id"])[0]["evidence_level"] == "mock" for r in lab.store.all("run"))
        # A new workflow is permitted after confirmed decision, and preserves the previous iteration.
        old_folder = lab.folder()
        lab.new_workflow()
        assert lab.next()["stage"] == "hypothesis"
        assert "keep all three" in load(old_folder / "approval.json")["review"]
        assert lab.next()["previous_decision"]
    finally:
        lab.close()


def test_hypothesis_needs_a_falsification_condition_and_approval_needs_comments(workspace_path):
    lab = ready(workspace_path)
    try:
        atomic(lab.folder() / "hypothesis.md", "## Hypothesis\n\nOverload causes the tail.\n\n## What result would prove me wrong\n\n", text=True)
        assert lab.next()["stage"] == "hypothesis" and lab.next()["next_actions"][0]["actor"] == "student"
        with pytest.raises(LabError) as error:
            lab.check()
        assert error.value.code == "E_SCHEMA" and error.value.actions[0]["do"] == "ask"
        atomic(lab.folder() / "hypothesis.md", HYPOTHESIS, text=True)
        checked = lab.check()
        with pytest.raises(LabError) as error:
            lab.approve(checked["plan_hash"])
        assert error.value.code == "E_NEEDS_APPROVAL" and error.value.actions[0]["actor"] == "agent"
        atomic(lab.folder() / "review.md", REVIEW, text=True)
        assert lab.approve(checked["plan_hash"])["status"] == "approved"
    finally:
        lab.close()


def test_approval_stale_and_frozen_task(workspace_path):
    lab = ready(workspace_path)
    try:
        approve(lab)
        path = lab.folder() / "candidates/baseline.yaml"
        data = load(path)
        data["strategy"]["serving"]["max_concurrency"] = 15
        atomic(path, data)
        with pytest.raises(LabError, match="changed after approval"):
            lab.submit()
        path = workspace_path / "task/task.yaml"
        task = load(path)
        task["constraints"][0]["value"] = 9999
        atomic(path, task)
        with pytest.raises(LabError) as error:
            lab.check()
        assert error.value.code == "E_FROZEN_CHANGED"
    finally:
        lab.close()


def test_requirements_and_mechanisms(workspace_path):
    lab = ready(workspace_path)
    try:
        path = lab.folder() / "plan.yaml"
        data = load(path)
        data["derived_requirements"] = []
        atomic(path, data)
        with pytest.raises(LabError) as error:
            lab.check()
        assert error.value.code == "E_SCHEMA"
        data["derived_requirements"] = [{"name": "memory", "value": "3 GiB", "derivation": "parameters times bytes"}]
        atomic(path, data)
        path = lab.folder() / "candidates/baseline.yaml"
        candidate = load(path)
        candidate["strategy"]["speculative"] = True
        atomic(path, candidate)
        with pytest.raises(LabError) as error:
            lab.check()
        assert error.value.code == "E_MECHANISM_UNSUPPORTED"
    finally:
        lab.close()


def test_idempotency_cancel_terminal_immutable(workspace_path):
    lab = ready(workspace_path)
    try:
        config = load(workspace_path / ".mlab/config.json")
        config["fixture_delay_s"] = 3
        atomic(workspace_path / ".mlab/config.json", config)
        approve(lab)
        first = lab.submit()
        second = lab.submit()
        assert first["run_ids"] == second["run_ids"] and second["created"] == 0
        start = time.monotonic()
        assert lab.wait(0)["status"] != "completed"
        assert time.monotonic() - start < 2
        backend = LocalBackend(workspace_path, "fixture")
        for identifier in first["run_ids"]:
            backend.cancel({"run_id": identifier})
        hashes = [hashed(workspace_path / r["folder"] / "result.json") for r in lab.store.all("run")]
        time.sleep(.5)
        assert all(r["status"] == "cancelled" for r in lab.store.all("run"))
        assert hashes == [hashed(workspace_path / r["folder"] / "result.json") for r in lab.store.all("run")]
    finally:
        lab.close()


def test_holdout_gate_and_recovery(workspace_path):
    lab = ready(workspace_path)
    try:
        approve(lab)
        with pytest.raises(LabError) as error:
            lab.submit("holdout", "baseline")
        assert error.value.code == "E_NEEDS_APPROVAL"
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        original = lab.next()
        state = workspace_path / "state.yaml"
        state.unlink()
        lab.close()
        lab = Lab(workspace_path)
        restored = lab.next()
        assert original == restored
        assert restored["budget"] == {"simulator": "unlimited", "hardware": {"used": 0, "remaining": 8, "total": 8}}
    finally:
        lab.close()


def test_retry_lock_and_unlock(workspace_path):
    lab = ready(workspace_path)
    try:
        for _ in range(3):
            lab.note_error("E_SCHEMA")
        with pytest.raises(LabError) as error:
            lab.check()
        assert error.value.code == "E_REPEAT_LIMIT"
        lab.unlock()
        assert lab.check()["planned_runs"] == 3
    finally:
        lab.close()


def test_api_failure_is_retryable(workspace_path, monkeypatch):
    # A closed local port exercises real worker failure without contacting a remote service.
    monkeypatch.setenv("MLAB_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MLAB_LLM_API_KEY", "unused")
    monkeypatch.setenv("MLAB_LLM_MODEL", "unused")
    lab = ready(workspace_path, backend="llm")
    try:
        approve(lab)
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        assert all(r["failure_class"] == "backend_error" for r in lab.store.all("run"))
        assert lab.next()["stage"] == "retry_backend"
    finally:
        lab.close()


@pytest.mark.parametrize("task", ["9.1", "9.3"])
def test_other_tasks(workspace_path, task):
    lab = ready(workspace_path, task=task)
    try:
        approve(lab)
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        results = [lab.result(r["id"])[0] for r in lab.store.all("run")]
        if task == "9.1":
            assert any(r["failure_class"] == "oom" and r["verdict"] == "constraints_violated" for r in results)
        else:
            assert len(results) >= 3
            assert all(r["metrics"]["makespan_s"] + 1e-9 >= r["metrics"]["critical_path_s"] for r in results), [(r["metrics"]["makespan_s"], r["metrics"]["critical_path_s"]) for r in results]
    finally:
        lab.close()


def test_artifacts_and_comparison_guard(workspace_path):
    lab = ready(workspace_path)
    try:
        approve(lab)
        lab.submit()
        lab.wait(20)
        run = lab.store.all("run")[0]
        folder = workspace_path / run["folder"]
        assert all((folder / name).is_file() for name in ["spec.json", "manifest.json", "result.json", "requests.jsonl", "events.jsonl", "logs.txt"])
        manifest = load(folder / "manifest.json")
        assert manifest["role"] == "simulator" and "calibration" not in manifest and "calibration" not in manifest["spec"]
        result = load(folder / "result.json")
        result["metrics"]["completion_rate"] = 0
        atomic(folder / "result.json", result)
        with pytest.raises(LabError) as error:
            lab.result(run["id"])
        assert error.value.code == "E_FROZEN_CHANGED"
    finally:
        lab.close()


def test_cli_json_error_hook_and_log_bounds(workspace_path):
    lab = ready(workspace_path)
    lab.close()
    for args, expected in [(["next", "--hook"], 0), (["workflow", "approve", "--plan", "abcd"], 3), (["workflow", "wat"], 2)]:
        process = subprocess.run([sys.executable, "-m", "mlab", "--workspace", str(workspace_path), *args], capture_output=True, encoding="utf-8", cwd=ROOT)
        assert process.returncode == expected, process.stderr + process.stdout
        output = json.loads(process.stdout)
        assert len(process.stdout.splitlines()) == 1
        if "--hook" in args:
            assert "additionalContext" in output["hookSpecificOutput"]
        else:
            assert output["ok"] is False and len(process.stdout.encode()) <= 1501


def test_simulator_batch_limit_rejects_without_orphan_runs(workspace_path):
    lab = ready(workspace_path)
    try:
        approve(lab)
        lab.task["budget"]["simulator"]["runs_per_batch"] = 2
        with pytest.raises(LabError) as error:
            lab.submit()
        assert error.value.code == "E_BUDGET"
        assert not list(lab.folder().glob("runs/*/spec.json"))
        assert not lab.store.all("run")
    finally:
        lab.close()


def test_real_worker_timeout_and_recovery(workspace_path):
    lab = ready(workspace_path)
    try:
        config = load(workspace_path / ".mlab/config.json")
        config.update(fixture_delay_s=3, run_timeout_s=.2)
        atomic(workspace_path / ".mlab/config.json", config)
        approve(lab)
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        assert all(r["status"] == "timed_out" for r in lab.store.all("run"))
        assert all(lab.result(r["id"])[0]["verdict"] == "not_evaluated" for r in lab.store.all("run"))
    finally:
        lab.close()


def test_unrelated_slip_after_a_fix_does_not_lock(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    def mlab(*args):
        process = subprocess.run([sys.executable, "-m", "mlab", "--workspace", str(workspace_path), *args], capture_output=True, encoding="utf-8", cwd=ROOT)
        return json.loads(process.stdout)
    mlab("workflow", "new")
    folder = workspace_path / "rounds/r001"
    atomic(folder / "hypothesis.md", HYPOTHESIS, text=True)
    assert mlab("workflow", "check")["error"]["code"] == "E_SCHEMA"
    plan = load(folder / "plan.yaml")
    plan.update(question="q", derived_requirements=[{"name": "a", "value": 2000, "derivation": "c"}])
    atomic(folder / "plan.yaml", plan)
    assert mlab("workflow", "check")["error"]["code"] == "E_SCHEMA"
    plan["derived_requirements"][0]["value"] = "2000"
    atomic(folder / "plan.yaml", plan)
    assert mlab("workflow", "check")["ok"]
    assert mlab("run", "show", "sim_9999")["error"]["code"] == "E_SCHEMA"
    assert mlab("next")["stage"] == "plan"


@pytest.mark.parametrize("command", ["check", "approve", "submit", "decide"])
def test_each_progress_command_clears_repeated_mistakes(workspace_path, command):
    lab = ready(workspace_path)
    try:
        checked = lab.check()
        atomic(lab.folder() / "review.md", REVIEW, text=True)
        def mistakes():
            lab.note_error("E_SCHEMA")
            lab.note_error("E_INCOMPARABLE")
            assert lab.workflow()["errors"]["E_SCHEMA"] == 1
        if command == "check":
            mistakes()
            lab.check()
        else:
            if command != "approve":
                lab.approve(checked["plan_hash"])
            if command == "approve":
                mistakes()
                lab.approve(checked["plan_hash"])
            elif command == "submit":
                mistakes()
                lab.submit()
                assert lab.wait(20)["status"] == "completed"
            else:
                lab.submit()
                assert lab.wait(20)["status"] == "completed"
                atomic(lab.folder() / "decision.md", "Student decision based on " + lab.workflow()["runs"][0], text=True)
                mistakes()
                lab.decide()
        assert not {"E_SCHEMA", "E_INCOMPARABLE"} & set(lab.workflow()["errors"])
    finally:
        lab.close()


def test_decision_must_cite_a_run_of_the_round(workspace_path):
    lab = ready(workspace_path)
    try:
        approve(lab)
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        atomic(lab.folder() / "decision.md", "Keep low concurrency.", text=True)
        with pytest.raises(LabError) as error:
            lab.decide("low_concurrency")
        assert error.value.code == "E_SCHEMA"
        atomic(lab.folder() / "decision.md", "Keep low concurrency: " + lab.workflow()["runs"][2], text=True)
        assert lab.decide("low_concurrency")["selected_candidate"] == "low_concurrency"
    finally:
        lab.close()


def test_read_only_commands_and_repeated_failures_still_lock(workspace_path):
    lab = ready(workspace_path)
    try:
        for _ in range(2):
            lab.note_error("E_SCHEMA")
        lab.next()
        lab.status()
        lab.note_error("E_SCHEMA")
        assert lab.workflow()["locked"]
        with pytest.raises(LabError) as error:
            lab.check()
        assert error.value.code == "E_REPEAT_LIMIT"
    finally:
        lab.close()


def inject_backend_failure(path, on=True):
    config = load(path / ".mlab/config.json")
    config.pop("fixture_failure", None)
    if on:
        config["fixture_failure"] = "backend_error"
    atomic(path / ".mlab/config.json", config)


def test_backend_failure_can_be_retried(workspace_path):
    lab = ready(workspace_path)
    try:
        inject_backend_failure(workspace_path)
        approve(lab)
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        state = lab.next()
        assert state["stage"] == "retry_backend" and state["next_actions"][0]["actor"] == "agent"
        assert not lab.workflow()["locked"]
        inject_backend_failure(workspace_path, on=False)
        assert lab.submit()["created"] == 3
        assert lab.wait(20)["status"] == "completed"
        assert lab.next()["stage"] == "analysis_and_decision"
        rows = lab.compare([])["rows"]
        assert len(rows) == 3 and all(r["verdict"] != "not_evaluated" for r in rows)
    finally:
        lab.close()


def test_repeated_backend_failure_stops_until_the_student_unlocks(workspace_path):
    lab = ready(workspace_path)
    try:
        inject_backend_failure(workspace_path)
        approve(lab)
        for _ in range(3):
            lab.submit()
            assert lab.wait(20)["status"] == "completed"
        with pytest.raises(LabError) as error:
            lab.submit()
        assert error.value.code == "E_REPEAT_LIMIT" and error.value.actions[0]["do"] == "end_turn"
        lab.unlock()
        assert lab.submit()["created"] == 3
        assert lab.wait(20)["status"] == "completed"
    finally:
        lab.close()


def test_validation_backend_failure_names_the_exact_retry(workspace_path):
    lab = ready(workspace_path)
    try:
        decided(lab)
        inject_backend_failure(workspace_path)
        lab.submit("repeat", "low_concurrency")
        assert lab.wait(20)["status"] == "completed"
        action = lab.next()["next_actions"][0]
        assert action["actor"] == "agent" and "mlab validate --split repeat --candidate low_concurrency" in action["say"]
        inject_backend_failure(workspace_path, on=False)
        lab.submit("repeat", "low_concurrency")
        assert lab.wait(20)["status"] == "completed"
        assert lab.next()["next_actions"][0]["actor"] == "student"
    finally:
        lab.close()


def test_next_names_who_acts_and_the_exact_command(workspace_path):
    lab = ready(workspace_path)
    try:
        checked = lab.check()
        action = lab.next()["next_actions"][0]
        assert action["actor"] == "student" and checked["plan_hash"] in action["say"] and "review.md" in action["say"]
        atomic(lab.folder() / "review.md", REVIEW, text=True)
        lab.approve(checked["plan_hash"])
        assert lab.next()["next_actions"][0]["say"].startswith("mlab workflow submit")
        assert lab.next()["context"]["review_path"].endswith("review.md")
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        action = lab.next()["next_actions"][0]
        assert action["actor"] == "agent" and action["say"].startswith("mlab compare") and "falsification" in action["say"]
        atomic(lab.folder() / "analysis.md", "Analysis of sim_0001.", text=True)
        action = lab.next()["next_actions"][0]
        assert action["actor"] == "student" and "mlab workflow decide --candidate ID" in action["say"]
    finally:
        lab.close()


def test_compare_reports_the_key_metric_without_student_numbers(workspace_path):
    lab = ready(workspace_path)
    try:
        decided(lab)
        compared = lab.compare([])
        assert compared["key_metric"] == "ttft_ms.p99" and "calibration" not in compared
        assert all(set(r) == {"run_id", "candidate", "verdict", "key_metric_value", "objective_value", "goodput_tok_s"} for r in compared["rows"])
    finally:
        lab.close()


def cli(path, *args):
    return subprocess.run([sys.executable, "-m", "mlab", "--workspace", str(path), *args], capture_output=True, encoding="utf-8", cwd=ROOT)


def test_every_command_says_what_comes_next_and_blocks_stop_the_agent(workspace_path):
    lab = ready(workspace_path)
    lab.close()
    checked = json.loads(cli(workspace_path, "workflow", "check").stdout)
    assert checked["next_actions"][0]["actor"] == "student"
    blocked = json.loads(cli(workspace_path, "workflow", "submit").stdout)
    assert blocked["error"]["code"] == "E_NEEDS_APPROVAL"
    assert [a["actor"] for a in blocked["next_actions"]] == ["agent", "student"] and blocked["next_actions"][0]["do"] == "end_turn"
    atomic(workspace_path / "rounds/r001/review.md", REVIEW, text=True)
    assert cli(workspace_path, "workflow", "approve", "--plan", checked["plan_hash"]).returncode == 0
    submitted = json.loads(cli(workspace_path, "workflow", "submit").stdout)
    assert "next_actions" in submitted
    assert json.loads(cli(workspace_path, "workflow", "wait").stdout)["status"] == "completed"
    shown = json.loads(cli(workspace_path, "run", "show", submitted["run_ids"][0]).stdout)
    assert shown["candidate"] == "baseline" and shown["split"] == "development" and shown["role"] == "simulator"


def test_hook_reports_errors_into_the_session(workspace_path):
    lab = ready(workspace_path)
    lab.close()
    with (workspace_path / "task/task.yaml").open("a", encoding="utf-8") as stream:
        stream.write("# tampered\n")
    process = cli(workspace_path, "next", "--hook")
    assert process.returncode == 0
    context = json.loads(process.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "E_FROZEN_CHANGED" in context


def test_report_is_readable_and_keeps_the_student_review(workspace_path):
    from mlab.core.reporting import build_report
    lab = ready(workspace_path)
    try:
        decided(lab, "baseline")
        for split in ("repeat", "holdout"):
            lab.submit(split, "baseline")
            assert lab.wait(20)["status"] == "completed"
        assert lab.conclude()["conclusion"] == "unresolved"
        text = Path(build_report(lab)["result_path"]).read_text(encoding="utf-8")
    finally:
        lab.close()
    assert "Simulation round 1" in text and "# Report: 9.2 " in text and "'schema'" not in text
    assert "### Comments on the plan" in text and "keep all three candidates" in text and "prediction" not in text.lower()
    assert "#### Comments on the plan" not in text  # review.md's own heading would repeat the section's.
    assert "holdout: short_prompt_distribution" in text and "| ttft_ms.p99 | goodput_tok_s |" in text
    table = text[text.index("## Run evidence"):]
    assert not re.search(r"\d\.\d{2,}", table)
