"""The student's own words: verbatim writes into the records, and the log of who said what.

Forms on the web page write here directly, so the records hold exactly what the student typed. Every
writer checks the same gates the CLI does and changes only the student's section of a shared file.
"""
import json
import os
from pathlib import Path
import yaml
from .io import LabError, atomic, load, require
from .store import Store, local, now
from .workspace import RUN_ID, comments

LOG = ".mlab/student.jsonl"
VERDICTS = {"keep": "keep", "change": "change", "drop": "drop"}
SOURCES = {"web-form": "form", "web-chat": "chat", "web-question": "question card", "web-card": "approval card", "cli": "terminal"}


def log_student(root, text, *, source, kind, workflow=None, measurement=None, refs=None, session=None):
    """Append the student's words, tagged with where they came from and the round or measurement at the time."""
    text = (text or "").strip()
    if not text:
        return None
    entry = {"at": now(), "source": source, "kind": kind, "workflow": workflow,
             "measurement": measurement, "refs": list(refs or []), "session": session, "text": text[:4000]}
    with open(Path(root) / LOG, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_log(root):
    """Logged student words, oldest first."""
    path = Path(root) / LOG
    entries = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def position(root):
    """The task's current round and measurement, to tag the student's words; empty when the ledger cannot be read."""
    try:
        store = Store(Path(root), readonly=True)
        try:
            current = store.get("settings", "task") or {}
            return {"workflow": current.get("workflow"), "measurement": current.get("measurement")}
        finally:
            store.close()
    except Exception:
        return {}


def log_prompt(root, payload):
    """UserPromptSubmit hook in the terminal. On the web page the server logs the student itself, so prompts it composes stay out."""
    if os.environ.get("MLAB_PROMPT_SOURCE") == "web":
        return
    text = payload.get("prompt") if isinstance(payload, dict) else None
    if isinstance(text, str) and text.strip():
        log_student(root, text, source="cli", kind="chat", session=payload.get("session_id"), **position(root))


def put_section(text, keyword, heading, block, append=True):
    """Write a block under the level-two heading containing keyword, keeping every other section intact."""
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if line.lstrip().startswith("#") and keyword in line), None)
    if index is None:
        return (text.rstrip() + f"\n\n{heading}\n\n{block}\n").lstrip()
    end = next((j for j in range(index + 1, len(lines)) if lines[j].lstrip().startswith(("# ", "## "))), len(lines))
    body = "\n".join(lines[index + 1:end]).strip()
    merged = f"{body}\n\n{block}" if append and body else block
    return "\n".join(lines[:index + 1] + ["", merged.strip(), ""] + lines[end:]).rstrip() + "\n"


def stamp(source):
    return f"({local()}, {SOURCES.get(source, source)})"


def draft_round(lab):
    w = lab.workflow()
    require(w["status"] == "draft", "E_NEEDS_APPROVAL", "This round is approved; its hypothesis and plan comments can no longer change. Start a new round instead")
    return w


def write_hypothesis(lab, claim, alternative, falsifier, source="web-form"):
    claim, alternative, falsifier = (claim or "").strip(), (alternative or "").strip(), (falsifier or "").strip()
    require(claim and falsifier, "E_SCHEMA", "Write the hypothesis and what result would prove it wrong", 2)
    created = False
    if not lab.current()["workflow"] or lab.workflow()["status"] == "decided":
        lab.new_workflow()
        created = True
    w = draft_round(lab)
    path = lab.folder(w) / "hypothesis.md"
    atomic(path, f"## Hypothesis\n\n{claim}\n\n## Alternative explanation\n\n{alternative or '(none given)'}\n\n"
                 f"## What result would prove me wrong\n\n{falsifier}\n", text=True)
    log_student(lab.root, f"Hypothesis: {claim}\nAlternative explanation: {alternative}\nWhat result would prove me wrong: {falsifier}", source=source,
                kind="hypothesis", workflow=w["id"])
    return {"workflow": w["id"], "written": [path.relative_to(lab.root).as_posix()], "created_round": created,
            "plan_check_stale": bool(w.get("checked_hash"))}


def candidate_ids(lab, w):
    plan = load(lab.folder(w) / "plan.yaml")
    ids = []
    for relative in plan.get("candidates", []):
        try:
            ids.append(load(lab.folder(w) / relative)["id"])
        except (OSError, KeyError, TypeError, ValueError, LabError):
            continue
    return ids


def write_review(lab, entries, general="", source="web-form"):
    """entries: [{candidate, verdict: keep|change|drop, reason}]; at least one entry or a general comment."""
    w = draft_round(lab)
    known = candidate_ids(lab, w)
    lines = []
    for entry in entries or []:
        require(entry.get("candidate") in known, "E_SCHEMA", f"The plan has no candidate {entry.get('candidate')}", 2)
        require(entry.get("verdict") in VERDICTS, "E_SCHEMA", "Mark each candidate keep, change or drop", 2)
        reason = (entry.get("reason") or "").strip()
        lines.append(f"- {entry['candidate']}: {VERDICTS[entry['verdict']]}. {reason}".rstrip())
    if (general or "").strip():
        lines.append(f"- Overall: {general.strip()}")
    require(lines, "E_SCHEMA", "Comment on at least one candidate, or write an overall comment", 2)
    path = lab.folder(w) / "review.md"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic(path, put_section(current, "Comments on the plan", "## Comments on the plan", stamp(source) + "\n" + "\n".join(lines)), text=True)
    require(comments(path.read_text(encoding="utf-8")), "E_SCHEMA", "The comments did not reach review.md", 2)
    log_student(lab.root, "\n".join(lines), source=source, kind="review", workflow=w["id"])
    keep_all = bool(entries) and all(entry["verdict"] == "keep" for entry in entries) and set(known) <= {e["candidate"] for e in entries}
    return {"workflow": w["id"], "written": [path.relative_to(lab.root).as_posix()], "keep_all": keep_all}


def write_decision(lab, text, candidate=None, source="web-form"):
    w = lab.workflow()
    require(w["status"] == "submitted" and lab.status()["status"] == "completed", "E_NEEDS_APPROVAL", "This round has no results yet, or is already decided")
    text = (text or "").strip()
    require(text, "E_SCHEMA", "Write the decision and the reasons", 2)
    require(not w["runs"] or set(RUN_ID.findall(text)) & set(w["runs"]), "E_SCHEMA",
            f"Cite at least one run of this round in the decision, e.g. {w['runs'][0] if w['runs'] else 'sim_0001'}", 2)
    if candidate:
        frozen = load(lab.folder(w) / "frozen_plan.json")
        require(candidate in {c["id"] for c in frozen["candidates"]}, "E_SCHEMA", f"This round has no candidate {candidate}", 2)
    path = lab.folder(w) / "decision.md"
    atomic(path, text + "\n", text=True)
    log_student(lab.root, text, source=source, kind="decision", workflow=w["id"])
    return {"workflow": w["id"], "written": [path.relative_to(lab.root).as_posix()], "candidate": candidate}


def write_points(lab, points, source="web-form"):
    """points: [{candidate, workflow?, workload, runs, rationale}]. Creates the measurement when needed."""
    m = lab.measurement(required=False)
    if lab.measure_stage(m) in {None, "measured"}:
        lab.measure_new()
        m = lab.measurement()
    require(m["status"] == "draft", "E_NEEDS_APPROVAL", "The points of this measurement are approved; start a new measurement to change them")
    cleaned = []
    for point in points or []:
        item = {"candidate": point.get("candidate"), "workload": point.get("workload") or "development",
                "runs": int(point.get("runs") or 1), "rationale": (point.get("rationale") or "").strip()}
        if point.get("workflow"):
            item["workflow"] = point["workflow"]
        cleaned.append(item)
    require(cleaned, "E_SCHEMA", "Choose at least one hardware point", 2)
    path = lab.mfolder(m) / "points.yaml"
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    header = "# Hardware points chosen in the web form; each rationale is the student's own words.\n"
    atomic(path, header + yaml.safe_dump({"schema": "mlab.v1", "measurement": m["id"], "points": cleaned},
                                         allow_unicode=True, sort_keys=False), text=True)
    try:
        _, resolved, _ = lab.measure_points(m)
    except LabError:
        if previous is not None:
            atomic(path, previous, text=True)
        raise
    total = sum(p["runs"] for p in resolved)
    log_student(lab.root, "\n".join(f"- {p['candidate']} ({p['workload']}, {p['runs']}×): {p['rationale']}"
                                    for p in cleaned), source=source, kind="points", measurement=m["id"])
    return {"measurement": m["id"], "written": [path.relative_to(lab.root).as_posix()], "hardware_runs": total,
            "hardware_budget": lab.hardware_budget(), "per_batch": lab.task["budget"]["hardware"]["runs_per_batch"]}


def record_choice(lab, action, detail="", source="web-form"):
    current = lab.current()
    return log_student(lab.root, f"Next step: {action}" + (f". {detail.strip()}" if (detail or "").strip() else ""), source=source,
                       kind="choice", workflow=current["workflow"], measurement=current["measurement"])

