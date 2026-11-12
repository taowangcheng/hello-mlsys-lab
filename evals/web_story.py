"""A scripted student drives the course page against a real model and records what happened.

  python evals/web_story.py OUT --provider claude-haiku [--modes chat,forms,mixed] [--smoke 9.1,9.3]

For each 9.2 mode a fresh course with one 9.2 session runs `mlab web --agent sdk --simulator fixture` on a free port; the student speaks
only through the page's API: chat in chat mode, forms where a form exists in forms mode, and an alternation of the two
in mixed mode. Cards are approved unless mlab would refuse them; questions get the first
option. 9.1 and 9.3 get one round each. Results go to OUT/web-verification.json.
"""
import argparse
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mlab.web.course import Course  # noqa: E402

# The twelve lines of docs/demo.md, in the student's words.
LINES = {
    "hypothesis": "I think the p99 of short requests gets worse because long prefills block decode, but it could also just be overall "
                  "overload. If changing only the chunking clearly lowers p99, it's blocking; if only lowering concurrency helps, it's overload. "
                  "Please start by designing a controlled experiment with the three existing candidates and one seed.",
    "review1": "I've read the plan: keep all three candidates. baseline is the control, chunked_256 changes only the chunking, and low_concurrency "
               "only lowers concurrency, so the variables are properly controlled. No changes. Please record my comments, then approve and run.",
    "decide1": "Chunking improved only the median and p99 didn't change; the SLA was met only after lowering concurrency, so the results support "
               "overload and rule out blocking. My decision: keep low_concurrency and reject the chunking-only approach. Please write this into "
               "decision.md, confirm the decision, and select low_concurrency.",
    "revise": "Let's do another simulation round. New hypothesis: excessive concurrency is the main cause, but concurrency 8 may be too "
              "conservative; if concurrency 24 also meets the SLA, I'm wrong. Please create a new round with three candidates named c16, c20 and "
              "c24, with concurrency set to 16, 20 and 24 respectively, all other settings the same as low_concurrency, and one seed.",
    "review2": "The plan is fine: the three candidates differ only in concurrency. No changes. Please record my comments, then approve and run.",
    "decide2": "Concurrency 20 is the highest concurrency that meets the SLA, but in the simulation it is only 3% below the SLA. I'll choose c20 "
               "for now. Please write this into decision.md and confirm the decision.",
    "points1": "The simulated margin is too small, so I want to go to hardware. Please create a new measurement: measure c20 once, because I want "
               "to choose it but in the simulation it is only 3% below the SLA; and measure low_concurrency from round 1 once, as a "
               "low-concurrency control.",
    "approve1": "I confirm these two points, using 2 runs of the hardware budget in total. Please approve and submit the measurement.",
    "compare1": "The measurement results are in. Please help me compare the simulation with the measurement: where do they disagree, and what "
                "could be the cause?",
    "points2": "c20 violated the SLA on hardware, so I reject it and switch to c16, which has a larger margin. Please create a new measurement "
               "for final validation: measure c16 twice on the development workload and once on each of the two holdout workloads. The "
               "reason: c16 has to be validated on hardware, and repeating it once shows the noise.",
    "approve2": "I confirm these four runs, using 4 runs of the hardware budget in total. Please approve and submit the measurement.",
    "conclude": "All four hardware runs meet the constraints. Please submit the conclusion with r002/c16, then generate the report.",
    "report": "Please generate the report.",
}
CLAIM = "the p99 of short requests gets worse because long prefills block decode"
FALLBACK = "Please continue, as I said before."


def squash(text):
    """Text without whitespace, to compare a record with the student's words."""
    return "".join(str(text).split())


def verbatim(claim, words):
    """Every piece of the recorded text, between punctuation marks, appears in the student's words. The agent may split
    the student's message across headings, which changes a comma to a full stop, but must not change the words."""
    said = squash(" ".join(words))
    pieces = [piece for piece in re.split(r"[,.;!?:\n]+", str(claim)) if piece.strip()]
    return bool(pieces) and all(squash(piece) in said for piece in pieces)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Page:
    """The student's side of the page: the same API calls the browser makes."""

    def __init__(self, root, provider, task, opening=False, agent="sdk"):
        self.root, self.port = Path(root), free_port()
        course = Course(self.root)
        course.ensure("fixture", "standin", provider)
        self.sessions = {task: course.create(task)}  # The page would start it with "New session".
        command = [sys.executable, "-m", "mlab", "web", str(self.root), "--agent", agent, "--simulator", "fixture", "--port", str(self.port), "--no-browser"]
        if provider:
            command += ["--provider", provider]
        if not opening:
            command.append("--no-opening")
        self.server = subprocess.Popen(command, cwd=ROOT, stdout=open(self.root / "server.log", "w"), stderr=subprocess.STDOUT)
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=60)
        self.cards, self.events = [], []
        for _ in range(200):
            try:
                if self.http.get("/api/course").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        raise RuntimeError("mlab web did not start; see server.log")

    def get(self, task, path):
        return self.http.get(f"/api/sessions/{self.sessions[task]}/{path}").json()

    def post(self, task, path, **body):
        result = self.http.post(f"/api/sessions/{self.sessions[task]}/{path}", json=body).json()
        self.events.append({"at": time.time(), "post": path, "ok": result.get("ok"), "error": (result.get("error") or {}).get("message")})
        return result

    def settle(self, task, limit=900):
        """Wait until the agent is done, answering cards on the way. Returns the task state."""
        deadline = time.time() + limit
        while time.time() < deadline:
            state = self.get(task, "state")
            agent = state["agent"]
            for card in agent["cards"]:
                self.answer(task, card, state)
            if agent["status"] in {"idle", "offline", "error"} and not agent["queued"] and not agent["cards"]:
                time.sleep(1.5)  # Let the watcher catch up with runs that finished at the end of the turn.
                again = self.get(task, "state")
                if again["agent"]["status"] in {"idle", "offline", "error"} and not again["agent"]["cards"] and not again["agent"]["queued"]:
                    return again
            time.sleep(.5)
        raise TimeoutError(f"{task}: agent still busy after {limit} s")

    def answer(self, task, card, state):
        if card["kind"] == "question":
            first = {q["question"]: (q.get("options") or [{"label": "Continue"}])[0]["label"] for q in card.get("questions", [])}
            self.post(task, f"questions/{card['id']}", answers=first)
            self.cards.append({"kind": "question", "answers": first, "step": state.get("step")})
            return
        summary = card.get("summary") or {}
        if summary.get("will_fail"):
            decision, reason = "deny", f"mlab would refuse this: {summary['will_fail']}. Fix it first."
        else:
            decision, reason = "allow", ""
        self.post(task, f"approvals/{card['id']}", decision=decision, reason=reason)
        self.cards.append({"kind": summary.get("kind"), "decision": decision, "command": card.get("command"), "step": state.get("step"),
                           "will_fail": summary.get("will_fail")})

    def say(self, task, text):
        return self.post(task, "chat", text=text)

    def close(self):
        self.server.terminate()
        try:
            self.server.wait(15)
        except subprocess.TimeoutExpired:
            self.server.kill()


def choose(state, sent):
    """The terminal demo's student: the next line for the current state, or None."""
    stage, mstage = state.get("stage"), state.get("measurement_stage")
    iteration = (state.get("context") or {}).get("iteration", 1)
    measurement = state.get("measurement") or ""
    if mstage == "measure_plan":
        key = "points1" if measurement == "m001" else "points2"
        if key not in sent:
            return key
        return ("approve1" if measurement == "m001" else "approve2") if state.get("step") == "points_approve" else None
    if mstage in {"measure_approved", "measuring", "measure_retry"}:
        return None
    if mstage == "measured" and measurement == "m001" and "compare1" not in sent:
        return "compare1"
    if stage in {"new_workflow", "hypothesis"}:
        if iteration == 1:
            return "hypothesis" if "hypothesis" not in sent else None
        return "revise" if "revise" not in sent else None  # The agent may open the second round itself.
    if stage == "plan":
        key = "review1" if iteration == 1 else "review2"
        return key if state.get("step") == "plan_review" and key not in sent else None
    if stage == "analysis_and_decision":
        key = "decide1" if iteration == 1 else "decide2"
        return key if key not in sent and state.get("step") == "decision" else None
    if stage == "decided":
        if iteration == 1:
            return "revise" if "revise" not in sent else None
        if "points1" not in sent:
            return "points1"
        if "points2" not in sent:
            return "points2"
        if state.get("conclusion"):
            return "report" if "report" not in sent else None
        return "conclude" if "conclude" not in sent else None
    return None


def runs_of(page, task, record, candidate):
    return [run["id"] for run in page.get(task, f"records/{record}")["runs"] if run["candidate"] == candidate]


def act(page, task, path, **body):
    """Submit a form, then let the agent finish the turn the notice starts; the page refuses forms while it works."""
    result = page.post(task, path, **body)
    page.settle(task)
    return result


# Forms for each student step, as the forms and mixed modes use them. Each returns True when it acted.
def form_step(page, task, state, sent, key):
    step = state.get("step")
    if key == "hypothesis":
        page.post(task, "forms/hypothesis", claim=CLAIM + ", since short requests wait behind long ones in a burst", alternative="overall overload",
                  falsifier="If changing only the chunking clearly lowers p99, it's blocking; if only lowering concurrency helps, it's overload")
        page.say(task, "Please start by designing a controlled experiment with the three existing candidates and one seed.")
    elif key in {"review1", "review2"} and step == "plan_review":
        round_ = page.get(task, f"records/{state['workflow']}")
        page.post(task, "forms/review", entries=[{"candidate": c["id"], "verdict": "keep", "reason": "the variables are properly controlled"} for c in round_["candidates"]], then="confirm")
    elif key in {"decide1", "decide2"} and step == "decision":
        candidate = "low_concurrency" if key == "decide1" else "c20"
        cited = runs_of(page, task, state["workflow"], candidate)
        text = (f"Based on {' '.join(cited)}: the SLA is met only after lowering concurrency, which supports overload and rules out blocking. "
                "Keep low_concurrency." if key == "decide1" else
                f"Based on {' '.join(cited)}: concurrency 20 is the highest that meets the SLA, but the margin is only 3%; c20 for now.")
        page.post(task, "forms/decision", text=text, candidate=candidate, then="confirm")
    elif key == "revise":
        if state.get("stage") == "decided":
            act(page, task, "forms/next_step", choice="round", detail="look for margin near low concurrency")
        page.post(task, "forms/hypothesis", claim="excessive concurrency is the main cause, but concurrency 8 may be too conservative", alternative="",
                  falsifier="if concurrency 24 also meets the SLA, I'm wrong")
        page.say(task, "Name this round's three candidates c16, c20 and c24, with concurrency 16, 20 and 24 respectively, all other settings "
                       "the same as low_concurrency, and one seed.")
    elif key in {"points1", "points2"}:
        if state.get("stage") == "decided" and state.get("step") == "next_step":
            act(page, task, "forms/next_step", choice="measure")
        points = ([{"candidate": "c20", "rationale": "I want to choose it, but in the simulation it is only 3% below the SLA"},
                   {"candidate": "low_concurrency", "rationale": "a low-concurrency control"}] if key == "points1" else
                  [{"candidate": "c16", "runs": 2, "rationale": "c16 has a larger margin and has to be validated on hardware; repeating once shows the noise"},
                   {"candidate": "c16", "workload": "short_prompt_distribution", "rationale": "holdout: more short requests"},
                   {"candidate": "c16", "workload": "long_prompt_distribution", "rationale": "holdout: more long requests"}])
        page.post(task, "forms/points", points=points)
    elif key in {"approve1", "approve2"}:
        page.post(task, "forms/approve_points")
    elif key == "compare1":
        # The measurement page's button: it puts the reference and a question in the chat box, and the student sends it.
        page.say(task, f"@{state['measurement']} Do the hardware results match the simulation? If not, what might explain the gap?")
    elif key == "conclude":
        page.post(task, "forms/conclude", candidate="r002/c16")
    else:
        return False
    return True


MIXED_FORMS = {"review1", "decide2", "points1", "approve2", "compare1", "conclude"}


def story(page, mode, limit):
    task, sent, stuck = "9.2", set(), {}
    deadline = time.time() + limit
    state = page.settle(task)
    while time.time() < deadline:
        if state.get("conclusion") and ((page.root / page.sessions[task] / "report.md").exists() or {"conclude", "report"} & sent):
            break
        key = choose(state, sent)
        slot = (state.get("stage"), state.get("measurement_stage"), state.get("measurement"), (state.get("context") or {}).get("iteration"), key)
        stuck[slot] = stuck.get(slot, 0) + 1
        if stuck[slot] > 4:
            page.events.append({"gave_up": str(slot)})
            break
        use_form = mode == "forms" or (mode == "mixed" and key in MIXED_FORMS)
        if key and key not in sent:
            if not (use_form and form_step(page, task, state, sent, key)):
                page.say(task, LINES[key])
            sent.add(key)
        else:
            page.say(task, FALLBACK)
        state = page.settle(task)
    return state


def smoke(page, task):
    """One round through forms: the agent drafts and checks a plan, runs it and analyses it."""
    page.post(task, "forms/hypothesis", claim="at least one of the existing candidates meets the task's constraints", alternative="",
              falsifier="if all three candidates violate the constraints, I'm wrong")
    state = page.settle(task)
    if state.get("step") == "plan_review":
        round_ = page.get(task, f"records/{state['workflow']}")
        page.post(task, "forms/review", entries=[{"candidate": c["id"], "verdict": "keep", "reason": "start from the baseline"} for c in round_["candidates"]], then="confirm")
        state = page.settle(task)
    if state.get("step") == "decision":
        runs = page.get(task, f"records/{state['workflow']}")["runs"]
        good = [run for run in runs if run.get("verdict") == "constraints_met"] or runs
        page.post(task, "forms/decision", text=f"Based on {good[0]['id']}, keep {good[0]['candidate']} for now.", candidate=good[0]["candidate"], then="confirm")
        state = page.settle(task)
    return state


def verify(page, task, state, mode=None):
    folder = page.root / page.sessions[task]
    ledger = [json.loads(line) for line in (folder / ".mlab/ledger.jsonl").read_text(encoding="utf-8").splitlines()] if (folder / ".mlab/ledger.jsonl").exists() else []
    errors = [event for event in ledger if event["type"].endswith("_error")]
    words = [json.loads(line) for line in (folder / ".mlab/student.jsonl").read_text(encoding="utf-8").splitlines()] if (folder / ".mlab/student.jsonl").exists() else []
    empty = []
    notebook = page.get(task, "notebook")
    ids = ["overview", "report"] + [item["id"] for item in notebook["items"] if item["kind"] != "conclusion"]
    ids += [config["id"] for config in notebook["configs"]] + [run["id"] for item in notebook["items"] for run in item.get("runs", [])]
    for identifier in ids:
        record = page.get(task, f"records/{identifier}")
        if not record.get("ok") or len(record) <= 3:
            empty.append(identifier)
    hypothesis = folder / "rounds/r001/hypothesis.md"
    claim = ""
    if hypothesis.exists():
        sections = hypothesis.read_text(encoding="utf-8").split("## ")
        claim = next((part.split("\n", 1)[1].strip() for part in sections if part.startswith("Hypothesis")), "")
    copied = verbatim(claim, [w["text"] for w in words])
    chat = [json.loads(line) for line in (folder / ".mlab/web/chat.jsonl").read_text(encoding="utf-8").splitlines()] if (folder / ".mlab/web/chat.jsonl").exists() else []
    agent = page.get(task, "state")["agent"]
    return {"task": task, "mode": mode, "stage": state.get("stage"), "conclusion": state.get("conclusion"), "selected": state.get("selected_candidate"),
            "selection": state.get("selection"), "hardware_budget": (state.get("budget") or {}).get("hardware"),
            "align_commands": sum(e.get("role") == "tool" and "mlab align" in str(e.get("text")) for e in chat),
            "rule_errors": [{"type": e["type"], **e["data"]} for e in errors], "page_notes_in_student_log": sum("[Page " in w["text"] for w in words),
            "student_sources": {source: sum(w["source"] == source for w in words) for source in sorted({w["source"] for w in words})},
            "hypothesis_claim": claim, "hypothesis_claim_is_students_words": copied,
            "empty_records": empty, "usage": agent["usage"], "workspace": str(folder)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--provider", default="claude-haiku")
    parser.add_argument("--modes", default="chat,forms,mixed")
    parser.add_argument("--smoke", default="9.1,9.3")
    parser.add_argument("--limit", type=int, default=2400, help="Seconds per 9.2 story")
    parser.add_argument("--agent", default="sdk", choices=["sdk", "fake"], help="fake: check this script without a model (forms mode only)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"provider": args.provider, "agent": args.agent, "runs": []}
    for mode in filter(None, args.modes.split(",")):
        page = Page(args.out / f"9.2-{mode}", args.provider, "9.2", agent=args.agent)
        started = time.time()
        try:
            state = story(page, mode, args.limit)
            result = verify(page, "9.2", state, mode)
        except Exception as error:  # noqa: BLE001 - one failed mode should not hide the others
            result = {"task": "9.2", "mode": mode, "error": f"{type(error).__name__}: {error}"}
        finally:
            page.close()
        result.update(seconds=round(time.time() - started), cards=page.cards, events=page.events[-40:])
        report["runs"].append(result)
        (args.out / "web-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: result.get(k) for k in ("mode", "conclusion", "selection", "hardware_budget", "align_commands", "rule_errors", "seconds", "error")}, ensure_ascii=False))
    for task in filter(None, args.smoke.split(",")):
        page = Page(args.out / f"smoke-{task}", args.provider, task, opening=True, agent=args.agent)
        started = time.time()
        try:
            page.post(task, "agent/open")
            page.settle(task)
            state = smoke(page, task)
            result = verify(page, task, state, "smoke")
        except Exception as error:  # noqa: BLE001
            result = {"task": task, "mode": "smoke", "error": f"{type(error).__name__}: {error}"}
        finally:
            page.close()
        result.update(seconds=round(time.time() - started), cards=page.cards, events=page.events[-20:])
        report["runs"].append(result)
        (args.out / "web-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: result.get(k) for k in ("task", "stage", "rule_errors", "seconds", "error")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
