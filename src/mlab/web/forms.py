"""Student forms on the page: the student's words go into the records, and the student's own confirmations run here.

Every form goes through core.student or the Lab method the CLI calls, so the checks are the same as in the terminal.
Errors come back in the CLI's envelope and are not counted toward the agent's repeated-error lock. Each form also
returns a notice: what the page tells the agent, marked [Page notice] so that it is never taken for the student's words.
Its first line is a short summary, also shown in the chat; the lines after it are instructions for the agent.
"""
import yaml
from pydantic import ValidationError
from ..core import student
from ..core.io import LabError, require
from ..core.reporting import build_report
from ..core.workspace import Lab
from . import i18n

NOTICE = "[Page notice]"
THEN = {"notify", "confirm", "save"}
CHOICES = {"round": "another simulation round", "measure": "hardware points", "validate": "validation on the simulator", "conclude": "a conclusion"}


def rejected(error):
    if not isinstance(error, LabError):
        error = LabError("E_SCHEMA", str(error)[:350], 2)
    return {"ok": False, "status": "rejected", "error": {"code": error.code, "message": str(error)[:350], "field": error.field},
            "next_actions": error.actions}


def run_action(root, action):
    """action(lab) on a writable Lab, then state.yaml and the ledger projection are refreshed as the CLI does."""
    lab = None
    try:
        lab = Lab(root)
        output = action(lab) or {}
        output.setdefault("next_actions", lab.next()["next_actions"])
        lab.store.project()
        return {"ok": True, "status": "ok", **output}
    except (LabError, ValidationError, ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as error:
        if lab:
            lab.store.rollback()
        return rejected(error)
    finally:
        if lab:
            lab.close()


def after_saving(label, confirm):
    """Run a confirmation after the student's words were saved; a refusal says the words are kept."""
    try:
        return confirm()
    except LabError as error:
        raise LabError(error.code, f"{label} saved, but the confirmation failed: {error}", error.exit_code, error.field, error.actions) from error


def hypothesis(lab, body):
    out = student.write_hypothesis(lab, body.get("claim"), body.get("alternative"), body.get("falsifier"))
    return out, f"Hypothesis saved from the form to {out['written'][0]}.\nThese are the student's own words; do not rewrite them."


def review(lab, body):
    entries = body.get("entries") or []
    out = student.write_review(lab, entries, body.get("general", ""), tr=i18n.translator(body.get("lang")))
    if body.get("then") == "confirm":
        w = lab.workflow()
        def approve():
            require(not any(entry.get("verdict") in {"change", "drop"} for entry in entries), "E_NEEDS_APPROVAL",
                    "Revise the candidates marked change or drop before confirming")
            require(w.get("checked_hash"), "E_NEEDS_APPROVAL", "Check the plan before confirming")
            return lab.approve(w["checked_hash"])
        out.update(after_saving("Comments", approve))
        return out, f"Plan comments saved and the plan of {w['id']} approved on the page.\nDo not run mlab workflow approve again."
    return out, ("Plan comments saved from the form to review.md.\nRevise the candidates as the comments ask and write the changes under "
                 "'Changes and responses'; after that, check the plan with mlab workflow check and ask for approval.")


def decision(lab, body):
    candidate = body.get("candidate") or None
    out = student.write_decision(lab, body.get("text"), candidate)
    w = lab.workflow()
    chosen = f", selecting {candidate}" if candidate else ""
    if body.get("then") == "confirm":
        out.update(after_saving("Decision", lambda: lab.decide(candidate)))
        return out, f"Decision saved and confirmed on the page for {w['id']}{chosen}.\nDo not run mlab workflow decide again."
    flag = f" --candidate {candidate}" if candidate else ""
    return out, f"Decision saved from the form to decision.md{chosen}.\nPlease run mlab workflow decide{flag}, then wait for the confirmation."


def next_step(lab, body):
    choice = body.get("choice")
    require(choice in CHOICES, "E_SCHEMA", "Choose the next step: round, measure, validate or conclude", 2)
    student.record_choice(lab, CHOICES[choice], body.get("detail", ""))
    if choice == "round":
        source = body.get("source") or None
        out = lab.new_workflow(source=source)
        origin = f" with the candidates of {source}" if source else ""
        return out, f"Next step: another simulation round; {out['workflow']} created{origin}.\nWait for the student's hypothesis."
    if choice == "measure":
        out = lab.measure_new()
        return out, (f"Next step: hardware points; {out['measurement']} created.\nThe student chooses the points on the page; "
                     "do not choose them.")
    if choice == "validate":
        candidate = body.get("candidate") or (lab.current()["selection"] or {}).get("candidate")
        require(candidate, "E_SCHEMA", "Select a candidate before validating on the simulator", 2)
        return {"candidate": candidate}, (f"Next step: validate {candidate} on the simulator.\nPlease run mlab validate --split repeat --candidate {candidate}, "
                                          f"and then run mlab validate --split holdout --candidate {candidate}; explain the results when the runs finish.")
    return {}, None  # The conclusion goes through its own form, after the student has seen the level it would get.


def points(lab, body):
    out = student.write_points(lab, body.get("points") or [])
    return out, (f"Hardware points saved from the form to {out['written'][0]}.\nThe reasons are the student's own words; do not change them. "
                 "Please run mlab measure check, then explain the simulator predictions and the budget the points use.")


def approve_points(lab, body):
    m = lab.measurement()
    require(m.get("checked_hash"), "E_NEEDS_APPROVAL", "Check the measurement points before confirming")
    out = lab.measure_approve(m["checked_hash"])
    return out, f"Hardware points of {m['id']} approved on the page ({out['hardware_runs']} runs).\nDo not run mlab measure approve again."


def unlock(lab, body):
    out = lab.measure_unlock() if body.get("target") == "measurement" else lab.unlock()
    return out, (f"{out.get('workflow') or out.get('measurement')} unlocked on the page after the errors were checked.\n"
                 "Start with mlab next, then try a different approach; do not repeat the same error.")


def conclude(lab, body):
    out = lab.conclude(body.get("candidate") or None)
    out["report"] = build_report(lab)["result_path"]
    return out, (f"Conclusion submitted on the page: {out['selection']}, {out['conclusion']}; report updated.\n"
                 f"Basis computed by mlab: {out['reason']}\n"
                 "Exploration can continue; a later submission replaces this one.")


def report(lab, body):
    return build_report(lab), None


def new_round(lab, body):
    source = body.get("source") or None
    out = lab.new_workflow(source=source)
    origin = f" with the candidates of {source}" if source else ""
    return out, f"{out['workflow']} created{origin}.\nWait for the student's hypothesis."


HANDLERS = {"hypothesis": hypothesis, "review": review, "decision": decision, "next_step": next_step, "points": points,
            "approve_points": approve_points, "unlock": unlock,
            "conclude": conclude, "report": report, "new_round": new_round}


def submit(root, kind, body):
    """(envelope, notice for the agent or None). With then=save the words are kept and the agent is not told."""
    handler = HANDLERS.get(kind)
    if not handler:
        return rejected(LabError("E_SCHEMA", f"unknown form {kind}", 2)), None
    if not isinstance(body, dict) or body.get("then", "notify") not in THEN:
        return rejected(LabError("E_SCHEMA", "then must be notify, confirm or save", 2)), None
    notes = []
    def action(lab):
        output, note = handler(lab, body)
        notes.append(note)
        return output
    result = run_action(root, action)
    note = notes[0] if notes and result["ok"] and body.get("then", "notify") != "save" else None
    if note:
        say = (result.get("next_actions") or [{}])[0].get("say")
        note = f"{NOTICE} {note}" + (f"\nNext (mlab next): {say}" if say else "")
    return result, note
