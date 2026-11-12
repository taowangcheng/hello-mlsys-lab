"""Page forms write the student's words verbatim, keep the agent's sections, and pass the same gates as the CLI."""
import json
import pytest
from mlab.core.io import LabError, atomic, load
from mlab.core.workspace import Lab, initialize
from mlab.core import student
from test_workflow import HYPOTHESIS, decided, ready
from test_flywheels import sweep


def logged(path):
    return [json.loads(line) for line in (path / ".mlab/student.jsonl").read_text(encoding="utf-8").splitlines()]


def test_hypothesis_and_review_forms_then_approval(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    lab = Lab(workspace_path)
    try:
        result = student.write_hypothesis(lab, "Long prefills block decode", "overall overload", "If only lower concurrency helps, it is overload")
        assert result["created_round"] and lab.workflow()["status"] == "draft"
        text = (lab.folder() / "hypothesis.md").read_text(encoding="utf-8")
        assert "## Hypothesis\n\nLong prefills block decode" in text and "## What result would prove me wrong\n\nIf only lower concurrency helps, it is overload" in text
        with pytest.raises(LabError) as error:
            student.write_hypothesis(lab, "x", "", " ")
        assert error.value.code == "E_SCHEMA"
        plan = load(lab.folder() / "plan.yaml")
        plan.update(question="Which change meets the SLA?", derived_requirements=[{"name": "ttft", "value": "< 2000 ms", "derivation": "SLA"}])
        atomic(lab.folder() / "plan.yaml", plan)
        checked = lab.check()
        atomic(lab.folder() / "review.md", "## Comments on the plan\n\n\n## Changes and responses\n\nagent: revised as commented.\n", text=True)
        with pytest.raises(LabError):
            student.write_review(lab, [{"candidate": "nope", "verdict": "keep"}])
        done = student.write_review(lab, [{"candidate": c, "verdict": "keep", "reason": "the variables are properly controlled"} for c in ("baseline", "chunked_256", "low_concurrency")])
        assert done["keep_all"]
        review = (lab.folder() / "review.md").read_text(encoding="utf-8")
        assert "- chunked_256: keep. the variables are properly controlled" in review and "agent: revised as commented." in review
        lab.approve(checked["plan_hash"])
        with pytest.raises(LabError) as error:
            student.write_review(lab, [], "one more change")
        assert error.value.code == "E_NEEDS_APPROVAL"
        entries = logged(workspace_path)
        assert [e["kind"] for e in entries] == ["hypothesis", "review"] and {e["source"] for e in entries} == {"web-form"}
        assert all(e["workflow"] == "r001" and "experiment" not in e for e in entries)
    finally:
        lab.close()


def test_decision_form_needs_evidence_and_feeds_decide(workspace_path):
    lab = ready(workspace_path)
    try:
        checked = lab.check()
        atomic(lab.folder() / "review.md", "## Comments on the plan\n\nAgree.\n", text=True)
        lab.approve(checked["plan_hash"])
        with pytest.raises(LabError):
            student.write_decision(lab, "Keep low_concurrency", "low_concurrency")
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        with pytest.raises(LabError) as error:
            student.write_decision(lab, "Keep low_concurrency", "low_concurrency")
        assert "sim_0001" in str(error.value)
        with pytest.raises(LabError):
            student.write_decision(lab, "Based on sim_0003", "c99")
        student.write_decision(lab, "Based on sim_0003, keep low_concurrency", "low_concurrency")
        assert lab.decide("low_concurrency")["selected_candidate"] == "low_concurrency"
    finally:
        lab.close()


def test_points_form_feeds_the_slow_flywheel(workspace_path):
    lab = ready(workspace_path)
    try:
        decided(lab)
        sweep(lab)
        decided(lab, "c20")
        with pytest.raises(LabError) as error:
            student.write_points(lab, [{"candidate": "c20", "rationale": ""}])
        assert "rationale" in str(error.value)
        assert "points: []" in (lab.mfolder() / "points.yaml").read_text(encoding="utf-8")  # a bad submission restores the file
        result = student.write_points(lab, [{"candidate": "c20", "runs": 1, "rationale": "my pick, but the margin is only 3%"},
                                            {"candidate": "low_concurrency", "rationale": "a low-concurrency control"}])
        assert result["hardware_runs"] == 2 and result["hardware_budget"]["remaining"] == 8
        checked = lab.measure_check()
        lab.measure_approve(checked["plan_hash"])
        lab.measure_submit()
        assert lab.measure_wait(20)["status"] == "completed"
        # With the hardware results in, the next points form opens a new measurement; nothing has to be handled first.
        assert student.write_points(lab, [{"candidate": "c16", "rationale": "a larger margin"}])["measurement"] == "m002"
        points = [e for e in logged(workspace_path) if e["kind"] == "points"]
        assert len(points) == 2 and "my pick, but the margin is only 3%" in points[0]["text"] and points[1]["measurement"] == "m002"
    finally:
        lab.close()


def test_review_form_keeps_earlier_comments(workspace_path):
    lab = ready(workspace_path)
    try:
        lab.check()
        student.write_review(lab, [{"candidate": "baseline", "verdict": "change", "reason": "set concurrency to 24"}])
        student.write_review(lab, [], "changed now, agree")
        review = (lab.folder() / "review.md").read_text(encoding="utf-8")
        assert review.index("set concurrency to 24") < review.index("changed now, agree") and "## Changes and responses" in review
    finally:
        lab.close()


def test_choice_is_logged(workspace_path):
    lab = ready(workspace_path)
    try:
        atomic(lab.folder() / "hypothesis.md", HYPOTHESIS, text=True)
        student.record_choice(lab, "hardware points", "the simulated margin is too small")
        assert logged(workspace_path)[-1]["text"] == "Next step: hardware points. the simulated margin is too small"
    finally:
        lab.close()
