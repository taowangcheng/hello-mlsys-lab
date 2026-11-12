import sys
from pathlib import Path
import pytest
from mlab.core.io import ROOT, atomic, load
from mlab.core.workspace import Lab, initialize
from mlab.core.evidence import check_claims
from mlab.core.reporting import build_report

sys.path.insert(0, str(ROOT / "scripts"))
from classroom import classroom_loop, ScriptedStudent


class FixtureTeacher:
    """Controller regression double; real Claude integration is separate."""
    def __init__(self, root):
        self.root = root
    def turn(self, phase, prompt):
        lab = Lab(self.root)
        try:
            folder = lab.folder()
            if phase == "plan":
                plan = load(folder / "plan.yaml")
                plan["question"] = "Test overload against prefill blocking"
                plan["derived_requirements"] = [{"name": "TTFT", "value": "under 2000ms", "derivation": "frozen SLA"}]
                atomic(folder / "plan.yaml", plan)
                if lab.workflow()["iteration"] > 1:
                    p = folder / "candidates/low_concurrency.yaml"
                    candidate = load(p)
                    candidate["strategy"]["serving"]["max_concurrency"] = 4
                    atomic(p, candidate)
                lab.check()
            elif phase == "run":
                lab.submit()
                assert lab.wait(20)["status"] == "completed"
            elif phase == "analysis":
                lab.compare([])
                run = next(r for r in lab.workflow()["runs"] if lab.store.get("run", r)["failure_class"] is None)
                result, _ = lab.result(run)
                atomic(folder / "analysis.md", f"mock_only placeholder numbers: evidence {run}; test the alternative explanation, claim no hardware improvement.", text=True)
                atomic(folder / "analysis.claims.json", [{"run_id": run, "metric": "ttft_ms.p99", "value": result["metrics"]["ttft_ms"]["p99"]}])
            elif phase == "validate":
                chosen = lab.workflow()["selected_candidate"]
                for split in ("repeat", "holdout"):
                    lab.submit(split, chosen)
                    assert lab.wait(20)["status"] == "completed"
        finally:
            lab.close()


def test_two_round_classroom_and_authoritative_report(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    report = classroom_loop(workspace_path, FixtureTeacher(workspace_path), ScriptedStudent(2))
    assert report["runs"] == 9 and report["conclusion"] == "mock_only"
    lab = Lab(workspace_path)
    try:
        assert lab.workflow()["iteration"] == 2
        assert lab.workflow()["selected_candidate"] == "low_concurrency"
        data = load(Path(report["data_path"]))
        assert len(data["iterations"]) == 2
        assert all("agree with the plan" in i["review"] for i in data["iterations"])
        assert all("Scripted student" in i["hypothesis"] and "What result would prove me wrong" in i["hypothesis"] for i in data["iterations"])
        assert all(r["role"] == "simulator" for r in data["runs"])
        assert data["selected_candidate"] == "low_concurrency"
        assert check_claims(lab)["valid"]
        claims_path = lab.folder() / "analysis.claims.json"
        claims = load(claims_path)
        claims[0]["value"] += 1000
        atomic(claims_path, claims)
        assert not check_claims(lab)["valid"]
        # Compare defaults exclude repeat and holdout and show the task's key metric.
        compared = lab.compare([])
        assert len(compared["rows"]) == 3
        assert all(r["key_metric_value"] is not None for r in compared["rows"])
    finally:
        lab.close()


def test_student_rejection_and_resume_preserve_the_review_gate(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    class Reject(ScriptedStudent):
        def confirm(self, label):
            return False
    classroom_loop(workspace_path, FixtureTeacher(workspace_path), Reject())
    lab = Lab(workspace_path)
    try:
        assert lab.next()["stage"] == "plan"
        assert not (lab.folder() / "approval.json").exists()
        assert not lab.store.all("run")
        before = (lab.folder() / "hypothesis.md").read_text(encoding="utf-8")
    finally:
        lab.close()
    report = classroom_loop(workspace_path, FixtureTeacher(workspace_path), ScriptedStudent(1))
    assert report["conclusion"] == "mock_only"
    lab = Lab(workspace_path)
    try:
        assert (lab.folder() / "hypothesis.md").read_text(encoding="utf-8") == before
        assert len(lab.store.all("run")) == 6
    finally:
        lab.close()


def test_verified_report_rejects_tampered_result(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    classroom_loop(workspace_path, FixtureTeacher(workspace_path), ScriptedStudent(1))
    lab = Lab(workspace_path)
    try:
        run = lab.store.all("run")[0]
        path = workspace_path / run["folder"] / "result.json"
        result = load(path)
        result["metrics"]["goodput_tok_s"] = 99999
        atomic(path, result)
        with pytest.raises(Exception, match="changed"):
            build_report(lab)
    finally:
        lab.close()


def test_resubmit_fixture_is_stable_when_gateway_port_changes(workspace_path, monkeypatch):
    initialize(workspace_path, "9.2", "fixture")
    classroom_loop(workspace_path, FixtureTeacher(workspace_path), ScriptedStudent(1))
    lab = Lab(workspace_path)
    try:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:12345")
        first = lab.submit("repeat", "low_concurrency")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:54321")
        second = lab.submit("repeat", "low_concurrency")
        assert first["run_ids"] == second["run_ids"] and second["created"] == 0
        assert len(lab.store.all("run")) == 6
    finally:
        lab.close()


def test_rejected_long_requests_cannot_win_by_tail_alone(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    lab = Lab(workspace_path)
    try:
        lab.new_workflow(example=True)
        atomic(lab.folder() / "hypothesis.md", "## Hypothesis\n\nScripted metric-gaming regression\n\n## What result would prove me wrong\n\nCompletion stays complete.\n", text=True)
        config = load(workspace_path / ".mlab/config.json")
        config.update(fixture_reject_category="long", fixture_log_bytes=10000)
        atomic(workspace_path / ".mlab/config.json", config)
        checked = lab.check()
        atomic(lab.folder() / "review.md", "## Comments on the plan\n\nAgree.\n", text=True)
        lab.approve(checked["plan_hash"])
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        for run in lab.store.all("run"):
            result, _ = lab.result(run["id"])
            assert result["metrics"]["completion_rate"] < 1
            assert result["verdict"] == "constraints_violated"
            assert (workspace_path / run["folder"] / "logs.txt").stat().st_size > 10000
    finally:
        lab.close()


def test_report_uses_confirmed_analysis_and_checks_locked_student_evidence(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    classroom_loop(workspace_path, FixtureTeacher(workspace_path), ScriptedStudent(1))
    lab = Lab(workspace_path)
    try:
        original = build_report(lab)
        data = load(Path(original["data_path"]))
        atomic(lab.folder() / "analysis.md", "Later unconfirmed edit", text=True)
        atomic(lab.folder() / "decision.md", "Later unconfirmed decision", text=True)
        updated = load(Path(build_report(lab)["data_path"]))
        assert updated["iterations"][0]["analysis"] == data["iterations"][0]["analysis"]
        assert updated["iterations"][0]["decision"] == data["iterations"][0]["decision"]
        for name in ("approval.json", "analysis.confirmed.json", "decision.confirmed.json"):
            path = lab.folder() / name
            saved = load(path)
            altered = dict(saved)
            altered["tampered"] = True
            atomic(path, altered)
            with pytest.raises(Exception, match="report source changed"):
                build_report(lab)
            atomic(path, saved)
    finally:
        lab.close()


def test_backend_outage_saves_and_resumes_without_a_dead_end(workspace_path, capsys):
    initialize(workspace_path, "9.2", "fixture")
    config = load(workspace_path / ".mlab/config.json")
    atomic(workspace_path / ".mlab/config.json", {**config, "fixture_failure": "backend_error"})
    assert classroom_loop(workspace_path, FixtureTeacher(workspace_path), ScriptedStudent(1)) is None
    assert "The model API is unavailable" in capsys.readouterr().out
    atomic(workspace_path / ".mlab/config.json", config)
    report = classroom_loop(workspace_path, FixtureTeacher(workspace_path), ScriptedStudent(1))
    assert report["conclusion"] == "mock_only"
    lab = Lab(workspace_path)
    try:
        assert len([r for r in lab.store.all("run") if r["failure_class"] is None]) == 6
    finally:
        lab.close()
