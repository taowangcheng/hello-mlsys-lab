"""Records of earlier work for the agent, configurations and conclusions, and the read-only Lab the web page uses."""
import json
import os
import sqlite3
import subprocess
import sys
import pytest
from mlab.core.io import ROOT, LabError, atomic, load
from mlab.core.workspace import Lab
from mlab.core.history import brief, history, show
from mlab.core.reporting import report_data
from mlab.core.student import log_student
from test_workflow import decided, ready
from test_flywheels import measured, sweep


def story(path):
    """Two rounds, c20 selected, then one measurement of c20 and low_concurrency."""
    lab = ready(path)
    decided(lab)
    sweep(lab)
    decided(lab, "c20")
    measured(lab, [{"candidate": "c20"}, {"candidate": "low_concurrency"}])
    return lab


def cli(path, *args, env=None):
    process = subprocess.run([sys.executable, "-m", "mlab", "--workspace", str(path), *args], capture_output=True,
                             encoding="utf-8", cwd=ROOT, env=env)
    return process.returncode, process.stdout


def snapshot(root):
    # SQLite maintains its -wal and -shm files even for readers; they hold no data of ours.
    files = {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
             if p.is_file() and not p.name.endswith(("-wal", "-shm"))}
    db = sqlite3.connect(root / ".mlab/ledger.sqlite3")
    try:
        return files, db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
    finally:
        db.close()


def test_history_and_show_stay_small_and_cover_every_record(workspace_path):
    lab = story(workspace_path)
    lab.close()
    for args in (["history"], ["show", "r002"], ["show", "m001"], ["show", "r002/c16"], ["show", "r001/low_concurrency"],
                 ["show", "sim_0005"], ["show", "hw_0001"], ["show", "r002", "--part", "review"],
                 ["show", "m001", "--part", "points"]):
        code, out = cli(workspace_path, *args)
        data = json.loads(out)
        assert code == 0 and data["ok"], (args, out[:300])
        assert len(out.encode()) <= 1501 and "result_path" not in data or "show" in args, (args, len(out))
    code, out = cli(workspace_path, "show", "m001")
    shown = json.loads(out)
    assert [r["candidate"] for r in shown["vs_simulator"]] == ["c20", "low_concurrency"] and shown["status"] == "measured"
    for bad in ("nope", "exp_001", "r002/nope", "v002"):
        code, out = cli(workspace_path, "show", bad)
        assert code == 2 and json.loads(out)["error"]["code"] == "E_SCHEMA", bad
    lab = Lab(workspace_path)
    try:
        assert "c20" in brief(lab, "sim_0005") and "r002" in brief(lab, "sim_0005")
        assert brief(lab, "@r002").startswith("r002 (round 2") and "simulation" in brief(lab, "r002/c16")
        listed = history(lab)["items"]
        assert [i["kind"] for i in listed] == ["round", "round", "measurement"]
        assert listed[2]["points"] == ["r001/low_concurrency", "r002/c20"]
        assert show(lab, "r002", "decision")["text"].startswith("Student selects c20")
        c20 = show(lab, "r002/c20")
        assert [r["run"] for r in c20["measured"]] == ["hw_0001"] and c20["measured"][0]["gap_pct"] > 5
        assert c20["simulated"] and c20["selected"] and not show(lab, "r002/c16")["selected"]
    finally:
        lab.close()


def test_reusing_a_round_names_the_same_config_and_conclusions_add_up(workspace_path):
    from mlab.core.configs import evidence
    lab = story(workspace_path)
    try:
        with pytest.raises(LabError) as error:
            lab.new_workflow(source="r009")
        assert error.value.code == "E_SCHEMA" and not (workspace_path / "rounds/r003").exists()
        created = lab.new_workflow(source="r002")
        names = sorted(p.stem for p in (workspace_path / "rounds/r003/candidates").glob("*.yaml"))
        assert names == ["c16", "c20", "c24"] and load(workspace_path / created["plan_path"])["question"] == ""
        # The copies hold only the fields set on purpose, so the reused round passes the check like the original.
        plan = load(workspace_path / created["plan_path"])
        plan.update(question="Reuse the candidates of round 2", derived_requirements=[{"name": "SLA", "value": "< 2 s", "derivation": "the task"}])
        atomic(workspace_path / created["plan_path"], plan)
        atomic(workspace_path / created["hypothesis_path"], "## Hypothesis\n\nReuse.\n\n## What result would prove me wrong\n\nThe results differ.\n", text=True)
        checked = lab.check()
        assert checked["candidates"] == ["c16", "c20", "c24"]
        atomic(lab.folder() / "review.md", "## Comments on the plan\n\nAgree.\n", text=True)
        lab.approve(checked["plan_hash"])
        assert evidence(lab, "r002/c16")["names"] == ["r002/c16", "r003/c16"]
        first = lab.conclude()
        assert first["submission"] == "001" and first["selection"] == "r002/c20" and "hw_0001" in first["reason"]
        second = lab.conclude("r002/c16")
        assert second["submission"] == "002" and second["conclusion"] == "unresolved"
        state = lab.state()
        assert state["selection"] == "r002/c16" and state["conclusion"] == "unresolved" and state["stage"] == "approved"
        assert sorted(p.name for p in (workspace_path / "conclusions").glob("*.json")) == ["001.json", "002.json"]
        with pytest.raises(LabError) as error:
            lab.conclude("r009/c1")
        assert error.value.code == "E_SCHEMA"
    finally:
        lab.close()


def test_read_only_lab_changes_nothing(workspace_path):
    lab = story(workspace_path)
    lab.next()
    lab.close()
    before = snapshot(workspace_path)
    view = Lab(workspace_path, readonly=True)
    try:
        state = view.state()
        assert state["next_actions"][0]["step"] == "next_step"
        history(view)
        show(view, "m001")
        show(view, "r002/c16")
        view.comparison(["sim_0004", "sim_0005", "sim_0006"])
        report_data(view)
        view.next()
        with pytest.raises(sqlite3.OperationalError):
            view.store.put("probe", "x", {})
            view.store.commit()
    finally:
        view.close()
    assert snapshot(workspace_path) == before


def test_steps_follow_the_stages(workspace_path):
    lab = ready(workspace_path)
    try:
        def step():
            return lab.state()["next_actions"][0]["step"]
        atomic(lab.folder() / "hypothesis.md", "## Hypothesis\n\nOverload.\n\n## What result would prove me wrong\n\n", text=True)
        assert step() == "falsifier"
        atomic(lab.folder() / "hypothesis.md", "## Hypothesis\n\nOverload.\n\n## What result would prove me wrong\n\nLowering concurrency does not help.\n", text=True)
        assert step() == "plan_draft"
        lab.check()
        assert step() == "plan_review"
        atomic(lab.folder() / "review.md", "## Comments on the plan\n\nAgree.\n", text=True)
        lab.approve(lab.workflow()["checked_hash"])
        assert step() == "submit"
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        assert step() == "analysis_draft"
        atomic(lab.folder() / "analysis.md", "Analysis of sim_0003.", text=True)
        assert step() == "decision"
    finally:
        lab.close()


def test_the_web_hook_stays_quiet_and_the_terminal_hook_tags_the_position(workspace_path):
    lab = story(workspace_path)
    try:
        lab.measure_new()
        lab.store.commit()
        log = workspace_path / ".mlab/student.jsonl"
        log_student(workspace_path, "before", source="cli", kind="chat")
    finally:
        lab.close()
    payload = json.dumps({"prompt": "a prompt the page composed", "cwd": str(workspace_path)})
    env = {**os.environ, "MLAB_PROMPT_SOURCE": "web"}
    process = subprocess.run([sys.executable, "-m", "mlab", "hook", "user-prompt"], input=payload, capture_output=True,
                             encoding="utf-8", cwd=ROOT, env=env)
    assert process.returncode == 0 and "the page composed" not in log.read_text(encoding="utf-8")
    env.pop("MLAB_PROMPT_SOURCE")
    subprocess.run([sys.executable, "-m", "mlab", "hook", "user-prompt"], input=payload, capture_output=True, encoding="utf-8", cwd=ROOT, env=env)
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["source"] == "cli" and entry["workflow"] == "r002" and entry["measurement"] == "m002" and "experiment" not in entry
