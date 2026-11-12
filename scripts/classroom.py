"""Student/Claude Code teaching loop. Student-only actions run in this controller.

Unlike the smoke demo, Claude edits the plan and calls individual mlab commands.
The controller never approves, decides, unlocks or closes without a student answer.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import uuid

from qwen_demo import ROOT, child_environment, connect_claude
from mlab.core.io import LabError, atomic, hashed, load, require, require_pinned_claude
from mlab.core.workspace import RUN_ID, Lab, initialize
from mlab.core.reporting import build_report
from mlab.core.evidence import check_claims


class ClaudeTeacher:
    def __init__(self, gateway, key_env, workspace):
        self.gateway, self.key_env, self.root = gateway, key_env, Path(workspace).resolve()
        self.claude = require_pinned_claude()
        self.runtime = self.root / ".mlab/classroom"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.server, url, token = connect_claude(gateway)
        self.env = child_environment(gateway.model, url, token, key_env, self.runtime / "claude-home", gateway.auth_mode)
        self.env["MLAB_BACKEND_IDENTITY"] = hashed({"upstream": gateway.url, "model": gateway.model})
        self.env["MLAB_LLM_TIMEOUT_S"] = str(gateway.timeout + 15)
        self.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(gateway.max_tokens)
        # Isolated, explicitly selected student workspace. No global trust changes.
        home = self.runtime / "claude-home"
        config_path = home / ".claude.json"
        config = load(config_path) if config_path.exists() else {}
        # Claude keys trust by normalized repository root on recent versions;
        # the selected workspace can be a nested directory in this repository.
        for project in (str(self.root), str(ROOT)):
            config.setdefault("projects", {}).setdefault(project, {})["hasTrustDialogAccepted"] = True
        atomic(config_path, config)
        template = load(self.root / ".claude/settings.json")
        rules = template["permissions"]
        for pattern in ("/rounds/**/plan.yaml", "/rounds/**/candidates/**", "/rounds/**/analysis.md", "/rounds/**/analysis.claims.json"):
            rules["allow"] += ["Edit(" + pattern + ")"]
        # In headless turns the Python student UI owns confirmations, as in Plan
        # section 12. The model cannot perform these commands on the student's behalf.
        rules["deny"] += rules.pop("ask", []) + ["Bash(mlab demo *)", "Bash(mlab init *)",
            "Bash(mlab workflow new*)", "Read(//**/config/qwen.local.json)",
            "Read(./.mlab/classroom/**)", "Read(**/logs.txt)"]
        rules["deny"] += ["Bash(mlab *workflow " + action + "*)" for action in ("approve", "decide", "unlock")]
        rules["deny"] += ["Bash(mlab *measure " + action + "*)" for action in ("approve", "unlock")]
        rules["deny"] += ["Bash(mlab *conclude*)"]
        for pattern in ("/task/**", "**/runs/**", "**/state.yaml", "**/approval.json", "/.mlab/**", "**/frozen_plan.json", "**/decision.confirmed.json", "**/review.md"):
            rules["deny"].append("Edit(" + pattern + ")")
        # Edit rules cover Write too; Write(path) rules do not match in Claude.
        # A leading / in --settings anchors at that file's directory, so use
        # cwd-relative paths for the copied template instead of .mlab/classroom.
        for kind, values in rules.items():
            rules[kind] = list(dict.fromkeys(re.sub(r"^(Read|Edit)\(/(?!/)", r"\1(./", value) for value in values))
        template.get("hooks", {}).pop("UserPromptSubmit", None)
        template["env"] = {k: v for k, v in self.env.items() if (k.startswith("ANTHROPIC_") and k not in {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"}) or k == "MAX_THINKING_TOKENS"}
        atomic(self.runtime / "settings.json", template)
        atomic(self.runtime / "mcp.json", {"mcpServers": {}})

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def turn(self, phase, prompt):
        session_path = self.runtime / "session.json"
        old = load(session_path) if session_path.exists() else None
        identifier = old["session_id"] if old else str(uuid.uuid4())
        args = [self.claude, "-p", "--model", self.gateway.model, "--tools", "Bash,Read,Edit,Write",
                "--system-prompt", "You are the hello-mlsys teaching assistant running inside Claude Code. Follow the workspace AGENTS.md and the current classroom phase. Use Chinese. Only work on this frozen task and current iteration. Read files before editing. Use mlab for all experiment operations. Never inspect git, environment variables, unrelated repository files, or install anything. Student approvals, comments and decisions belong to the terminal controller. Stop when the requested phase is complete. Never claim real performance from mock numbers.",
                "--settings", str(self.runtime / "settings.json"), "--setting-sources", "project,local",
                "--strict-mcp-config", "--mcp-config", str(self.runtime / "mcp.json"),
                "--output-format", "stream-json", "--verbose", "--max-turns", "18",
                "--resume" if old else "--session-id", identifier]
        prefix = self.runtime / (phase + "-" + uuid.uuid4().hex[:8])
        final = None
        with prefix.with_suffix(".jsonl").open("w", encoding="utf-8") as log, prefix.with_suffix(".stderr.txt").open("w", encoding="utf-8") as err:
            with subprocess.Popen(args, cwd=self.root, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=err, text=True, encoding="utf-8", errors="replace") as process:
                try:
                    process.stdin.write("CLI classroom phase: " + phase + "\nUse Chinese for student-facing explanations. " + prompt)
                    process.stdin.close()
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("type") == "assistant":
                            for block in event.get("message", {}).get("content", []):
                                if block.get("type") == "text":
                                    print(block["text"], flush=True)
                                elif block.get("type") == "tool_use":
                                    print("[tool] " + block["name"], flush=True)
                        if event.get("type") == "result":
                            final = event
                    code = process.wait()
                except BaseException:
                    process.terminate()
                    raise
        require(not code and final and not final.get("is_error") and final.get("subtype") == "success",
                "E_BACKEND_UNAVAILABLE", "Agent turn did not finish; see .mlab/classroom logs and resume this workspace.", 4)
        atomic(session_path, {"session_id": identifier, "model": self.gateway.model, "last_phase": phase})


class Student:
    """The student's hypothesis, comments and decisions, in natural language."""
    scripted = False
    def text(self, label):
        answer = input(label + "\n> ").strip()
        if answer.lower() in {"/quit", "/exit"}:
            raise EOFError
        require(answer, "E_SCHEMA", "Empty answer; restart this workspace to continue.", 2)
        return answer
    def confirm(self, label):
        return input(label + " [y/N] ").strip().lower() in {"y", "yes"}
    def hypothesis(self, lab):
        claim = self.text("State this round's hypothesis and one alternative explanation (/quit saves and exits).")
        falsifier = self.text("What result would prove your hypothesis wrong?")
        return f"## Hypothesis\n\n{claim}\n\n## What result would prove me wrong\n\n{falsifier}\n"
    def review(self, lab):
        return self.text("Comment on the plan above: keep, change or drop each candidate, and why? Say 'agree' if you have no comments.")
    def decision(self, lab):
        while True:
            answer = self.text("Which results changed or supported your hypothesis? What do you keep, revise or reject? Cite run IDs.")
            if RUN_ID.search(answer):
                return answer
            print("Cite at least one run ID, e.g. sim_0001.")
    def candidate(self, candidates, lab):
        answer = self.text("Select a candidate (number or name): " + ", ".join(f"{i + 1}. {name}" for i, name in enumerate(candidates)))
        if answer.isdigit() and 1 <= int(answer) <= len(candidates):
            return candidates[int(answer) - 1]
        matches = [name for name in candidates if name in answer]
        return matches[0] if len(matches) == 1 else answer
    def action(self, lab):
        answer = self.text("Next: revise, validate or close (submit a conclusion), or /quit to save and exit.")
        return {"conclude": "close"}.get(answer, answer)


class ScriptedStudent(Student):
    """Explicitly labelled scripted student, for integration acceptance only."""
    scripted = True
    def __init__(self, rounds=2):
        self.rounds = rounds
    def confirm(self, label):
        print("[scripted student confirms] " + label)
        return True
    def hypothesis(self, lab):
        claim = ("Scripted student: concurrency and long requests cause the tail latency; the alternative explanation is precision overhead."
                 if lab.workflow()["iteration"] == 1 else
                 "Scripted student, revised hypothesis: the last round cannot attribute the change to chunking; this round lowers "
                 "concurrency further to test overall overload.")
        return f"## Hypothesis\n\n{claim}\n\n## What result would prove me wrong\n\nScripted student: if p99 stays the same after lowering concurrency, it is not overload.\n"
    def review(self, lab):
        return "Scripted student: agree with the plan, no changes."
    def decision(self, lab):
        ids = [r["id"] for r in lab.store.all("run") if r["workflow"] == lab.workflow()["id"] and r["split"] == "development"]
        return "Scripted student: based on " + ", ".join(ids) + ", revise the overload explanation; the evidence is mock only, so continue with controls and validation."
    def candidate(self, candidates, lab):
        from mlab.core.metrics import get_metric
        runs = [r for r in lab.store.all("run") if r["workflow"] == lab.workflow()["id"] and r["split"] == "development"]
        feasible = [r for r in runs if r.get("verdict") == "constraints_met"] or runs
        metric = lab.task["key_metrics"][0]
        # Explicit acceptance policy, not an inferred student preference.
        return min(feasible, key=lambda r: get_metric(lab.result(r["id"])[0]["metrics"], metric) or float("inf"))["candidate"]
    def action(self, lab):
        return "revise" if lab.workflow()["iteration"] < self.rounds else "validate"


def finish(lab):
    lab.conclude()
    report = build_report(lab)
    print("[verified final result] " + json.dumps(report, ensure_ascii=False))
    return report


def classroom_loop(workspace, agent, student):
    lab = Lab(workspace)
    try:
        print("Classroom workspace: " + str(lab.root) + "\nType /quit to save and exit; give the same workspace again to resume.")
        while True:
            state = lab.next()
            print("[stage] " + state["stage"], flush=True)
            if state["stage"] == "locked":
                print(json.dumps(lab.workflow()["errors"], ensure_ascii=False))
                if not student.confirm("You have seen the error record. Unlock?"):
                    return
                lab.unlock()
                continue
            if state["stage"] == "new_workflow":
                lab.new_workflow()
                continue
            if state["stage"] == "hypothesis":
                atomic(lab.folder() / "hypothesis.md", student.hypothesis(lab), text=True)
                continue
            if state["stage"] == "plan":
                agent.turn("plan", "Read mlab next, task/task.yaml, hypothesis.md, plan.yaml and candidate files. "
                    "Use the existing task and example candidate formats. Fill question and derived_requirements with justified constraints; "
                    "edit declarative candidates to test the student's hypothesis. For a revised iteration use the previous decision and change a meaningful strategy dimension. "
                    "Do not change student intent, task, SLA, approval or comments. Keep at least three candidates. "
                    "Run mlab workflow check; fix schema errors at most twice. Explain the plan, then STOP for the student's comments. "
                    "Do not approve, decide, unlock, close, submit or use mlab demo.")
                checked = lab.check()
                _, candidates, _ = lab.specifications()
                print("[plan to approve] " + json.dumps({**checked, "candidates": candidates}, ensure_ascii=False))
                comments = student.review(lab)
                if not student.confirm("Approve the candidates above? Plan hash: " + checked["plan_hash"]):
                    print("Not approved; saved and exiting. Resume after the changes.")
                    return
                atomic(lab.folder() / "review.md", "## Comments on the plan\n\n" + comments + "\n", text=True)
                lab.approve(checked["plan_hash"])
                continue
            if state["stage"] in {"approved", "running", "retry_backend"}:
                agent.turn("run", "Run mlab next. If approved or retry_backend, mlab workflow submit. Use mlab workflow wait --max 60 once. "
                    "If still running, end your turn; do not poll in a loop. Do not invent results or perform student-only commands.")
                after = lab.next()["stage"]
                if after == "running":
                    print("Still running; saved and exiting. Resume later with the same workspace.")
                    return
                if after == "retry_backend":
                    print("The model API is unavailable for now; this batch did not finish and used no budget. Saved and exiting; resume later with the same workspace.")
                    return
                require(after == "analysis_and_decision", "E_SCHEMA", "Agent did not submit/finish the expected batch; inspect classroom logs.", 2)
                continue
            if state["stage"] == "analysis_and_decision":
                agent.turn("analysis", "Run mlab compare and read its full result_path plus individual result metrics/constraints. "
                    "Write analysis.md citing real run IDs, metric fields and mock limitations, and say whether the results meet the falsification condition in hypothesis.md. "
                    "Write analysis.claims.json as a nonempty JSON array of {run_id,metric,value}, using exact numeric values from current run metrics. "
                    "Discuss alternative explanations and ask what the student would revise. Run mlab lint and stop. "
                    "Do not decide, select on behalf of a real student, validate or close.")
                claims = check_claims(lab)
                require(claims["valid"], "E_SCHEMA", "Analysis claims failed verification: " + str(claims["errors"]), 2)
                analysis = (lab.folder() / "analysis.md").read_text(encoding="utf-8")
                require(analysis.strip() and any(r in analysis for r in lab.workflow()["runs"]), "E_SCHEMA", "Analysis must cite actual current run IDs.", 2)
                build_report(lab)
                print("[metrics checked] " + json.dumps(lab.compare([]), ensure_ascii=False))
                decision = student.decision(lab)
                candidates = [c["id"] for c in load(lab.folder() / "frozen_plan.json")["candidates"]]
                chosen = student.candidate(candidates, lab)
                require(chosen in candidates, "E_SCHEMA", "Unknown student candidate; resume and select a listed candidate.", 2)
                if not student.confirm("Record your decision and select " + chosen + "?"):
                    return
                atomic(lab.folder() / "decision.md", decision + "\n", text=True)
                lab.decide(chosen)
                continue
            if state["stage"] == "decided":
                action = student.action(lab).strip().lower()
                if action == "revise":
                    lab.new_workflow()
                    continue
                if action == "close":
                    if student.confirm("Submit a conclusion on the current evidence? With missing evidence or failed validation it is unresolved."):
                        return finish(lab)
                    return
                require(action == "validate", "E_SCHEMA", "Choose revise, validate or close; /quit saves and exits.", 2)
                selected = lab.workflow().get("selected_candidate")
                require(selected, "E_SCHEMA", "This older workflow has no structured selected candidate; start a revised workflow.", 2)
                agent.turn("validate", "The student confirmed candidate " + selected + ". Run mlab validate --split repeat --candidate " + selected +
                    "; wait --max 60. If still running stop. Then mlab validate --split holdout --candidate " + selected +
                    "; wait --max 60. Do not close. Report only actual evidence, with mock_only limitations.")
                if lab.status()["status"] != "completed":
                    print("The validation runs have not finished; resume later.")
                    return
                if lab.backend_failures(lab.workflow()):
                    print("The model API is unavailable for now; some validation runs did not finish and used no budget. Saved and exiting; resume later with the same workspace.")
                    return
                validated = [r for r in lab.store.all("run") if r["workflow"] == lab.workflow()["id"] and r["candidate"] == selected]
                require({"repeat", "holdout"} <= {r["split"] for r in validated} and set(lab.task["holdouts"]) <= {r["holdout"] for r in validated if r["split"] == "holdout"},
                        "E_NEEDS_APPROVAL", "Agent did not submit all required validation splits; inspect evidence and resume.")
                if not student.confirm("Submit a conclusion on the actual evidence?"):
                    return
                return finish(lab)
    except (EOFError, KeyboardInterrupt):
        print("Workflow saved; give the same workspace next time to continue.")
    except LabError as error:
        lab.store.rollback()
        if error.code in {"E_SCHEMA", "E_INCOMPARABLE"}:
            lab.note_error(error.code)
        raise
    finally:
        lab.store.project()
        lab.close()


def run_classroom(gateway, key_env, workspace, task="9.2", backend="fixture", scripted=False, rounds=2):
    workspace = Path(workspace).resolve()
    if not workspace.exists():
        initialize(workspace, task, backend)
    else:
        require((workspace / ".mlab/config.json").exists(), "E_SCHEMA", "Existing directory is not an mlab workspace.", 2)
    teacher = ClaudeTeacher(gateway, key_env, workspace)
    try:
        report = classroom_loop(workspace, teacher, ScriptedStudent(rounds) if scripted else Student())
        if report and report.get("data_path"):
            data = load(Path(report["data_path"]))
            atomic(workspace / "classroom-verification.json", {"ok": True, "model": gateway.model,
                "student_type": "scripted" if scripted else "human", "iterations": len(data["iterations"]),
                "runs": len(data["runs"]), "conclusion": data["conclusion"],
                "selected_candidate": data["selected_candidate"], "report_path": report["data_path"],
                "scope": "Verified stored artifacts and confirmations; free-text teaching quality needs human review."})
        return report
    finally:
        teacher.close()
