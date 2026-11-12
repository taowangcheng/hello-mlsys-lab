"""A rule-following stand-in for the model, for tests and offline walkthroughs (`mlab web --agent fake`).

It does the agent's steps the way AGENTS.md asks: it runs the real mlab CLI, edits only the agent's files, and asks
for the student's confirmation through the page for every gated command. It never writes the student's words: the
hypothesis, comments, decisions and points come from the forms or the chat. Asked to compare the simulator with the
hardware, it runs mlab align and lists the pairs without judging them.
"""
import asyncio
import json
import os
import re
import shlex
import sys
import uuid
from ..core.io import ROOT, load, tool_path
from ..core.metrics import get_metric
from ..core.workspace import TASKS, Lab, explicit_candidate
from . import approvals, i18n, labels

NOTICE = "[Page notice]"
NOTE = "[Page note]"
MAX_STEPS = 14
# Page notices ask for commands with "Please run …"; a test may send "Run: <command>" on a line of its own.
COMMANDS = re.compile(r"\b(?:[Pp]lease run|and then run) (mlab [^,;\n]+?)(?=[,;]|\.(?:\s|$)|\n|$)")
EXPLICIT = re.compile(r"^Run: (.+)$", re.M)
APPROVING = ("approve", "confirm", "agree", "keep", "go ahead")
COMPARING = ("compare", "match", "gap")
HINTS = dict(labels.HINTS)


class FakeAgentClient:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = runtime.root
        self.session = None
        self.stopped = False
        self.process = None
        self.last = {}
        self.lang = "en"
        self.env = {**os.environ, "PATH": tool_path(), "PYTHONUTF8": "1", "MLAB_PROMPT_SOURCE": "web"}

    def tr(self, template, /, **params):
        """The fake agent replies in the page's language, like the real one."""
        return i18n.translator(self.lang)(template, **params)

    async def connect(self, session):
        self.session = session or f"fake-{uuid.uuid4().hex[:12]}"
        return self.session

    async def disconnect(self):
        self.stopped = True

    async def interrupt(self):
        self.stopped = True
        if self.process and self.process.returncode is None:
            self.process.terminate()  # Only waits are long; they are safe to stop.

    async def turn(self, prompt):
        self.stopped = False
        self.lang = getattr(self.runtime, "lang", None) or "en"
        if getattr(self.runtime, "exploration", False):
            yield {"type": "text", "text": self.tr("This is the offline preview. You can edit experiments and run them directly from the workbench; connect a model for evidence-based discussion.")}
            yield {"type": "result", "session": self.session, "usage": {"input_tokens": 0, "output_tokens": 0}}
            return
        notice = prompt.startswith(NOTICE)
        if notice and "opened this session's page" in prompt:
            state = await self.state()
            yield self.say(self.tr("{step}. {hint}", step=self.tr(labels.STEPS.get(state["step"], state["step"] or "")),
                                   hint=self.tr(HINTS.get(state["step"], "I'll continue."))))
            yield self.result()
            return
        explicit = EXPLICIT.findall(prompt) if not notice else []
        commands = explicit or (COMMANDS.findall(prompt) if notice else [])
        for command in commands:
            events, ok = await self.run(command.strip())
            for event in events:
                yield event
            if not ok or self.stopped:
                yield self.result()
                return
        if explicit:
            yield self.result()
            return
        said = prompt.split(f"\n\n{NOTE}")[0]  # The student's own words, without what the page appended.
        meaning = i18n.english(said).lower()  # A quick reply or question template from a Chinese page, in English.
        if not notice and any(word in meaning for word in COMPARING):
            async for event in self.compare(said):
                yield event
            yield self.result()
            return
        intent = not notice and any(word in meaning for word in APPROVING)
        for _ in range(MAX_STEPS):
            if self.stopped:
                yield self.say(self.tr("OK, stopping here."))
                break
            state = await self.state()
            step = state["step"]
            ended = False
            async for event in self.step(step, state, intent):
                if event["type"] == "end":
                    ended = True
                    continue
                yield event
            intent = False  # One approval per message.
            if ended:
                break
        yield self.result()

    # Helpers.

    def say(self, text):
        return {"type": "text", "text": text}

    def result(self):
        return {"type": "result", "usage": {"input_tokens": 0, "output_tokens": 0}, "cost_usd": 0, "session": self.session}

    async def mlab(self, *args):
        self.process = await asyncio.create_subprocess_exec(sys.executable, "-m", "mlab", *args, cwd=self.root, env=self.env,
                                                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await self.process.communicate()
        text = out.decode("utf-8", "replace")
        try:
            data = json.loads(text)
        except ValueError:
            data = {"ok": False, "error": {"message": (err.decode("utf-8", "replace") or text)[-300:]}}
        if str(data.get("message", "")).startswith("Detailed output saved"):
            data = load(data["result_path"])
        return text, data

    async def state(self):
        _, data = await self.mlab("next")
        step = (data.get("next_actions") or [{}])[0].get("step")
        return {**data, "step": step}

    async def bash(self, command):
        """Run a command as the agent's Bash would: plain mlab commands directly, gated ones after the student approves."""
        found = approvals.classify(command)
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = []
        direct = not found["kind"] and tokens[:1] == ["mlab"] and not approvals.OPERATORS & set(tokens)
        identifier = uuid.uuid4().hex[:8]
        yield {"type": "tool", "id": identifier, "name": "Bash", "input": {"command": command}}
        if not direct:
            decision = await self.runtime.permission("Bash", {"command": command})
            if not decision["allow"]:
                yield {"type": "tool_result", "id": identifier, "text": decision["message"], "is_error": True}
                yield self.say(self.tr("OK, skipping this step. {message}", message=i18n.translate(self.lang, decision["message"])))
                yield {"type": "end"}
                return
            tokens = shlex.split(command)
        text, data = await self.mlab(*tokens[1:])
        yield {"type": "tool_result", "id": identifier, "text": text[:600], "is_error": not data.get("ok")}
        if not data.get("ok"):
            message = (data.get("error") or {}).get("message", "command failed")
            yield self.say(self.tr("{command} failed: {message}", command=command, message=i18n.translate(self.lang, message)))
            yield {"type": "end"}
            return
        self.last = data

    async def run(self, command):
        """bash() for use inside a step: (events, ok)."""
        events, ok = [], True
        async for event in self.bash(command):
            if event["type"] == "end":
                ok = False
            else:
                events.append(event)
        return events, ok

    def lab(self):
        return Lab(self.root, readonly=True)

    # One step of the workflow; yields events and {"type": "end"} when the turn should stop.

    async def step(self, step, state, intent):
        if step in HINTS and step not in {"points", "plan_review", "decision", "points_approve", "locked", "measure_locked"}:
            yield self.say(self.tr(HINTS[step]))
            yield {"type": "end"}
            return
        # File work runs in a thread, so the page's requests are never held up by the agent.
        gated = await asyncio.to_thread(self.proposal, step, state) if intent else None
        if step in {"plan_review", "decision", "points_approve", "locked", "measure_locked"}:
            if not gated:
                yield self.say(self.tr(HINTS[step]))
                yield {"type": "end"}
                return
            commands = [gated]
        elif step == "points":
            if not await asyncio.to_thread(self.points_written):
                yield self.say(self.tr(HINTS["points"]))
                yield {"type": "end"}
                return
            commands = ["mlab measure check"]
        elif step == "plan_draft":
            await asyncio.to_thread(self.draft_plan, state)
            yield {"type": "tool", "id": "edit", "name": "Edit", "input": {"file_path": state["context"]["hypothesis_path"].replace("hypothesis.md", "plan.yaml")}}
            commands = ["mlab workflow check"]
        elif step == "analysis_draft":
            commands = ["mlab compare"]
        else:
            commands = {"new_round": ["mlab workflow new"], "submit": ["mlab workflow submit", "mlab workflow wait --max 60"],
                        "running": ["mlab workflow wait --max 60"], "retry_backend": ["mlab workflow submit"],
                        "validation_retry": re.findall(r"mlab validate --split \w+ --candidate [\w-]+", state["next_actions"][0]["say"]),
                        "points_predicting": ["mlab measure wait --max 60", "mlab measure check"],
                        "measure_submit": ["mlab measure submit", "mlab measure wait --max 60"], "measure_retry": ["mlab measure submit"]}.get(step)
            if commands is None:
                yield self.say(self.tr("Unknown step {step}; see the middle column.", step=step))
                yield {"type": "end"}
                return
        for command in commands:
            events, ok = await self.run(command)
            for event in events:
                yield event
            if not ok:
                yield {"type": "end"}
                return
            if " wait " in command and self.last.get("status") != "completed":
                yield self.say(self.tr("The runs have not finished; I will continue when they do."))
                yield {"type": "end"}
                return
        if step == "analysis_draft":
            await asyncio.to_thread(self.write_analysis)
            yield self.say(self.tr("Analysis draft written; every number cites a run."))

    async def compare(self, said):
        """mlab align for the measurement the student cites (else the current one), read back as plain lines; the reasons
        and what to do next are the student's."""
        cited = re.findall(r"@(m\d{3})\b", said)
        events, ok = await self.run("mlab align" + (f" {cited[0]}" if cited else ""))
        for event in events:
            yield event
        if not ok:
            return
        tr = self.tr
        shifts = {"all": tr("all requests shifted"), "tail": tr("only the slowest requests shifted")}
        lines = []
        for row in self.last.get("rows", []):
            gap = f"{row['gap_pct']:+.1f}%" if row["gap_pct"] is not None else "-"
            marks = [tr("beyond noise ({shift})", shift=shifts.get(row["shift"], row["shift"])) if row["beyond_noise"] else tr("within noise"),
                     tr("verdict flipped") if row["verdict_flip"] else "",
                     tr("effective configuration differs: {diff}", diff="; ".join(row["config_diff"])) if row.get("config_diff") else ""]
            lines.append(tr("- {run} ({candidate}): simulated {sim}, hardware {hw}, gap {gap}; {marks}", run=row["run"], candidate=row["candidate"],
                            sim=row["sim"], hw=row["hw"], gap=gap, marks="; ".join(filter(None, marks))))
        yield self.say(tr("Simulation vs. hardware:") + "\n" + "\n".join(lines) + "\n" +
                       tr("This preview lists the numbers. Consider what they mean and what to try next."))

    def proposal(self, step, state):
        say = state["next_actions"][0]["say"]
        if step == "plan_review":
            found = re.search(r"mlab workflow approve --plan (sha256:[0-9a-f]+)", say)
            return found and f"mlab workflow approve --plan {found.group(1)}"
        if step == "points_approve":
            found = re.search(r"mlab measure approve --plan (sha256:[0-9a-f]+)", say)
            return found and f"mlab measure approve --plan {found.group(1)}"
        if step == "decision":
            candidate = self.decided_candidate()
            return "mlab workflow decide" + (f" --candidate {candidate}" if candidate else "")
        return {"locked": "mlab workflow unlock", "measure_locked": "mlab measure unlock"}.get(step)

    # The agent's own file edits.

    def points_written(self):
        lab = self.lab()
        try:
            m = lab.measurement(required=False)
            return bool(m and (load(lab.mfolder(m) / "points.yaml") or {}).get("points"))
        finally:
            lab.close()

    def decided_candidate(self):
        lab = self.lab()
        try:
            w = lab.workflow()
            text = (lab.folder(w) / "decision.md").read_text(encoding="utf-8")
            names = [c["id"] for c in load(lab.folder(w) / "frozen_plan.json")["candidates"]]
            return next((name for name in sorted(names, key=len, reverse=True) if name in text), None)
        finally:
            lab.close()

    def draft_plan(self, state):
        """Question and requirements from the task's example plan; after a decided round, a sweep around its selection."""
        lab = self.lab()
        try:
            w = lab.workflow()
            folder = lab.folder(w)
            plan = load(folder / "plan.yaml")
            example = load(ROOT / "examples" / TASKS[lab.task["id"]] / "plan.yaml")
            if not str(plan.get("question", "")).strip():
                plan.update(question=example["question"], derived_requirements=example["derived_requirements"])
            earlier = [x for x in lab.store.all("workflow") if x["status"] == "decided"]
            if earlier and not w.get("source") and earlier[-1].get("selected_candidate"):
                frozen = [explicit_candidate(c) for c in load(lab.folder(earlier[-1]) / "frozen_plan.json")["candidates"]]
                base = next(c for c in frozen if c["id"] == earlier[-1]["selected_candidate"])
                rule = lab.task["decision_space"].get("serving.max_concurrency")
                value = base["strategy"]["serving"]["max_concurrency"]
                targets = list(dict.fromkeys(round(value * f) for f in (2, 2.5, 3)))
                valid = [t for t in targets if rule and (t in rule.get("values", []) if rule["type"] == "enum" else rule["min"] <= t <= rule["max"])]
                chosen = []
                if len(valid) >= 2:
                    for target in valid:
                        candidate = json.loads(json.dumps(base))
                        candidate.update(id=f"c{target}", rationale=f"{base['id']} with concurrency {target} only, to see how the margin changes with concurrency")
                        candidate["strategy"]["serving"]["max_concurrency"] = target
                        chosen.append(candidate)
                else:
                    chosen = frozen
                for candidate in chosen:
                    (folder / "candidates" / f"{candidate['id']}.yaml").write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
                plan["candidates"] = [f"candidates/{c['id']}.yaml" for c in chosen]
            (folder / "plan.yaml").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        finally:
            lab.close()

    def write_analysis(self):
        lab = self.lab()
        try:
            w = lab.workflow()
            metric = lab.task["key_metrics"][0]
            lines, claims = [f"## Results ({metric})", ""], []
            for run_id in w["runs"]:
                run = lab.store.get("run", run_id)
                if run["split"] != "development" or not run.get("result_hash"):
                    continue
                result, _ = lab.result(run_id)
                value = get_metric(result["metrics"], metric)
                shown = f"{value:.1f}" if type(value) in {int, float} else "no value"
                lines.append(f"- {run_id} ({run['candidate']}): {metric} = {shown}, {result['verdict']}")
                if type(value) in {int, float}:
                    claims.append({"run_id": run_id, "metric": metric, "value": value})
            lines += ["", "## Against the falsification condition", "",
                      "The example agent only lists the numbers and does not judge them; compare them with the falsification condition in the hypothesis.", ""]
            (lab.folder(w) / "analysis.md").write_text("\n".join(lines), encoding="utf-8")
            (lab.folder(w) / "analysis.claims.json").write_text(json.dumps(claims, ensure_ascii=False), encoding="utf-8")
        finally:
            lab.close()
