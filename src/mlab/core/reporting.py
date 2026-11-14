"""Reports are compiled from verified artifacts, never an LLM's summary."""
import json

from .io import NOTICE, atomic, hashed, load, require
from .metrics import get_metric
from .store import local
from .student import SOURCES, read_log


def number(value):
    return f"{value:.1f}" if type(value) in {int, float} else "-"


def english(template, /, **params):
    return template.format(**params) if params else template


def same(text):
    return text


def demote(markdown, title, tr=english):
    """Embedded records keep their headings, nested under the report's section; a heading that repeats its title is dropped.
    The record templates' headings are shown in the report's language."""
    lines = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            heading = line.strip().lstrip("#").strip()
            if heading == title:
                continue
            line = f"#### {tr(heading)}"
        lines.append(line)
    return "\n".join(lines).strip()


def text(folder, name):
    path = folder / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def verify(folder, record, files):
    for name, key in files:
        if record.get(key):
            require(hashed(load(folder / name)) == record[key], "E_FROZEN_CHANGED", "report source changed: " + name)


def report_data(lab):
    """Everything in the task's report, verified against the frozen records. Reads only."""
    iterations, measurements, runs = [], [], []
    profile = load(lab.root / "task/profile.json") if (lab.root / "task/profile.json").exists() else None
    for workflow in lab.store.all("workflow"):
        folder = lab.folder(workflow)
        verify(folder, workflow, [("approval.json", "approval_hash"), ("frozen_plan.json", "frozen_hash"),
                                  ("decision.confirmed.json", "decision_hash"), ("analysis.confirmed.json", "analysis_hash")])
        frozen = load(folder / "frozen_plan.json") if (folder / "frozen_plan.json").exists() else None
        if frozen:
            expected = hashed({"plan": frozen["plan"], "candidates": frozen["candidates"],
                               "hypothesis": hashed(folder / frozen["plan"]["hypothesis_ref"]), "task": hashed(lab.task)})
            require(expected == workflow["approved_hash"], "E_FROZEN_CHANGED", "report hypothesis or frozen plan changed")
        approval = load(folder / "approval.json") if workflow.get("approval_hash") else None
        confirmed = load(folder / "decision.confirmed.json") if workflow.get("decision_hash") else None
        analysis = load(folder / "analysis.confirmed.json")["text"] if workflow.get("analysis_hash") else text(folder, "analysis.md")
        iterations.append({"workflow": workflow["id"], "iteration": workflow["iteration"], "status": workflow["status"],
            "hypothesis": text(folder, "hypothesis.md"), "plan": frozen["plan"] if frozen else None,
            "review": (approval or {}).get("review") or text(folder, "review.md"),
            "analysis": analysis, "decision": confirmed["text"] if confirmed else text(folder, "decision.md"),
            "selected_candidate": workflow.get("selected_candidate"), "errors": workflow["errors"]})
    for measurement in lab.store.all("measurement"):
        folder = lab.mfolder(measurement)
        verify(folder, measurement, [("approval.json", "approval_hash"), ("frozen_points.json", "frozen_hash")])
        frozen = load(folder / "frozen_points.json") if measurement.get("frozen_hash") else None
        alignment = lab.alignment(measurement) if measurement["status"] == "submitted" else None
        measurements.append({"measurement": measurement["id"], "status": measurement["status"],
            "points": [{key: point[key] for key in ("candidate", "workflow", "workload", "runs", "rationale")} for point in frozen["points"]] if frozen else [],
            "alignment": alignment and {key: alignment[key] for key in ("metric", "rows", "ranking", "rank_flip", "pending")},
            "errors": measurement["errors"]})
    for record in lab.store.all("run"):
        owner = record.get("workflow") or record.get("measurement") or "-"
        if record["status"] not in {"succeeded", "failed", "cancelled", "timed_out"}:
            runs.append({"run_id": record["id"], "status": record["status"], "role": record.get("role", "simulator"), "owner": owner})
            continue
        result, spec = lab.result(record["id"])
        runs.append({"run_id": record["id"], "owner": owner, "role": record.get("role", "simulator"), "candidate": record["candidate"],
            "split": record["split"], "holdout": record["holdout"], "status": record["status"], "failure_class": result["failure_class"],
            "holdout_change": spec["execution"].get("holdout_change"),
            "verdict": result["verdict"], "evidence_level": result["evidence_level"],
            "metrics": result["metrics"], "constraints": result["constraints"],
            "evidence": str(lab.root / record["folder"] / "result.json"), "spec_hash": result["spec_hash"]})
    messages = read_log(lab.root)
    selection = lab.current()["selection"] or {}
    conclusions = [{key: c[key] for key in ("id", "at", "selection", "conclusion", "reason", "evidence")} for c in lab.store.all("conclusion")]
    latest = conclusions[-1] if conclusions else {}
    data = {"schema": "mlab.v1", "task": lab.task["id"], "objective": lab.task["objective"],
            "conclusion": latest.get("conclusion"), "conclusion_reason": latest.get("reason"), "conclusions": conclusions,
            "selected_candidate": selection.get("candidate"), "selection": selection or None,
            "hardware_budget": lab.hardware_budget(), "iterations": iterations, "measurements": measurements,
            "runs": runs, "student_messages": messages,
            "student_notes": [note for note in lab.store.all("note") if note.get("author") == "student"],
            "profile": profile, "notice": profile["notice"] if profile else NOTICE,
            "warning": "Small synthetic samples do not establish reliable p99 or real hardware improvements."}
    return data


def student_prose(data):
    """Separate authored words from UI action envelopes without changing the audit."""
    entries = []
    for message in data["student_messages"]:
        if message.get("source") == "web-action":
            continue
        if message.get("source") == "web-card" and message.get("kind") == "execution":
            try:
                action = json.loads(message["text"])
            except (ValueError, TypeError):
                continue
            reason = action.get("reason") if isinstance(action, dict) else None
            if isinstance(reason, str) and reason.strip():
                entries.append({**message, "text": reason})
        else:
            entries.append(message)
    entries.extend({**note, "source": "web-note"} for note in data.get("student_notes", []))
    return sorted(entries, key=lambda entry: entry.get("at", ""))


def render_report(lab, data, tr=english, translate=same):
    """report.md text for report_data(). report.md is English; the page passes tr (labels) and translate (sentences mlab
    wrote, such as a conclusion's basis) to show the report in its own language."""
    iterations, measurements, runs = data["iterations"], data["measurements"], data["runs"]
    messages = student_prose(data)
    budget = data["hardware_budget"]
    selection = data["selection"]
    chosen = f"{selection['workflow']}/{selection['candidate']}" if selection else "-"

    def row(*cells):
        return "| " + " | ".join(str(cell) for cell in cells) + " |"

    def yes(flag):
        return tr("yes") if flag else tr("no")

    lines = ["# " + tr("Report: {task} {title}", task=data["task"], title=tr(lab.task["title"])), "", tr(data["notice"]), "",
             tr("The sample is small: it supports no reliable p99 conclusion and shows no improvement on real hardware."), "",
             row(tr("Item"), tr("Details")), "| --- | --- |",
             row(tr("Conclusion"), data["conclusion"] or tr("not submitted yet")),
             row(tr("Basis"), translate(data["conclusion_reason"]) or "-"),
             row(tr("Current selection"), chosen),
             row(tr("Hardware budget"), tr("{used} of {total} runs used", used=budget["used"], total=budget["total"])), ""]
    if data["conclusions"]:
        lines += ["## " + tr("Submitted conclusions"), "", row("#", tr("Time"), tr("Configuration"), tr("Conclusion"), tr("Basis")),
                  "| --- | --- | --- | --- | --- |"]
        for c in data["conclusions"]:
            lines.append(row(c["id"], local(c["at"]), f"{c['selection']['workflow']}/{c['selection']['candidate']}", c["conclusion"], translate(c["reason"])))
        lines += ["", tr("The latest one counts."), ""]
    for iteration in iterations:
        lines += ["## " + tr("Simulation round {n} ({round})", n=iteration["iteration"], round=iteration["workflow"]), ""]
        for title, key in (("Hypothesis and falsification condition", "hypothesis"), ("Comments on the plan", "review"), ("Analysis", "analysis"),
                           ("Decision", "decision")):
            lines += ["### " + tr(title), "", demote(iteration[key], title, tr) or "-", ""]
    for measurement in measurements:
        lines += ["## " + tr("Measurement {id}", id=measurement["measurement"]), "", "### " + tr("Hardware points"), "",
                  row(tr("Candidate"), tr("Round"), tr("Workload"), tr("No. of runs"), tr("Reason")), "| --- | --- | --- | ---: | --- |"]
        for point in measurement["points"]:
            lines.append(row(point["candidate"], point["workflow"], point["workload"], point["runs"], point["rationale"]))
        alignment = measurement["alignment"]
        if alignment and alignment["rows"]:
            lines += ["", "### " + tr("Simulation vs. hardware ({metric})", metric=alignment["metric"]), "",
                      row(tr("Hardware run"), tr("Candidate"), tr("Workload"), tr("Simulated"), tr("Hardware"), tr("Gap"), tr("Paired shift"),
                          tr("Verdict flipped"), tr("Configuration differences")),
                      "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |"]
            for item in alignment["rows"]:
                gap = f"{item['gap_pct']:+.1f}%" if item["gap_pct"] is not None else "-"
                lines.append(row(item["run_id"], item["candidate"], item["workload"], number(item["simulated"]), number(item["hardware"]), gap,
                                 yes(item["beyond_noise"]), yes(item["verdict_flip"]), "; ".join(item["config_diff"]) or "-"))
            lines += ["", tr("Paired shifts use fixed thresholds; they do not establish statistical significance or simulator accuracy."),
                         "", tr("Order: simulated {sim}; hardware {hw}.", sim=" < ".join(alignment["ranking"]["simulated"]) or "-",
                             hw=" < ".join(alignment["ranking"]["hardware"]) or "-")]
        lines += [""]
    columns = list(dict.fromkeys(filter(None, [lab.task["key_metrics"][0], lab.task["objective"].get("metric")])))
    lines += ["## " + tr("Run evidence"), "", row(tr("Run"), tr("Source"), tr("Candidate"), tr("Split"), tr("Status"), tr("Constraints"), *columns),
              "| --- | --- | --- | --- | --- | --- | " + " | ".join("---:" for _ in columns) + " |"]
    for item in runs:
        split = item.get("split", "")
        if item.get("holdout"):
            split += ": " + item["holdout"]
        status = item["status"] + (f" ({item['failure_class']})" if item.get("failure_class") else "")
        source = tr("{owner}, hardware" if item["role"] == "hardware" else "{owner}, simulation", owner=item["owner"])
        values = [number(get_metric(item.get("metrics", {}), column)) for column in columns]
        lines.append(row(item["run_id"], source, item.get("candidate", ""), split, status, item.get("verdict", ""), *values))
    if messages:
        lines += ["", "## " + tr("Your original words"), "",
                  tr("Your original notes and comments, kept alongside the experiment records."), ""]
        for message in messages:
            source = "note" if message.get("source") == "web-note" else SOURCES.get(message.get("source"), "terminal")
            lines.append(tr("- {at} ({source}): {text}", at=local(message["at"], seconds=True), source=tr(source), text=message["text"].strip()))
            if message.get("refs"):
                lines.append("  " + tr("References") + ": " + ", ".join(f"`{ref}`" for ref in message["refs"]))
    lines += ["", tr("Full metrics, the simulation-vs-hardware data and the evidence paths are in report.json."), ""]
    return "\n".join(lines)


def build_report(lab):
    data = report_data(lab)
    directory = lab.root
    atomic(directory / "report.json", data)
    atomic(directory / "report.md", render_report(lab, data), text=True)
    return {"result_path": str(directory / "report.md"), "data_path": str(directory / "report.json"),
            "runs": len(data["runs"]), "conclusion": data["conclusion"], "selected_candidate": data["selected_candidate"]}
