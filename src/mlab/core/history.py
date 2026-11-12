"""Earlier records of a task: compact enough for the agent's context, and the facts behind @references on the page.

Everything here only reads, so the web views can share it.
"""
import re
from .configs import CONFIG_ID, config_of, evidence, pairs
from .io import LabError, load, require
from .metrics import get_metric
from .workspace import RUN_ID, hypothesis_parts, sections

PAGE_ITEMS = 8
PAGE_CHARS = 600
IDS = "r002, m001, r002/c16, sim_0001, hw_0001"


def short(text, limit=80):
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def resolve(lab, identifier):
    """(kind, record) for a round r002, a measurement m001, a configuration r002/c16, or a run sim_0001 / hw_0001."""
    identifier = (identifier or "").strip().lstrip("@")
    if re.fullmatch(r"r\d{3}", identifier):
        record, kind = lab.store.get("workflow", identifier), "workflow"
    elif re.fullmatch(r"m\d{3}", identifier):
        record, kind = lab.store.get("measurement", identifier), "measurement"
    elif CONFIG_ID.fullmatch(identifier):
        record, kind = {**config_of(lab, identifier), "id": identifier}, "config"
    elif RUN_ID.fullmatch(identifier):
        record, kind = lab.store.get("run", identifier), "run"
    else:
        raise LabError("E_SCHEMA", f"{identifier} is not a record ID ({IDS})", 2)
    require(record, "E_SCHEMA", f"no record {identifier}", 2)
    return kind, record


def text_of(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run_line(lab, run):
    metric = lab.task["key_metrics"][0]
    if run["status"] not in {"succeeded", "failed", "cancelled", "timed_out"}:
        return {"id": run["id"], "candidate": run["candidate"], "status": run["status"]}
    result, _ = lab.result(run["id"])
    value = get_metric(result["metrics"], metric)
    return {"id": run["id"], "candidate": run["candidate"], "split": run["split"] + (f":{run['holdout']}" if run.get("holdout") else ""),
            "role": run.get("role", "simulator"),
            metric: round(value, 1) if type(value) in {int, float} else value, "verdict": result["verdict"],
            **({"failure": result["failure_class"]} if result["failure_class"] else {})}


def owner_label(run):
    return run.get("workflow") or run.get("measurement") or "-"


def measurement_status(lab, m):
    """The record's status, or "measured" once every hardware run has finished."""
    return "measured" if lab.measure_stage(m) == "measured" else m["status"]


def brief(lab, identifier):
    """One line of facts for a referenced record."""
    kind, record = resolve(lab, identifier)
    if kind == "run":
        line = run_line(lab, record)
        metric = lab.task["key_metrics"][0]
        facts = [owner_label(record), record["candidate"], line.get("split", record["split"]),
                 "hardware" if line.get("role") == "hardware" else "simulation"]
        if metric in line:
            facts.append(f"{metric}={line[metric]}")
        facts.append(line.get("verdict") or line.get("status"))
        return f"{record['id']} ({', '.join(str(f) for f in facts if f)})"
    if kind == "workflow":
        names = [load(lab.root / record["folder"] / "frozen_plan.json")["candidates"]] if (lab.root / record["folder"] / "frozen_plan.json").exists() else []
        candidates = ", ".join(c["id"] for c in names[0]) if names else "not approved"
        return f"{record['id']} (round {record['iteration']}, {record['status']}, candidates {candidates}" + \
               (f", selected {record['selected_candidate']}" if record.get("selected_candidate") else "") + ")"
    if kind == "measurement":
        rows = pairs(lab, record)
        apart = [r["run_id"] for r in rows if r["beyond_noise"] or r["verdict_flip"] or r["config_diff"]]
        return f"{record['id']} ({measurement_status(lab, record)}, {len([r for r in record['runs'] if r.startswith('hw_')])} hardware runs" + \
               (f", off from the simulation: {', '.join(apart)}" if apart else "") + ")"
    if kind == "config":
        data = evidence(lab, record["id"])
        simulated = sum(1 for r in data["runs"] if r["role"] == "simulator")
        measured = sum(1 for r in data["runs"] if r["role"] == "hardware")
        missing = data["missing"].get("hardware") if "hardware" in data["missing"] else None
        return f"{record['id']} ({simulated} simulation runs, {measured} hardware runs" + \
               (f", hardware validation still missing {', '.join(missing)}" if missing else "") + f", conclusion on current evidence {data['preview']})"


def round_item(lab, w):
    claim, _ = hypothesis_parts(text_of(lab.root / w["folder"] / "hypothesis.md"))
    decision = text_of(lab.root / w["folder"] / "decision.md")
    return {"id": w["id"], "kind": "round", "iteration": w["iteration"], "status": w["status"], "hypothesis": short(claim, 60),
            "runs": len(w["runs"]), "selected": w.get("selected_candidate"), "decision": short(decision, 60), "source": w.get("source"),
            "at": w["created_at"]}


def measurement_item(lab, m):
    points = load(lab.root / m["folder"] / "frozen_points.json")["points"] if (lab.root / m["folder"] / "frozen_points.json").exists() else []
    return {"id": m["id"], "kind": "measurement", "status": measurement_status(lab, m), "runs": len([r for r in m["runs"] if r.startswith("hw_")]),
            "points": sorted({f"{p['workflow']}/{p['candidate']}" for p in points}), "at": m["created_at"]}


def conclusion_item(record):
    selection = record["selection"]
    return {"id": f"conclusion:{record['id']}", "kind": "conclusion", "conclusion": record["conclusion"],
            "selected": f"{selection['workflow']}/{selection['candidate']}", "at": record["at"]}


def timeline(lab):
    """Rounds, measurements and submitted conclusions of the task, oldest first."""
    items = [round_item(lab, w) for w in lab.store.all("workflow")]
    items += [measurement_item(lab, m) for m in lab.store.all("measurement")]
    items += [conclusion_item(c) for c in lab.store.all("conclusion")]
    return sorted(items, key=lambda item: item["at"])


def history(lab, page=1):
    require(page >= 1, "E_SCHEMA", "--page must be positive", 2)
    items = timeline(lab)
    start = (page - 1) * PAGE_ITEMS
    shown = [{k: v for k, v in item.items() if k not in {"at", "source"} and v not in (None, "", [])}
             for item in items[start:start + PAGE_ITEMS]]
    return {"page": page, "items": shown, "has_more": start + PAGE_ITEMS < len(items),
            "hint": f"mlab show ID for details ({IDS}); mlab show ID --part hypothesis|review|analysis|decision|points for text; "
                    "mlab align m001 sets a measurement against the simulator."}


PARTS = {"workflow": {"hypothesis": "hypothesis.md", "plan": "plan.yaml", "review": "review.md", "analysis": "analysis.md", "decision": "decision.md"},
         "measurement": {"points": "points.yaml"}}


def show(lab, identifier, part=None, page=1):
    kind, record = resolve(lab, identifier)
    require(page >= 1, "E_SCHEMA", "--page must be positive", 2)
    if part:
        require(part in PARTS.get(kind, {}), "E_SCHEMA", f"{kind} parts: {', '.join(PARTS.get(kind, {})) or 'none'}", 2)
        content = text_of(lab.root / record["folder"] / PARTS[kind][part])
        start = (page - 1) * PAGE_CHARS
        return {"id": identifier, "part": part, "page": page, "text": content[start:start + PAGE_CHARS], "has_more": start + PAGE_CHARS < len(content)}
    if kind == "run":
        result, spec = lab.result(record["id"])
        line = run_line(lab, record)
        constraints = {c["metric"]: c["met"] for c in result["constraints"] if c["met"] is not None}
        manifest = load(lab.root / record["folder"] / "manifest.json")
        effective = (manifest.get("effective_config") or {}).get("serving", {})
        differences = [f"{k}: {spec['strategy']['serving'].get(k)}→{v}" for k, v in effective.items() if spec["strategy"]["serving"].get(k) != v]
        return {"id": record["id"], "kind": kind, "owner": owner_label(record), **line, "constraints_met": constraints,
                "serving": {k: v for k, v in spec["strategy"]["serving"].items() if v is not None},
                **({"config_diff": differences} if differences else {}), "result_path": str(lab.root / record["folder"] / "result.json")}
    if kind == "workflow":
        folder = lab.root / record["folder"]
        claim, falsifier = hypothesis_parts(text_of(folder / "hypothesis.md"))
        review = next((v for k, v in sections(text_of(folder / "review.md")).items() if "comment" in k.lower()), "")
        runs = [run_line(lab, lab.store.get("run", r)) for r in record["runs"]]
        return {"id": record["id"], "kind": kind, "iteration": record["iteration"], "status": record["status"],
                "hypothesis": short(claim, 120), "falsifier": short(falsifier, 120), "review": short(review, 160),
                "runs": runs[:8], "decision": short(text_of(folder / "decision.md"), 160), "selected": record.get("selected_candidate"),
                **({"from": record["source"]} if record.get("source") else {})}
    if kind == "measurement":
        folder = lab.root / record["folder"]
        points = load(folder / "frozen_points.json")["points"] if (folder / "frozen_points.json").exists() else []
        return {"id": record["id"], "kind": kind, "status": measurement_status(lab, record),
                "points": [{k: p[k] for k in ("workflow", "candidate", "workload", "runs")} for p in points],
                "vs_simulator": [{"run": r["run_id"], "candidate": r["candidate"], "sim": r["simulated"], "hw": r["hardware"],
                                  "gap_pct": r["gap_pct"], "shift": r["shift"], "verdict_flip": r["verdict_flip"],
                                  "ratio_median": (r["per_request_ratio"] or {}).get("median"),
                                  "slowest_median": (r["slowest_ratio"] or {}).get("median")} for r in pairs(lab, record)]}
    if kind == "config":
        data = evidence(lab, record["id"])
        def line(row):
            keep = ("run", "owner", "split", "holdout", "value", "verdict", "gap_pct", "shift")
            return {k: row[k] for k in keep if row.get(k) is not None}
        return {"id": data["id"], "kind": kind, "names": data["names"][:6], "metric": data["metric"],
                "simulated": [line(r) for r in data["runs"] if r["role"] == "simulator"][:6],
                "measured": [line(r) for r in data["runs"] if r["role"] == "hardware"][:6],
                "missing": data["missing"], "selected": data["selected"], "conclusion_preview": data["preview"]}
