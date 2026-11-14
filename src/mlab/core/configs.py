"""Everything known about one configuration of a task: the rounds it appears in, its simulations, its hardware runs
and their gaps to the simulator, and which validation it has. Reads only, so `mlab show` and the web page share it."""
import re
from .io import load, require
from .metrics import get_metric

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
CONFIG_ID = re.compile(r"r\d{3}/[A-Za-z0-9][A-Za-z0-9_-]{0,47}")


def config_of(lab, reference):
    """{workflow, candidate, config_hash} for r002/c16, a candidate of an approved round."""
    rid, _, cid = (reference or "").strip().lstrip("@").partition("/")
    w = lab.store.get("workflow", rid)
    path = lab.root / w["folder"] / "frozen_plan.json" if w else None
    require(w and cid and path.exists(), "E_SCHEMA", f"{reference} is not a candidate of an approved round, e.g. r002/c16", 2)
    candidate = next((c for c in load(path)["candidates"] if c["id"] == cid), None)
    require(candidate, "E_SCHEMA", f"{rid} has no candidate {cid}", 2)
    return lab.selection_for(w, candidate)


def names(lab, digest):
    """Every round/candidate whose approved configuration has this hash, oldest first."""
    found = []
    for w in lab.store.all("workflow"):
        path = lab.root / w["folder"] / "frozen_plan.json"
        if path.exists():
            found += [f"{w['id']}/{c['id']}" for c in load(path)["candidates"] if lab.selection_for(w, c)["config_hash"] == digest]
    return found


def pairs(lab, m):
    """A measurement's hardware runs next to the simulator's frozen predictions; empty before any hardware run."""
    return lab.alignment(m)["rows"] if m["status"] == "submitted" else []


def validation(lab, runs):
    """Keep unassessed or incomplete runs distinct from violated constraints."""
    matrix = {}
    def combine(previous, current):
        values = [previous, current]
        if False in values:
            return False
        for status in ("incomplete", "not_evaluated"):
            if status in values:
                return status
        return current
    for run in runs:
        if run["status"] not in TERMINAL:
            continue
        cell = matrix.setdefault(run.get("role", "simulator"),
                                 {"development": None, "repeat": None, "holdout": {name: None for name in lab.task["holdouts"]}})
        ok = (False if run.get("verdict") == "constraints_violated" else "incomplete" if run["status"] != "succeeded" else
              True if run.get("verdict") == "constraints_met" else "not_evaluated")
        if run["split"] == "holdout":
            previous = cell["holdout"].get(run["holdout"])
            cell["holdout"][run["holdout"]] = combine(previous, ok)
        else:
            previous = cell[run["split"]]
            cell[run["split"]] = combine(previous, ok)
    missing = {}
    for role, cell in matrix.items():
        gaps = [split for split in ("development", "repeat") if cell[split] is not True]
        gaps += [f"holdout:{name}" for name, ok in cell["holdout"].items() if ok is not True]
        missing[role] = gaps
    return matrix, missing


def evidence(lab, reference):
    """The configuration's page: names, every run with its key metric, gaps to the frozen predictions, validation."""
    selection = config_of(lab, reference)
    digest = selection["config_hash"]
    metric = lab.task["key_metrics"][0]
    everything = lab.store.all("run")
    runs = [r for r in everything if r.get("config_hash") == digest]
    gaps = {}
    for m in lab.store.all("measurement"):
        if any(run.get("measurement") == m["id"] for run in runs):
            gaps.update({row["run_id"]: row for row in pairs(lab, m)})
    rows = []
    for run in runs:
        row = {"run": run["id"], "role": run.get("role", "simulator"), "owner": run.get("workflow") or run.get("measurement"),
               "split": run["split"], "holdout": run.get("holdout"), "status": run["status"]}
        if run["status"] in TERMINAL:
            result, _ = lab.result(run["id"])
            value = get_metric(result["metrics"], metric)
            row.update(value=round(value, 1) if type(value) in {int, float} else value, verdict=result["verdict"])
        if run["id"] in gaps:
            gap = gaps[run["id"]]
            row.update(sim_run=gap["sim_run"], simulated=gap["simulated"],
                       gap_pct=gap["gap_pct"], shift=gap["shift"], verdict_flip=gap["verdict_flip"])
        rows.append(row)
    matrix, missing = validation(lab, runs)
    level, reason = lab.conclusion_for(selection, everything)
    current = lab.current()["selection"] or {}
    return {"id": f"{selection['workflow']}/{selection['candidate']}", "kind": "config", "workflow": selection["workflow"],
            "candidate": selection["candidate"], "config_hash": digest, "names": names(lab, digest), "metric": metric,
            "runs": rows, "validation": matrix, "missing": missing, "selected": current.get("config_hash") == digest,
            "conclusions": [c["id"] for c in lab.store.all("conclusion") if c["selection"]["config_hash"] == digest],
            "preview": level, "reason": reason}


def catalog(lab):
    """Every approved configuration of the task under its first name, with its run counts and what validation still lacks."""
    named = {}
    for w in lab.store.all("workflow"):
        path = lab.root / w["folder"] / "frozen_plan.json"
        if path.exists():
            for c in load(path)["candidates"]:
                named.setdefault(lab.selection_for(w, c)["config_hash"], []).append(f"{w['id']}/{c['id']}")
    runs = {}
    for run in lab.store.all("run"):
        runs.setdefault(run.get("config_hash"), []).append(run)
    selected = (lab.current()["selection"] or {}).get("config_hash")
    found = []
    for digest, aliases in named.items():
        mine = runs.get(digest, [])
        _, missing = validation(lab, mine)
        found.append({"id": aliases[0], "names": aliases, "selected": digest == selected,
                      "simulated": sum(1 for r in mine if r.get("role", "simulator") == "simulator"),
                      "measured": sum(1 for r in mine if r.get("role") == "hardware"), "missing": missing})
    return found
