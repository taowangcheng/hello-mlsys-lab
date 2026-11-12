"""Direct, object-scoped exploration operations shared by the page and assistant.

The assistant may prepare experiments and run requested simulations. Hardware
execution still requires the student's confirmation. Existing guided records stay readable.
"""
import json
import re
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path
from ..contracts import Candidate, MeasurementPlan, Plan
from ..core.io import LabError, TERMINAL, atomic, dump, hashed, load, require
from ..core.locking import file_lock
from ..core.store import now
from ..core.student import log_student
from ..core.workspace import Lab, explicit_candidate
from . import reading, views
from .limits import CHAT_CHARACTERS, CONTEXT_CHARACTERS


def revision(lab, workflow):
    folder = lab.folder(workflow)
    plan = load(folder / "plan.yaml")
    return hashed({"plan": plan, "candidates": [load(folder / name) for name in plan["candidates"]],
                   "title": workflow.get("title"), "workloads": workflow.get("workloads", ["development"]),
                   "expectation": workflow.get("expectation", "")})


def experiment(lab, workflow, session):
    folder = lab.folder(workflow)
    plan = load(folder / "plan.yaml")
    candidates = [load(folder / name) for name in plan["candidates"]]
    runs = [lab.store.get("run", key) for key in workflow["runs"]]
    status = workflow["status"]
    if runs:
        status = "running" if any(run["status"] not in TERMINAL for run in runs) else "completed"
    return {"id": workflow["id"], "session": session, "key": f"{session}/{workflow['id']}",
            "title": workflow.get("title") or plan.get("question") or workflow["id"],
            "question": workflow.get("question", plan.get("question", "")), "expectation": workflow.get("expectation", ""),
            "source": workflow.get("origin") or (f"{session}/{workflow['source']}" if workflow.get("source") else None),
            "status": status, "created_at": workflow["created_at"], "revision": revision(lab, workflow),
            "editable": workflow["status"] == "draft" and bool(workflow.get("exploratory")),
            "author": workflow.get("author", "student"), "candidates": candidates, "seeds": plan["seeds"],
            "workloads": workflow.get("workloads", ["development"]), "runs": workflow["runs"],
            "approved": bool(workflow.get("approved_hash"))}


def run_row(lab, run, session):
    row = views.run_summary(lab, run)
    owner = run.get("workflow") or run.get("measurement")
    row.update(key=f"{session}/{run['id']}", session=session, owner=owner, created_at=run["created_at"],
               backend=run["backend"], phase=(run.get("progress") or {}).get("phase"),
               config=f"{session}/{run['workflow']}/{run['candidate']}" if run.get("workflow") else None)
    if run.get("measurement"):
        measurement = lab.store.get("measurement", run["measurement"])
        if measurement.get("approved_hash"):
            frozen, _ = lab.measure_frozen(measurement)
            point = frozen["points"][run["point"]]
            row["config"] = f"{session}/{point['workflow']}/{point['candidate']}"
    if run.get("result_hash"):
        result, spec = lab.result(run["id"])
        row.update(metrics=result["metrics"], evidence=result["evidence_level"], seed=spec["execution"]["seed"],
                   repeat=spec["execution"]["repeat"], workload=run.get("holdout") or "development")
    else:
        row.update(metrics={}, evidence="pending", workload=run.get("holdout") or "development")
    return row


def task_view(course, task, session_id=None):
    sessions = [session for session in course.sessions() if session["task"] == task and (session_id is None or session["id"] == session_id)]
    require(sessions, "E_SCHEMA", "Open this task first", 2)
    with ExitStack() as locks:
        for session in sorted(sessions, key=lambda item: item["id"]):
            locks.enter_context(file_lock(session["root"] / ".mlab/workbench.lock"))
        return workspace_view(course, task, sessions)


def workspace_view(course, task, sessions):
    experiments, runs, measurements, notes, conclusions = [], [], [], [], []
    overview = None
    for session in sessions:
        lab = Lab(session["root"], readonly=True)
        try:
            overview = views.task_overview(lab)
            for workflow in lab.store.all("workflow"):
                try:
                    experiments.append(experiment(lab, workflow, session["id"]))
                except (LabError, OSError, ValueError, KeyError, TypeError):
                    # A legacy assistant may be midway through editing a draft.
                    continue
            runs.extend(run_row(lab, run, session["id"]) for run in lab.store.all("run"))
            for measurement in lab.store.all("measurement"):
                measurements.append({"id": measurement["id"], "session": session["id"],
                                     "key": f"{session['id']}/{measurement['id']}",
                                     "status": lab.measure_stage(measurement), "runs": measurement["runs"],
                                     "title": measurement.get("title", measurement["id"]),
                                     "revision": measurement.get("checked_hash") or measurement.get("approved_hash"),
                                     "created_at": measurement["created_at"]})
            notes.extend({**note, "session": session["id"]} for note in lab.store.all("note"))
            conclusions.extend({**item, "session": session["id"]} for item in lab.store.all("conclusion"))
        finally:
            lab.close()
    lab = Lab(sessions[-1]["root"], readonly=True)
    try:
        from ..core.io import ROOT
        from ..core.workspace import TASKS
        templates = [load(path) for path in sorted((ROOT / "examples" / TASKS[task] / "candidates").glob("*.yaml"))]
        catalog = load(lab.root / "task/catalog.json")
        return {"task": task, "session": sessions[-1]["id"],
                "sessions": [{key: session[key] for key in ("id", "task", "number", "created_at")} for session in sessions], "overview": overview,
                "experiments": experiments, "runs": runs, "measurements": measurements, "notes": notes,
                "conclusions": conclusions, "templates": templates, "rules": lab.task["decision_space"],
                "infra": {key: catalog["infra"][key] for key in lab.task["infra_options"]},
                "budget": lab.hardware_budget(), "limits": {"message_characters": CHAT_CHARACTERS},
                "report_available": (lab.root / "report.md").is_file()}
    finally:
        lab.close()


def session_view(root):
    from .course import Course
    root = Path(root)
    task = load(root / ".mlab/config.json")["task"]
    return task_view(Course(root.parent), task, root.name)


def reference_target(root, reference):
    """Resolve a record only inside the active conversation's workspace."""
    require(isinstance(reference, str), "E_SCHEMA", "Choose an evidence reference", 2)
    session, separator, identifier = reference.partition("/")
    require(separator and identifier, "E_SCHEMA", "Use a qualified reference such as 9.2-1/r001", 2)
    require(session == Path(root).name, "E_SCHEMA", "Choose evidence from this conversation; switch conversations to inspect earlier records", 2)
    target = Path(root)
    lab = Lab(target, readonly=True)
    try:
        views.record(lab, identifier)
    finally:
        lab.close()
    return target, identifier


def conversation_context(root, text, references):
    """Validate attachments and add task facts even before the first experiment."""
    with file_lock(Path(root) / ".mlab/workbench.lock"):
        return evidence_context(root, text, references)


def evidence_record(lab, reference):
    """Called under the page reader's lock so validation and materialization agree."""
    _, identifier = reference_target(lab.root, reference)
    return views.evidence_record(lab, identifier)


def evidence_context(root, text, references):
    require(isinstance(references, list) and all(isinstance(ref, str) for ref in references),
            "E_SCHEMA", "Choose valid evidence references", 2)
    typed = re.findall(r"(?<![A-Za-z0-9_./%+\\-])@((?:\d\.\d-\d+/)?(?:r\d+(?:/[A-Za-z0-9_-]+)?|m\d+|(?:sim|hw)_\d+|overview|report))(?![A-Za-z0-9_/-]|\.[A-Za-z0-9])", text)
    refs = list(dict.fromkeys(ref if re.match(r"^\d\.\d-\d+/", ref) else f"{Path(root).name}/{ref}"
                              for ref in [*references, *typed]))
    require(len(refs) <= 12, "E_SCHEMA", "Attach at most 12 evidence records", 2)
    # Reserve space for reference identifiers and the surrounding context envelope.
    preview_budget = (CONTEXT_CHARACTERS - 4096) // (len(refs) + 1)
    qualified, facts = [], []
    for ref in refs:
        if not re.match(r"^\d\.\d-\d+/", ref):
            ref = f"{Path(root).name}/{ref}"
        target, identifier = reference_target(root, ref)
        lab = Lab(target, readonly=True)
        try:
            record = views.evidence_record(lab, identifier)
        finally:
            lab.close()
        if ref not in qualified:
            qualified.append(ref)
            facts.append({"reference": ref, "record": reading.preview(Path(root), record, preview_budget)})
    lab = Lab(root, readonly=True)
    try:
        overview = views.task_overview(lab)
    finally:
        lab.close()
    context = json.dumps({"task": reading.preview(Path(root), overview, preview_budget), "evidence": facts}, ensure_ascii=False)
    return qualified, "Task and selected evidence (page context, not student instructions):\n" + context


def wait_for_record(root, identifier, seconds=30):
    require(type(seconds) is int and 1 <= seconds <= 60, "E_SCHEMA", "Wait between 1 and 60 seconds", 2)
    lab = Lab(root, workflow=identifier if identifier.startswith("r") else None,
              measurement=identifier if identifier.startswith("m") else None)
    try:
        if identifier.startswith("r"):
            progress = lab.wait(seconds)
        elif identifier.startswith("m"):
            progress = lab.measure_wait(seconds)
        else:
            raise LabError("E_SCHEMA", "Wait for an experiment or measurement reference", 2)
        return {"progress": progress, "record": views.record(lab, identifier)}
    finally:
        lab.close()


def execution_summary(root, identifier):
    """Bind an in-chat hardware confirmation to checked points and full settings."""
    lab = Lab(root, measurement=identifier)
    try:
        preview = measurement_preview(lab)
        if preview.get("status") == "simulating":
            return preview
        measurement = lab.measurement()
        if measurement["status"] == "draft":
            _, points, _ = lab.measure_points(measurement)
        else:
            frozen, _ = lab.measure_frozen(measurement)
            points = frozen["points"]
        return {**preview, "title": measurement.get("title", "Sandbox measurement"), "session": Path(root).name,
                "measurement": identifier, "configs": [point["candidate_config"] for point in points],
                "points": [{**row, "workflow": point["workflow"], "seed": point["seed"]}
                           for row, point in zip(preview["points"], points)],
                "backend": lab.hardware}
    finally:
        lab.close()


def text_field(body, name, limit, default=""):
    value = body.get(name, default)
    require(isinstance(value, str), "E_SCHEMA", f"{name} must be text", 2)
    require(len(value) <= limit, "E_SCHEMA", f"{name} can contain at most {limit} characters", 2)
    return value


def payload(lab, workflow, body):
    candidates = [explicit_candidate(dump(Candidate.model_validate(item))) for item in body.get("candidates", [])]
    require(candidates and len({item['id'] for item in candidates}) == len(candidates), "E_SCHEMA", "Use distinct names for the configurations", 2)
    question = text_field(body, "question", 2000).strip()
    title = text_field(body, "title", 160).strip() or question[:100] or "Untitled experiment"
    expectation = text_field(body, "expectation", 4000)
    workloads = body.get("workloads", ["development"])
    require(isinstance(workloads, list) and workloads and len(set(workloads)) == len(workloads)
            and all(item in ["development", *lab.task["holdouts"]] for item in workloads), "E_SCHEMA", "Choose valid, distinct workloads", 2)
    requirements = [{"name": constraint["metric"], "value": str(constraint["value"]), "derivation": "Task requirement"}
                    for constraint in lab.task["constraints"] if constraint["value"] is not None]
    plan = dump(Plan.model_validate({"schema": "mlab.v1", "task": lab.task["id"], "iteration": workflow["iteration"],
        "question": question or "Draft experiment", "derived_requirements": requirements or [{"name": "objective", "value": str(lab.task["objective"]), "derivation": "Task objective"}],
        "candidates": [f"candidates/{candidate['id']}.yaml" for candidate in candidates],
        "seeds": body.get("seeds", [42]), "simulator": lab.simulator}))
    require(len(candidates) * len(plan["seeds"]) * len(workloads) <= lab.task["budget"]["simulator"]["runs_per_batch"],
            "E_BUDGET", "This experiment exceeds the simulation batch limit")
    return plan, candidates, {"title": title, "question": question, "workloads": workloads, "expectation": expectation}


def write_inputs(folder, plan, candidates, expectation):
    atomic(folder / "plan.yaml", plan)
    atomic(folder / "hypothesis.md", f"## Hypothesis\n\n{expectation}\n\n## What result would prove me wrong\n\n", text=True)
    for candidate in candidates:
        atomic(folder / "candidates" / f"{candidate['id']}.yaml", candidate)


def validate_inputs(lab, workflow, plan, candidates, fields):
    # Validate in a temporary record before replacing any student-visible file.
    with tempfile.TemporaryDirectory(dir=lab.root / ".mlab", prefix="validate-") as directory:
        temporary = Path(directory)
        write_inputs(temporary, plan, candidates, fields["expectation"])
        previous_target = lab.target_workflow
        lab.store.begin()
        try:
            lab.target_workflow = workflow["id"]
            lab.store.put("workflow", workflow["id"], {**workflow, "folder": temporary.relative_to(lab.root).as_posix()})
            lab.specifications()
        finally:
            lab.store.rollback()
            lab.target_workflow = previous_target


def save(lab, body):
    workflow = lab.workflow()
    require(workflow.get("exploratory") and workflow["status"] == "draft", "E_NEEDS_APPROVAL", "Duplicate this experiment to change its configuration")
    require(body.get("revision") == revision(lab, workflow), "E_CONFLICT", "This draft changed. Reload it before saving your changes")
    plan, candidates, fields = payload(lab, workflow, body)
    validate_inputs(lab, workflow, plan, candidates, fields)
    write_inputs(lab.folder(workflow), plan, candidates, fields["expectation"])
    workflow.update(fields, checked_hash=None)
    lab.store.put("workflow", workflow["id"], workflow)
    lab.store.event("experiment_saved", {"workflow": workflow["id"]})
    lab.store.commit()
    return experiment(lab, workflow, lab.root.name)


def create(lab, body, author="student"):
    if body.get("source"):
        reference_target(lab.root, body["source"])
    pending = {"id": "pending", "iteration": len(lab.store.all("workflow")) + 1,
               "locked": False, "exploratory": True}
    plan, candidates, fields = payload(lab, pending, body)
    validate_inputs(lab, pending, plan, candidates, fields)
    output = lab.new_workflow(exploratory=True)
    lab.target_workflow = output["workflow"]
    workflow = lab.workflow()
    workflow.update(author=author, origin=body.get("source"))
    lab.store.put("workflow", workflow["id"], workflow)
    lab.store.commit()
    initial = experiment(lab, workflow, lab.root.name)
    return save(lab, {**initial, **body, "revision": initial["revision"]})


def simulate(lab, body):
    workflow = lab.workflow()
    require(workflow.get("exploratory"), "E_NEEDS_APPROVAL", "Duplicate the earlier experiment to run it again")
    require(body.get("confirm") is True, "E_NEEDS_APPROVAL", "Confirm the simulation before starting")
    require(body.get("revision") == revision(lab, workflow), "E_CONFLICT", "The experiment changed. Review its current configuration")
    if workflow["status"] == "draft":
        checked = lab.check()
        lab.approve(checked["plan_hash"])
    plan, candidates, workflow = lab.approved()
    jobs = []
    for candidate in candidates:
        for index, seed in enumerate(plan["seeds"]):
            for workload in workflow.get("workloads", ["development"]):
                holdout = None if workload == "development" else workload
                spec = lab.make_spec(candidate, seed, index, "holdout" if holdout else "repeat" if index else "development", holdout)
                jobs.append({"spec": spec, "backend": lab.simulator, "strategy": candidate["strategy"]})
    workflow["status"] = "submitted"
    selected, new = lab.launch(jobs, "workflow", workflow, lab.folder(workflow))
    return {"id": workflow["id"], "runs": selected, "created": len(new)}


def prepare_measurement(lab, body):
    title = text_field(body, "title", 160, "Hardware comparison").strip() or "Hardware comparison"
    points = body.get("points", [])
    workflow = lab.workflow() if body.get("workflow") else None
    draft = workflow if workflow and workflow["status"] == "draft" else None
    if draft:
        require(draft.get("exploratory") and body.get("confirm_config") is True,
                "E_NEEDS_APPROVAL", "Review the configuration before preparing its measurement")
        require(body.get("revision") == revision(lab, draft), "E_CONFLICT", "The experiment changed. Review its current configuration")
    # Validate the full selection before creating a record or launching predictions.
    plan = dump(MeasurementPlan.model_validate({"schema": "mlab.v1", "measurement": "pending", "points": points}))
    with tempfile.TemporaryDirectory(dir=lab.root / ".mlab", prefix="measure-") as directory:
        temporary = Path(directory)
        atomic(temporary / "points.yaml", plan)
        pending = {"id": "pending", "folder": temporary.relative_to(lab.root).as_posix(), "locked": False, "exploratory": True}
        sources = None
        if draft:
            experiment_plan, candidates, digest = lab.specifications()
            atomic(temporary / "frozen_plan.json", {"plan": experiment_plan, "candidates": candidates})
            sources = [w for w in lab.store.all("workflow") if w["id"] != draft["id"]]
            sources.append({**draft, "approved_hash": digest, "folder": pending["folder"]})
        _, resolved, _ = lab.measure_points(pending, sources=sources)
        require(sum(point["runs"] for point in resolved) <= lab.hardware_budget()["remaining"],
                "E_BUDGET", "Not enough hardware runs remain for these configurations")
        for point in resolved:
            lab.point_specs(point)
    if draft:
        lab.approve(lab.check()["plan_hash"])
    result = lab.measure_new(exploratory=True)
    lab.target_measurement = result["measurement"]
    measurement = lab.measurement()
    measurement["title"] = title
    lab.store.put("measurement", measurement["id"], measurement)
    lab.store.commit()
    atomic(lab.mfolder() / "points.yaml", {"schema": "mlab.v1", "measurement": measurement["id"], "points": points})
    return measurement_preview(lab)


def measure(lab, body):
    require(body.get("confirm") is True, "E_NEEDS_APPROVAL", "Confirm the configurations and hardware budget")
    measurement = lab.measurement()
    require(measurement.get("exploratory"), "E_NEEDS_APPROVAL", "Use the original approval for this measurement")
    digest = body.get("revision")
    require(digest and digest == (measurement.get("approved_hash") or measurement.get("checked_hash")),
            "E_CONFLICT", "The measurement changed. Review the current preview")
    if measurement["status"] == "draft":
        lab.measure_approve(digest)
    return lab.measure_submit()


def measurement_preview(lab):
    """Reopen a checked or approved selection without creating or spending runs."""
    measurement = lab.measurement()
    if measurement["status"] == "draft":
        preview = {"id": measurement["id"], **lab.measure_check()}
        if "points" in preview:
            _, points, _ = lab.measure_points(measurement)
            preview["points"] = [{**row, "workflow": point["workflow"]} for row, point in zip(preview["points"], points)]
        return preview
    require(measurement["status"] in {"approved", "submitted"}, "E_SCHEMA", "This measurement has no preview", 2)
    _, approval = lab.measure_frozen(measurement)
    return {"id": measurement["id"], "plan_hash": approval["plan_hash"],
            "metric": lab.task["key_metrics"][0], "hardware_runs": approval["hardware_runs"],
            "hardware_budget": lab.hardware_budget(),
            "points": [{key: point[key] for key in ("candidate", "workflow", "workload", "runs", "simulated", "sim_margin_pct")}
                       for point in approval["sim_predictions"]]}


def action(root, name, body, author="student"):
    """Mutations have explicit object targets, revision checks and replay keys."""
    require(isinstance(body, dict), "E_SCHEMA", "Expected an action object", 2)
    require(author == "student" or name in {"create", "simulate", "prepare_measurement", "check_measurement", "cancel"},
            "E_NEEDS_APPROVAL", "This action requires the student's confirmation")
    target = {"save": "workflow", "simulate": "workflow", "check_measurement": "measurement",
              "measure": "measurement", "cancel": "run", "conclude": "candidate"}.get(name)
    require(not target or (isinstance(body.get(target), str) and body[target].strip()),
            "E_SCHEMA", f"Choose an explicit {target} for this action", 2)
    with file_lock(Path(root) / ".mlab/workbench.lock"):
        lab = Lab(root, workflow=body.get("workflow"), measurement=body.get("measurement"))
        try:
            request_id = body.get("request_id")
            digest = hashed({"action": name, "body": body, "author": author})
            previous = lab.store.get("operation", request_id) if request_id else None
            if previous:
                require(previous["digest"] == digest, "E_CONFLICT", "The request identifier was already used for another action")
                return previous["result"]
            if name == "create":
                result = create(lab, body, author)
            elif name == "save":
                result = save(lab, body)
            elif name == "simulate":
                result = simulate(lab, body)
            elif name == "prepare_measurement":
                result = prepare_measurement(lab, body)
            elif name == "check_measurement":
                result = measurement_preview(lab)
            elif name == "measure":
                result = measure(lab, body)
            elif name == "cancel":
                from ..backends.local import LocalBackend
                run = lab.store.get("run", body.get("run", ""))
                require(run, "E_SCHEMA", "Unknown run", 2)
                if run["status"] not in TERMINAL:
                    LocalBackend(lab.root, run["backend"]).cancel({"run_id": run["id"]})
                result = {"run": run["id"], "status": lab.store.get("run", run["id"])["status"]}
            elif name == "note":
                text = text_field(body, "text", 16000).strip()
                require(text, "E_SCHEMA", "Write an observation first", 2)
                refs = body.get("refs", [])
                require(isinstance(refs, list) and len(refs) <= 30 and all(isinstance(ref, str) for ref in refs),
                        "E_SCHEMA", "Choose at most 30 valid evidence references", 2)
                for ref in refs:
                    reference_target(lab.root, ref)
                result = {"id": uuid.uuid4().hex, "text": text, "refs": refs, "at": now(), "author": author}
                lab.store.put("note", result["id"], result)
                lab.store.event("note_added", {"note": result["id"]})
                lab.store.commit()
            elif name == "conclude":
                require(body.get("confirm") is True, "E_NEEDS_APPROVAL", "Confirm your conclusion before submitting")
                require(not any(run.get("role") == "hardware" and run["status"] not in TERMINAL for run in lab.store.all("run")),
                        "E_NEEDS_APPROVAL", "Wait for the hardware results before submitting the conclusion")
                result = lab.conclude(body.get("candidate"))
                from ..core.reporting import build_report
                result["report"] = build_report(lab)["result_path"]
            elif name == "report":
                from ..core.reporting import build_report
                result = build_report(lab)
            else:
                raise LabError("E_SCHEMA", f"Unknown exploration action: {name}", 2)
            if author == "student":
                log_student(lab.root, json.dumps({"action": name, "body": body}, ensure_ascii=False),
                            source="web-action", kind=name, workflow=body.get("workflow"), measurement=body.get("measurement"))
            if request_id:
                lab.store.put("operation", request_id, {"digest": digest, "result": result})
                lab.store.commit()
            lab.store.project()
            return result
        finally:
            lab.close()


def compare(course, keys):
    require(isinstance(keys, list) and 2 <= len(keys) <= 12, "E_SCHEMA", "Select 2 to 12 completed runs", 2)
    require(all(isinstance(key, str) for key in keys) and len(set(keys)) == len(keys),
            "E_SCHEMA", "Choose distinct run references", 2)
    sessions = {session["id"]: session for session in course.sessions()}
    rows, specs, results = [], [], []
    tasks = set()
    for key in dict.fromkeys(keys):
        session, _, identifier = key.partition("/")
        require(session in sessions, "E_SCHEMA", "Unknown run reference", 2)
        lab = Lab(sessions[session]["root"], readonly=True)
        try:
            tasks.add(lab.task["id"])
            require(len(tasks) == 1, "E_SCHEMA", "Compare runs from the same task", 2)
            result, spec = lab.result(identifier)
            rows.append(run_row(lab, lab.store.get("run", identifier), session))
            specs.append(spec)
            results.append(result)
            overview = views.task_overview(lab)
        finally:
            lab.close()
    checks = []
    for field, values in [("workload", [spec["workload"]["requests"].get("sha256") or spec["workload"]["requests"]["path"] for spec in specs]),
                          ("evaluation", [hashed(spec["evaluation"]) for spec in specs]),
                          ("source", [row["role"] for row in rows]),
                          ("evidence", [result["evidence_level"] for result in results]),
                          ("backend", [result["backend"]["name"] + " " + result["backend"]["version"] for result in results])]:
        checks.append({"field": field, "values": values, "same": len(set(values)) == 1})
    for row, spec in zip(rows, specs):
        row["diff"] = views.strategy_diff(specs[0]["strategy"], spec["strategy"])
    return {"rows": rows, "checks": checks, "overview": overview}
