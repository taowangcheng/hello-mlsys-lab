"""Offline CLI acceptance: seeded workloads, task-specific holdouts and hard exits."""
import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import pytest
from mlab.core.io import ROOT, atomic, load
from mlab.core.workspace import Lab
from mlab.core.workloads import generate
from mlab.cli import demo
from test_workflow import ready, approve


@pytest.mark.parametrize("task", ["9.1", "9.2", "9.3"])
def test_task_demo_and_seeded_workload(workspace_path, task):
    summary = demo(workspace_path, task)
    assert summary["conclusion"] in {"mock_only", "unresolved"} and summary["hardware_budget"]["used"] == 1
    assert [row["run"][:3] for row in summary["alignment"]] == ["hw_"] and summary["alignment"][0]["sim"]
    lab = Lab(workspace_path)
    try:
        if task == "9.2":
            assert summary["conclusion"] == "mock_only"
        source = workspace_path / "task" / lab.task["frozen"]["workload_requests"]["path"]
        base = [json.loads(line) for line in source.read_text().splitlines()]
        assert generate(task, base, 42) == generate(task, base, 42)
        assert generate(task, base, 42) != generate(task, base, 43)
        if task == "9.3":
            development = next(r for r in lab.store.all("run") if r["candidate"] == summary["selected_candidate"] and r["split"] == "development" and r["role"] == "simulator")
            _, original = lab.result(development["id"])
            held = next(r for r in lab.store.all("run") if r["holdout"] == "one_dimension_change")
            _, changed = lab.result(held["id"])
            restored = copy.deepcopy(changed["strategy"])
            restored["serving"]["max_concurrency"] = original["strategy"]["serving"]["max_concurrency"]
            assert restored == original["strategy"] and changed["strategy"] != restored
            assert held["config_hash"] == development["config_hash"]
            path = workspace_path / "task" / changed["workload"]["requests"]["path"]
            assert [json.loads(line) for line in path.read_text().splitlines()] == base
            rows = generate(task, base, 1043)
            done = set()
            for row in rows:
                assert set(row.get("depends_on", [])) <= done
                done.add(row["request_id"])
            output = subprocess.run([sys.executable, "-m", "mlab", "--workspace", str(workspace_path), "run", "show", development["id"], "--timeline"], capture_output=True, encoding="utf-8", check=True)
            payload = json.loads(output.stdout)
            assert len(payload["rows"]) == 4 and payload["has_more"]
    finally:
        lab.close()


@pytest.mark.parametrize("committed", [False, True])
def test_hard_kill_transaction_recovery(workspace_path, committed):
    lab = ready(workspace_path)
    original = lab.next()
    lab.close()
    marker = workspace_path / "ready.txt"
    script = """
import sys, time
from pathlib import Path
from mlab.core.store import Store
root = Path(sys.argv[1])
s = Store(root)
s.begin()
s.put('crash_probe', 'probe', {'value': 7})
s.event('crash_probe', {'value': 7})
if sys.argv[2] == 'True': s.commit()
(root / 'ready.txt').write_text('ready')
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(workspace_path), str(committed)], cwd=ROOT)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker.exists()
    finally:
        process.kill()
        process.wait(timeout=10)
    lab = Lab(workspace_path)
    try:
        assert lab.store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert bool(lab.store.get("crash_probe", "probe")) == committed
        assert lab.next() == original
        lab.store.project()
        events = [json.loads(s) for s in (workspace_path / ".mlab/ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(e["type"] == "crash_probe" for e in events) == committed
    finally:
        lab.close()


def test_twenty_minute_job_yields_and_killed_worker_recovers(workspace_path):
    lab = ready(workspace_path)
    try:
        config = load(workspace_path / ".mlab/config.json")
        config["fixture_delay_s"] = 1200
        atomic(workspace_path / ".mlab/config.json", config)
        lab.config = config
        approve(lab)
        lab.submit()
        started = time.monotonic()
        state = lab.wait(0)
        assert time.monotonic() - started < 2
        assert state["eta_s"] > 120 and state["next_actions"][0]["do"] == "end_turn"
        for run in lab.store.all("run"):
            os.kill(run["pid"], signal.SIGKILL)  # Left unreaped: a zombie must still count as lost.
        lab.close()
        lab = Lab(workspace_path)
        assert lab.wait(10)["status"] == "completed"
        assert all(r["failure_class"] == "lost" for r in lab.store.all("run"))
        for run in lab.store.all("run"):
            assert lab.result(run["id"])[0]["verdict"] == "not_evaluated"
    finally:
        lab.close()
