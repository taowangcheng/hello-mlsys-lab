"""Read-only data for the page. Every function takes a read-only Lab and writes nothing."""
import re
from ..backends.local import load_requests
from ..core.configs import catalog, evidence, pairs, validation
from ..core.evidence import check_claims
from ..core.history import resolve, timeline
from ..core.io import NOTICE, TERMINAL, LabError, hashed, load
from ..core.metrics import get_metric
from ..core.reporting import render_report, report_data
from ..core.workspace import comments, hypothesis_parts, margin, read_rows, rounded, sections
from . import i18n, labels

EVENT_LABELS = {"workflow_new": "Round created", "workflow_approved": "Plan approved", "workflow_decided": "Decision confirmed",
                "measurement_new": "Measurement created", "measurement_approved": "Measurement approved", "concluded": "Conclusion submitted"}
REFERENCE = re.compile(r"@(r\d{3}/[A-Za-z0-9][A-Za-z0-9_-]{0,47}|r\d{3}|m\d{3}|(?:sim|hw|mock)_\d{4})(?![\w/-])")


def text_of(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def readable(path, default=None):
    """A file the agent may be editing right now: a parse error is shown, not raised."""
    try:
        return load(path), None
    except FileNotFoundError:
        return default, None
    except (OSError, ValueError, LabError) as error:  # yaml errors are ValueErrors
        return default, f"{path.name} cannot be parsed right now: {str(error).splitlines()[0][:160]}"


def leaves(value, prefix=""):
    if isinstance(value, dict):
        return {k: v for key, item in value.items() for k, v in leaves(item, f"{prefix}.{key}" if prefix else key).items()}
    if isinstance(value, list):
        return {k: v for i, item in enumerate(value) for k, v in leaves(item, f"{prefix}[{i}]").items()}
    return {prefix: value}


def strategy_diff(base, other):
    """Fields of a strategy that differ from the base candidate's."""
    left, right = leaves(base), leaves(other)
    return [{"field": key, "base": left.get(key), "value": right.get(key)} for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)]


def key_constraint(lab):
    metric = lab.task["key_metrics"][0]
    return next(({"metric": c["metric"], "op": c["op"], "value": c["value"]} for c in lab.task["constraints"]
                 if c["metric"] == metric and c["value"] is not None and c["status"] != "tbd"), None)


def task_state(lab, agent_status="idle"):
    """What the page needs to choose a form, quick replies and the tab badge."""
    state = lab.state()
    action = state["next_actions"][0]
    step = action.get("step")
    runs = []
    for kind in ("workflow", "measurement"):
        owner = lab.store.get(kind, state[kind]) if state.get(kind) else None
        for run in (lab.store.get("run", i) for i in (owner or {}).get("runs", [])):
            if run and run["status"] not in TERMINAL:
                runs.append({"id": run["id"], "role": run.get("role", "simulator"), "candidate": run["candidate"], "status": run["status"],
                             "phase": (run.get("progress") or {}).get("phase")})
    return {**state, "task": lab.task["id"], "title": lab.task["title"], "step": step, "actor": action["actor"],
            "step_label": labels.STEPS.get(step, step), "form": labels.FORMS.get(step), "hint": labels.HINTS.get(step),
            "quick_replies": labels.QUICK_REPLIES.get(step, []), "badge": labels.badge(state, agent_status),
            "runs_in_flight": runs, "ledger_seq": lab.store.last_seq()}


MEASURE_STEPS = {"points", "points_predicting", "points_approve", "measure_submit", "measure_retry", "measure_locked"}


def default_record(state):
    """The record a task opens on: where the student's next step is."""
    if state.get("measurement") and state.get("step") in MEASURE_STEPS:
        return state["measurement"]
    return state.get("workflow") or "overview"


def task_overview(lab):
    task = lab.task
    space = {path: (rule["values"] if rule["type"] == "enum" else [rule["min"], rule["max"]]) for path, rule in task["decision_space"].items()}
    return {"task": task["id"], "title": task["title"], "objective": task["objective"], "key_metrics": task["key_metrics"],
            "constraints": [{k: c.get(k) for k in ("metric", "op", "value", "status", "note")} for c in task["constraints"]],
            "holdouts": task["holdouts"], "decision_space": space,
            "budget": {"simulator_per_batch": task["budget"]["simulator"]["runs_per_batch"], "hardware": lab.hardware_budget(),
                       "hardware_per_batch": task["budget"]["hardware"]["runs_per_batch"]},
            "backends": {"simulator": lab.simulator, "hardware": lab.hardware}, "guide": text_of(lab.root / "task/GUIDE.md"),
            "mode": lab.mode, "notice": NOTICE}


def run_summary(lab, run):
    line = {"id": run["id"], "candidate": run["candidate"], "split": run["split"], "holdout": run.get("holdout"), "role": run.get("role", "simulator"),
            "status": run["status"]}
    if run["status"] in TERMINAL and run.get("result_hash"):
        result, _ = lab.result(run["id"])
        metric = lab.task["key_metrics"][0]
        objective = lab.task["objective"].get("metric")
        line.update(verdict=result["verdict"], failure=result["failure_class"], key_value=rounded(get_metric(result["metrics"], metric)),
                    objective_value=rounded(get_metric(result["metrics"], objective)) if objective else None, margin_pct=margin(result, metric),
                    constraints=[{k: c.get(k) for k in ("metric", "op", "value", "observed", "met")} for c in result["constraints"] if c["value"] is not None])
    return line


def validation_matrix(lab):
    """Which splits the selected configuration passed, per role, what it still lacks, and the conclusion it would get now."""
    selection = lab.current()["selection"]
    if not selection:
        return None
    everything = lab.store.all("run")
    matrix, missing = validation(lab, [r for r in everything if r.get("config_hash") == selection["config_hash"]])
    conclusion, reason = lab.conclusion_for(selection, everything)
    return {"candidate": selection["candidate"], "workflow": selection["workflow"], "config": f"{selection['workflow']}/{selection['candidate']}",
            "matrix": matrix, "missing": missing, "conclusion": conclusion, "reason": reason}


def task_notebook(lab):
    """The left column and the overview page: the task's timeline, its configurations, the selection and conclusions."""
    items, question = [], ""
    apart = {m["id"]: [r["run_id"] for r in pairs(lab, m) if r["beyond_noise"] or r["verdict_flip"] or r["config_diff"]]
             for m in lab.store.all("measurement")}
    for item in timeline(lab):
        if item["kind"] == "round":
            w = lab.store.get("workflow", item["id"])
            plan, _ = readable(lab.root / w["folder"] / "plan.yaml", {})
            question = (plan if isinstance(plan, dict) else {}).get("question") or question
            item.update(label=f"Simulation round {w['iteration']}", runs=[run_summary(lab, lab.store.get("run", r)) for r in w["runs"]])
        elif item["kind"] == "measurement":
            m = lab.store.get("measurement", item["id"])
            item.update(label=f"Measurement {m['id']}", flagged=apart[m["id"]],
                        runs=[run_summary(lab, lab.store.get("run", r)) for r in m["runs"] if r.startswith("hw_")])
        else:
            item.update(label="Conclusion submitted", status=item["conclusion"])
        items.append(item)
    events = []
    for event in lab.store.events():
        data = event["data"]
        if event["type"] in EVENT_LABELS:
            events.append({"at": event["at"], "type": event["type"], "label": EVENT_LABELS[event["type"]],
                           "id": data.get("workflow") or data.get("measurement") or data.get("conclusion")})
    current = lab.current()
    latest = lab.latest_conclusion()
    selection = current["selection"]
    return {"id": "overview", "kind": "overview", "question": question, "items": items, "configs": catalog(lab), "timeline": events,
            "selection": selection and f"{selection['workflow']}/{selection['candidate']}",
            "conclusion": latest and {k: latest[k] for k in ("id", "at", "conclusion", "reason")} | {
                "config": f"{latest['selection']['workflow']}/{latest['selection']['candidate']}"},
            "validation": validation_matrix(lab), "budget": lab.hardware_budget(),
            "current": {"workflow": current["workflow"], "measurement": current["measurement"]},
            "report": (lab.root / "report.md").exists()}


def candidates_of(lab, w, plan):
    folder = lab.root / w["folder"]
    if (folder / "frozen_plan.json").exists():
        return load(folder / "frozen_plan.json")["candidates"], True
    found = []
    for relative in plan.get("candidates", []):
        candidate, error = readable(folder / relative, None)
        found.append(candidate if isinstance(candidate, dict) else {"id": relative, "rationale": error or "(file not found)", "strategy": {}})
    return found, False


def round_view(lab, wid):
    w = lab.store.get("workflow", wid)
    if not w:
        raise LabError("E_SCHEMA", f"unknown round {wid}", 2)
    folder = lab.root / w["folder"]
    text = text_of(folder / "hypothesis.md")
    claim, falsifier = hypothesis_parts(text)
    alternative = next((v for k, v in sections(text).items() if "alternative" in k.lower()), "")
    plan, plan_error = readable(folder / "plan.yaml", {})
    plan = plan if isinstance(plan, dict) else {}
    candidates, frozen = candidates_of(lab, w, plan)
    base = candidates[0].get("strategy", {}) if candidates else {}
    check = None
    current = lab.current()
    if w["status"] == "draft" and w.get("checked_hash") and current["workflow"] == wid:
        try:
            _, _, digest = lab.specifications()
            check = {"hash": w["checked_hash"], "stale": digest != w["checked_hash"]}
        except LabError as error:
            check = {"hash": w["checked_hash"], "stale": True, "error": str(error)}
    runs = [run_summary(lab, lab.store.get("run", r)) for r in w["runs"]]
    development = [r for r in runs if r["split"] == "development" and "key_value" in r]
    chart = {"kind": "bars", "metric": lab.task["key_metrics"][0], "threshold": key_constraint(lab),
             "bars": [{"label": r["candidate"], "value": r["key_value"], "verdict": r["verdict"], "run": r["id"]} for r in development]}
    review = sections(text_of(folder / "review.md"))
    confirmed = load(folder / "decision.confirmed.json") if (folder / "decision.confirmed.json").exists() else None
    previous = None
    earlier = [x for x in lab.store.all("workflow") if x["iteration"] < w["iteration"] and x["status"] == "decided"]
    if earlier:
        last = load(lab.root / earlier[-1]["folder"] / "decision.confirmed.json")
        previous = {"id": earlier[-1]["id"], "text": last["text"], "selected": last.get("selected_candidate")}
    approval = load(folder / "approval.json") if (folder / "approval.json").exists() else None
    return {"id": wid, "kind": "round", "iteration": w["iteration"], "status": w["status"],
            "locked": w["locked"], "errors": w["errors"], "source": w.get("source"),
            "editable": current["workflow"] == wid,
            "hypothesis": {"claim": claim, "alternative": alternative, "falsifier": falsifier},
            "plan": {"question": plan.get("question", ""), "derived_requirements": plan.get("derived_requirements", []),
                     "seeds": plan.get("seeds", []), "error": plan_error},
            "candidates": [{"id": c.get("id"), "rationale": c.get("rationale", ""), "infra": c.get("infra"),
                            "diff": strategy_diff(base, c.get("strategy", {})) if i else []} for i, c in enumerate(candidates)],
            "candidates_frozen": frozen, "check": check,
            "review": {"student": comments(text_of(folder / "review.md")), "agent": next((v for k, v in review.items() if "response" in k.lower()), "")},
            "approval": approval and {k: approval.get(k) for k in ("plan_hash", "at")},
            "runs": runs, "chart": chart, "analysis": text_of(folder / "analysis.md"),
            "claims": check_claims(lab, w) if (folder / "analysis.claims.json").exists() else None,
            "decision": {"text": confirmed["text"] if confirmed else text_of(folder / "decision.md"), "confirmed": bool(confirmed),
                         "selected": w.get("selected_candidate")}, "previous_decision": previous}


def run_view(lab, run_id):
    run = lab.store.get("run", run_id)
    if not run:
        raise LabError("E_SCHEMA", f"unknown run {run_id}", 2)
    view = run_summary(lab, run)
    view.update(kind="run", owner=run.get("workflow") or run.get("measurement"), config=config_name(lab, run))
    if run["status"] not in TERMINAL or not run.get("result_hash"):
        return {**view, "progress": run.get("progress")}
    result, spec = lab.result(run_id)
    folder = lab.root / run["folder"]
    manifest = load(folder / "manifest.json")
    effective = (manifest.get("effective_config") or {}).get("serving", {})
    paired = None
    if run.get("role") == "hardware":
        approval = load(lab.root / lab.store.get("measurement", run["measurement"])["folder"] / "approval.json")
        prediction = next((p for p in approval["sim_predictions"] if p["point"] == run["point"]), None)
        paired = prediction and {k: prediction[k] for k in ("sim_run", "simulated", "sim_margin_pct")}
    logs = text_of(folder / "logs.txt")
    return {**view, "evidence_level": result["evidence_level"], "metrics": result["metrics"], "unsupported": result["unsupported_metrics"],
            "quality": result["quality"], "coverage": result["coverage"], "requests": result["requests"],
            "strategy": spec["strategy"], "workload": spec["workload"]["requests"]["path"], "seed": spec["execution"]["seed"],
            "repeat": spec["execution"]["repeat"], "holdout_change": spec["execution"].get("holdout_change"),
            "config_diff": [{"field": f"serving.{k}", "requested": spec["strategy"]["serving"].get(k), "effective": v}
                            for k, v in effective.items() if spec["strategy"]["serving"].get(k) != v],
            "environment": manifest.get("environment"), "paired": paired, "logs": logs[:2000], "logs_truncated": len(logs) > 2000}


def run_requests(lab, run_id):
    """Per-request timing for the timeline chart, with the critical path of a dependency workload."""
    run = lab.store.get("run", run_id)
    if not run or run["status"] not in TERMINAL:
        raise LabError("E_SCHEMA", f"{run_id} has no finished requests", 2)
    _, spec = lab.result(run_id)
    rows = read_rows(lab.root / run["folder"] / "requests.jsonl")
    workload = {r["request_id"]: r for r in load_requests(lab.root, spec)}
    by_id = {r["request_id"]: r for r in rows}
    path = []
    if rows and any(r["depends_on"] for r in rows):
        # Walk back from the last request to finish, each time through the parent that finished last.
        current = max(rows, key=lambda r: r["finished_ms"])["request_id"]
        while current:
            path.append(current)
            parents = [p for p in by_id[current]["depends_on"] if p in by_id]
            current = max(parents, key=lambda p: by_id[p]["finished_ms"]) if parents else None
        path.reverse()
    def lane(identifier):
        source = workload.get(identifier, {})
        return source.get("target_model") or source.get("category") or "requests"
    return {"run": run_id, "candidate": run["candidate"], "duration_ms": spec["evaluation"]["duration_s"] * 1000,
            "lanes": list(dict.fromkeys(lane(r["request_id"]) for r in rows)), "critical_path": path,
            "rows": [{"id": r["request_id"], "lane": lane(r["request_id"]), "ready": rounded(r["ready_ms"]), "first": rounded(r["first_token_ms"]),
                      "finish": rounded(r["finished_ms"]), "status": r["status"], "depends_on": r["depends_on"],
                      "ttft": rounded(r["first_token_ms"] - r["ready_ms"]) if r["first_token_ms"] is not None else None,
                      "input_tokens": workload.get(r["request_id"], {}).get("input_tokens"), "critical": r["request_id"] in path} for r in rows]}


def point_choices(lab):
    """Candidates of the decided rounds, with the simulator's development result, for the points form."""
    metric = lab.task["key_metrics"][0]
    choices = []
    for w in lab.store.all("workflow"):
        if w["status"] != "decided":
            continue
        runs = [lab.store.get("run", r) for r in w["runs"]]
        for candidate in load(lab.root / w["folder"] / "frozen_plan.json")["candidates"]:
            line = {"workflow": w["id"], "iteration": w["iteration"], "candidate": candidate["id"],
                    "selected": w.get("selected_candidate") == candidate["id"], "rationale": candidate["rationale"]}
            done = [r for r in runs if r["candidate"] == candidate["id"] and r["split"] == "development" and r["status"] == "succeeded"]
            if done:
                result, _ = lab.result(done[-1]["id"])
                line.update(run=done[-1]["id"], value=rounded(get_metric(result["metrics"], metric)), margin_pct=margin(result, metric),
                            verdict=result["verdict"])
            choices.append(line)
    return choices


def draft_predictions(lab, m):
    """The simulator's predictions for points not yet approved, as mlab measure check would show them."""
    try:
        _, points, digest = lab.measure_points(m)
        rows = []
        for point in points:
            _, simulated = lab.point_specs(point)
            run = lab.sim_prediction(simulated)
            rows.append(lab.prediction_row(point, run) if run else {"point": point["index"], "candidate": point["candidate"],
                        "workflow": point["workflow"], "workload": point["workload"], "runs": point["runs"], "sim_run": None})
        return {"rows": rows, "hash": digest, "checked": m.get("checked_hash") == digest, "hardware_runs": sum(p["runs"] for p in points), "error": None}
    except LabError as error:
        return {"rows": [], "hash": None, "checked": False, "hardware_runs": 0, "error": str(error)}


def measurement_view(lab, mid):
    m = lab.store.get("measurement", mid)
    if not m:
        raise LabError("E_SCHEMA", f"unknown measurement {mid}", 2)
    folder = lab.root / m["folder"]
    frozen = load(folder / "frozen_points.json") if (folder / "frozen_points.json").exists() else None
    plan, plan_error = (frozen, None) if frozen else readable(folder / "points.yaml", {})
    points = (plan if isinstance(plan, dict) else {}).get("points") or []
    approval = load(folder / "approval.json") if (folder / "approval.json").exists() else None
    alignment = lab.alignment(m) if m["status"] == "submitted" else None
    current = lab.current()
    bars = [{"label": f"{row['candidate']}·{row['workload']}", "candidate": row["candidate"], "workload": row["workload"], "run": row["run_id"],
             "simulated": row["simulated"], "hardware": row["hardware"],
             "interval": row["hardware_interval"], "flip": row["verdict_flip"], "beyond_noise": row["beyond_noise"]} for row in (alignment or {}).get("rows", [])]
    return {"id": mid, "kind": "measurement", "status": m["status"], "stage": lab.measure_stage(m),
            "locked": m["locked"], "errors": m["errors"], "editable": current["measurement"] == mid,
            "points": [{k: p.get(k) for k in ("candidate", "workflow", "workload", "runs", "rationale")} for p in points if isinstance(p, dict)],
            "points_error": plan_error, "frozen": bool(frozen),
            "choices": point_choices(lab),
            "draft": draft_predictions(lab, m) if m["status"] == "draft" and points else None,
            "predictions": (approval or {}).get("sim_predictions"), "hardware_runs": (approval or {}).get("hardware_runs"),
            "runs": [run_summary(lab, lab.store.get("run", r)) for r in m["runs"]],
            "alignment": alignment and {"metric": alignment["metric"], "ranking": alignment["ranking"], "rank_flip": alignment["rank_flip"],
                                        "pending": alignment["pending"], "notes": alignment["notes"],
                                        "rows": [{k: v for k, v in row.items() if k != "evidence"} for row in alignment["rows"]]},
            "chart": {"kind": "paired", "metric": lab.task["key_metrics"][0], "threshold": key_constraint(lab), "bars": bars},
            "flagged": [r["run_id"] for r in (alignment or {}).get("rows", []) if r["beyond_noise"] or r["verdict_flip"] or r["config_diff"]],
            "budget": lab.hardware_budget(),
            "per_batch": lab.task["budget"]["hardware"]["runs_per_batch"]}


def alignment_pairs(lab, mid, run_id):
    """Simulated against measured TTFT per request, slowest first: does the whole run shift, or only the tail?"""
    m = lab.store.get("measurement", mid)
    if not m or m["status"] != "submitted":
        raise LabError("E_SCHEMA", f"{mid} has no hardware results", 2)
    row = next((r for r in lab.alignment(m)["rows"] if r["run_id"] == run_id), None)
    if not row:
        raise LabError("E_SCHEMA", f"{run_id} is not in {mid}", 2)
    def ttft(identifier):
        return {r["request_id"]: r["first_token_ms"] - r["ready_ms"] for r in read_rows(lab.root / lab.store.get("run", identifier)["folder"] / "requests.jsonl")
                if r["first_token_ms"] is not None}
    measured, simulated = ttft(run_id), ttft(row["sim_run"])
    pairs = sorted(({"id": i, "sim": rounded(simulated[i]), "hw": rounded(measured[i]), "ratio": round(measured[i] / simulated[i], 3)}
                    for i in measured if simulated.get(i)), key=lambda p: -p["hw"])
    slowest = max(3, len(pairs) // 4)  # The same cut as mlab align.
    for index, pair in enumerate(pairs):
        pair["slowest"] = index < slowest
    return {"run": run_id, "sim_run": row["sim_run"], "candidate": row["candidate"], "pairs": pairs, "shift": row["shift"],
            "per_request_ratio": row["per_request_ratio"], "slowest_ratio": row["slowest_ratio"], "interval": row["hardware_interval"],
            "simulated": row["simulated"], "hardware": row["hardware"]}


def comparison_view(lab, run_ids):
    found = lab.comparison(run_ids)
    specs = {run_id: lab.result(run_id)[1] for run_id in run_ids}
    groups = {}
    for row in found["rows"]:
        spec = specs[row["run_id"]]
        key = hashed([spec["workload"]["requests"]["path"], spec["evaluation"], spec["execution"]["split"], row["role"]])
        groups.setdefault(key, []).append(row["run_id"])
    base = specs[run_ids[0]]["strategy"]
    rows = [{**{k: v for k, v in row.items() if k not in {"metrics", "evidence"}}, "diff": strategy_diff(base, specs[row["run_id"]]["strategy"])}
            for row in found["rows"]]
    order = list(groups.values())
    return {**found, "rows": rows, "groups": order,
            "chart": {"kind": "bars", "metric": found["key_metric"], "threshold": key_constraint(lab),
                      "bars": [{"label": f"{r['candidate']}·{r['run_id']}", "value": r["key_metric_value"], "verdict": r["verdict"], "run": r["run_id"],
                                "group": next(i for i, g in enumerate(order) if r["run_id"] in g)} for r in rows]}}


def report_view(lab, lang=None):
    """The report in the page's language; report.md itself stays in English."""
    data = report_data(lab)
    language = lang or "en"
    markdown = render_report(lab, data, i18n.translator(language), lambda text: i18n.translate(language, text))
    return {"id": "report", "kind": "report", "markdown": markdown, "written": (lab.root / "report.md").exists(),
            "conclusion": data["conclusion"], "selected": data["selected_candidate"]}


def config_name(lab, run):
    """The configuration a run belongs to, as round/candidate."""
    if run.get("workflow"):
        return f"{run['workflow']}/{run['candidate']}"
    path = lab.root / lab.store.get("measurement", run["measurement"])["folder"] / "frozen_points.json"
    if run.get("point") is None or not path.exists():
        return None
    return f"{load(path)['points'][run['point']]['workflow']}/{run['candidate']}"


def config_view(lab, reference):
    """A configuration across the task: every simulation and hardware run of it, gaps to the frozen predictions, validation."""
    data = evidence(lab, reference)
    measured = [r for r in data["runs"] if r["role"] == "hardware" and r.get("value") is not None]
    if measured:
        chart = {"kind": "paired", "metric": data["metric"], "threshold": key_constraint(lab),
                 "bars": [{"label": f"{r['run']}·{r.get('holdout') or r['split']}", "candidate": r["run"], "workload": r.get("holdout") or "development",
                           "run": r["run"], "simulated": r.get("simulated"), "hardware": r["value"], "interval": None,
                           "flip": r.get("verdict_flip"), "beyond_noise": bool(r.get("shift"))} for r in measured]}
    else:
        chart = {"kind": "bars", "metric": data["metric"], "threshold": key_constraint(lab),
                 "bars": [{"label": f"{r['run']}·{r.get('owner') or '-'}", "value": r.get("value"), "verdict": r.get("verdict"), "run": r["run"]}
                          for r in data["runs"] if r.get("value") is not None]}
    return {**data, "chart": chart}


def record(lab, identifier, lang=None):
    """The view for any record ID: overview, report, r002, m001, r002/c16, sim_0001 or hw_0001."""
    if identifier == "overview":
        return task_notebook(lab)
    if identifier == "report":
        return report_view(lab, lang)
    kind, found = resolve(lab, identifier)
    if kind == "config":
        return config_view(lab, found["id"])
    return {"workflow": round_view, "measurement": measurement_view, "run": run_view}[kind](lab, found["id"])


def references(text):
    """Record IDs a student wrote with @ in chat, in order, without repeats."""
    return list(dict.fromkeys(REFERENCE.findall(text or "")))
