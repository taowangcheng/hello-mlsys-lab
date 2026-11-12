import argparse
import json
import os
import re
import sys
from pathlib import Path
import yaml
from pydantic import ValidationError
from .. import __version__
from ..contracts import Candidate, Capabilities, MeasurementPlan, Plan, RequestObservation, RunResult, RunSpec, TaskDefinition
from ..core.io import ROOT, NOTICE, TERMINAL, LabError, atomic, claude_install, claude_logged_in, hashed, load, pinned_claude_version, require
from ..core.student import log_prompt
from ..core.workspace import RUN_ID, Lab, find_workspace, initialize

MEASUREMENT_COMMANDS = {"measure", "align"}


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise LabError("E_SCHEMA", message, 2)


def parser():
    root = JsonParser(prog="mlab", description="hello-mlsys CLI teaching agent tools")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--workspace", type=Path)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("directory")
    init.add_argument("--task", choices=["9.1", "9.2", "9.3"], default="9.2")
    init.add_argument("--simulator", "--backend", dest="simulator", choices=["llm", "fixture"], default="llm",
                      help="Fast flywheel backend (--backend is the older name)")
    init.add_argument("--hardware", choices=["standin"], default="standin", help="Slow flywheel backend")
    init.add_argument("--provider")
    init.add_argument("--mode", choices=["guided", "open"], default="guided", help="open lets advanced students skip hypothesis-first")
    nxt = commands.add_parser("next")
    nxt.add_argument("--hook", action="store_true")
    hook = commands.add_parser("hook")
    hook.add_argument("event", choices=["user-prompt"])
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--web", action="store_true", help="Also check the web page's dependencies")
    doctor_parser.add_argument("--agent", choices=["codex", "sdk"], default="codex")
    task = commands.add_parser("task")
    task.add_argument("action", choices=["show"])
    workflow = commands.add_parser("workflow")
    workflow.add_argument("action", choices=["new", "show", "check", "approve", "submit", "wait", "status", "cancel", "decide", "unlock"])
    workflow.add_argument("--example", action="store_true")
    workflow.add_argument("--from", dest="source", help="Start the new round from an approved round's candidates, e.g. r002")
    workflow.add_argument("--plan")
    workflow.add_argument("--max", type=float, default=60)
    workflow.add_argument("--candidate")
    measure = commands.add_parser("measure")
    measure.add_argument("action", choices=["new", "show", "check", "approve", "submit", "wait", "status", "cancel", "unlock"])
    measure.add_argument("--plan")
    measure.add_argument("--max", type=float, default=60)
    align = commands.add_parser("align", help="The simulator's frozen predictions next to the hardware results; writes nothing")
    align.add_argument("measurement", nargs="?", help="e.g. m001; the current measurement when omitted")
    conclude = commands.add_parser("conclude", help="Submit the task's conclusion for the selected configuration")
    conclude.add_argument("--candidate", help="A configuration other than the current selection, e.g. r002/c16")
    show = commands.add_parser("run")
    show.add_argument("action", choices=["show"])
    show.add_argument("id")
    show.add_argument("--metrics", action="store_true")
    show.add_argument("--constraints", action="store_true")
    show.add_argument("--logs", action="store_true")
    show.add_argument("--timeline", action="store_true", help="Paginated request ready/finish times, in milliseconds")
    show.add_argument("--page", type=int, default=1)
    compare = commands.add_parser("compare")
    compare.add_argument("runs", nargs="*")
    commands.add_parser("lint")
    validation = commands.add_parser("validate")
    validation.add_argument("--split", choices=["repeat", "holdout"], required=True)
    validation.add_argument("--candidate", required=True)
    commands.add_parser("report")
    past = commands.add_parser("history")
    past.add_argument("--page", type=int, default=1)
    record = commands.add_parser("show")
    record.add_argument("id")
    record.add_argument("--part")
    record.add_argument("--page", type=int, default=1)
    schema = commands.add_parser("schema")
    schema.add_argument("--out", type=Path, default=Path("schemas"))
    demo = commands.add_parser("demo")
    demo.add_argument("--out", type=Path, required=True)
    demo.add_argument("--task", choices=["9.1", "9.2", "9.3"], default="9.2")
    web = commands.add_parser("web", help="Serve the course page: one agent per task, on 127.0.0.1")
    web.add_argument("directory", nargs="?", default="mlsys-course", help="Course directory; created if missing. Each session started on the page is a workspace in it")
    web.add_argument("--simulator", choices=["llm", "fixture"], default="llm")
    web.add_argument("--provider", help="Model profile under agent/profiles (default: codex-luna)")
    web.add_argument("--agent", choices=["codex", "sdk", "fake"], default="codex", help="codex: isolated Codex; fake: offline; sdk: legacy Claude")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true")
    web.add_argument("--no-opening", action="store_true", help="Do not let the agent speak first when a task page opens")
    web.add_argument("--auto-continue", action="store_true", help="Let the agent continue by itself when runs finish")
    generate = commands.add_parser("generate-workload")
    generate.add_argument("--task", choices=["9.1", "9.2", "9.3"], required=True)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--out", type=Path, required=True)
    return root


def web_doctor(agent="codex"):
    """Check web dependencies and the default port without starting a model."""
    import importlib.util
    import socket
    modules = [("starlette", "starlette"), ("uvicorn", "uvicorn")]
    if agent == "sdk":
        modules.append(("sdk", "claude_agent_sdk"))
    report = {name: bool(importlib.util.find_spec(module)) for name, module in modules}
    try:
        with socket.socket() as probe:
            report["port_8765_free"] = probe.connect_ex(("127.0.0.1", 8765)) != 0
        report["local_tcp_available"] = True
    except OSError:
        report.update(port_8765_free=None, local_tcp_available=False)
    return report


def doctor(web=False, agent="codex"):
    if agent == "codex":
        from ..codex import MODEL, VERSION, credentials, installation
        path, version = installation()
        try:
            auth = credentials()["type"]
        except LabError:
            auth = None
        report = {"python": sys.version.split()[0], "mlab": __version__, "agent": "codex", "model": MODEL,
                  "codex_path": path, "codex": version, "codex_pinned": VERSION, "codex_version_matches_pin": version == VERSION,
                  "auth": auth, "state_directory": "<workspace>/.mlab/codex", "credentials_on_disk": False,
                  "llm_route": "api" if os.environ.get("MLAB_LLM_BASE_URL") else "codex_login",
                  "fixture_ready": True, "warning": "No live model call performed."}
        if web:
            report["web"] = web_doctor()
        if version != VERSION:
            report["next_actions"] = [{"actor": "student", "do": "fix", "say": "Run scripts/setup.sh to install the supported Codex CLI."}]
        elif web and not all(report["web"].get(k) for k in ("starlette", "uvicorn")):
            report["next_actions"] = [{"actor": "student", "do": "fix", "say": "Run scripts/setup.sh to install the web extra."}]
        elif not auth:
            report["next_actions"] = [{"actor": "student", "do": "fix", "say": "Run codex login or set OPENAI_API_KEY; mlab does not modify that login."}]
        elif web and not report["web"]["local_tcp_available"]:
            report["next_actions"] = [{"actor": "student", "do": "fix", "say": "Run the page in an environment that permits local TCP sockets."}]
        return report
    claude, version = claude_install()
    pinned = pinned_claude_version()
    ready = all(os.environ.get(v) for v in ["MLAB_LLM_BASE_URL", "MLAB_LLM_API_KEY", "MLAB_LLM_MODEL"]) or (
        all(os.environ.get(v) for v in ["ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"]) and bool(os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")))
    compatibility = load(ROOT / "agent/compatibility.json")
    login = claude_logged_in(claude) if claude else None
    report = {"python": sys.version.split()[0], "mlab": __version__, "claude": version, "claude_path": claude, "claude_pinned": pinned,
              "claude_version_matches_pin": version == pinned, "claude_logged_in": login,
              "local_version_evidence": compatibility["verified"].get(version, []),
              "llm_route": "api" if ready else "claude_login", "fixture_ready": True,
              "warning": "No live model call performed."}
    if web:
        report["web"] = web_doctor("sdk")
    if version != pinned:
        report["next_actions"] = [{"actor": "student", "do": "fix", "say": f"Run scripts/setup.sh to install Claude Code {pinned} into this project."}]
    elif web and not all(report["web"].get(k) for k in ("sdk", "starlette", "uvicorn")):
        report["next_actions"] = [{"actor": "student", "do": "fix", "say": "Run scripts/setup.sh to install the web extra."}]
    elif login is False and not ready:
        report["next_actions"] = [{"actor": "student", "do": "fix", "say": "Log in once: run ./claude in a workspace, or claude from an activated environment."}]
    return report


def finish(lab, wait, label):
    require(wait(30)["status"] == "completed", "E_BACKEND_UNAVAILABLE", label + " did not finish", 4)


def demo(out, task):
    """Scripted student on fixture and the hardware stand-in: one simulator round, simulator validation, one measurement."""
    initialize(out, task, "fixture")
    lab = Lab(out)
    try:
        lab.new_workflow(example=True)
        folder = lab.folder()
        atomic(folder / "hypothesis.md", "## Hypothesis\n\nScripted student example: compare declarative candidates to test a configuration "
               "trade-off; not a real student's submission.\n\n"
               "## What result would prove me wrong\n\nIf every candidate violates the constraints, this direction does not hold.\n", text=True)
        checked = lab.check()
        atomic(folder / "review.md", "## Comments on the plan\n\n- Scripted student: agree with the plan, no changes.\n", text=True)
        lab.approve(checked["plan_hash"])
        submitted = lab.submit()
        finish(lab, lab.wait, "demo")
        comparison = lab.compare(submitted["run_ids"])
        atomic(folder / "analysis.md", "# Scripted mock example\n\n" + "\n".join(f"- Evidence {r}: result.json; placeholder numbers only." for r in submitted["run_ids"]) + "\n", text=True)
        _, candidates, _ = lab.specifications()
        successful = [r for r in submitted["run_ids"] if lab.result(r)[0]["verdict"] == "constraints_met"]
        if successful and task == "9.2":
            successful.sort(key=lambda identifier: lab.result(identifier)[0]["metrics"]["ttft_ms"]["p99"])
        selected = lab.result(successful[0])[0]["candidate_id"] if successful else candidates[0]["id"]
        atomic(folder / "decision.md", f"Scripted demo decision: select {selected} to walk through validation; not a real student's conclusion. "
               f"Evidence: {', '.join(submitted['run_ids'])}.\n", text=True)
        lab.decide(selected)
        for split in ["repeat", "holdout"]:
            lab.submit(split, selected)
            finish(lab, lab.wait, "validation")
        # Slow flywheel: measure the selection once on the hardware stand-in and set it against the simulator.
        lab.measure_new()
        atomic(lab.mfolder() / "points.yaml", {"schema": "mlab.v1", "measurement": "m001", "points": [
            {"candidate": selected, "workload": "development", "runs": 1, "rationale": "Scripted student: check the selected candidate on the hardware stand-in"}]})
        checked = lab.measure_check()
        if checked.get("status") == "simulating":
            finish(lab, lab.measure_wait, "simulator prediction")
            checked = lab.measure_check()
        lab.measure_approve(checked["plan_hash"])
        lab.measure_submit()
        finish(lab, lab.measure_wait, "measurement")
        aligned = lab.align()
        closure = lab.conclude()
        state = lab.next()
        summary = {"workspace": str(lab.root), "comparison": comparison["result_path"], "conclusion": closure["conclusion"],
                   "reason": closure["reason"], "runs": len(lab.store.all("run")), "hardware_budget": state["budget"]["hardware"],
                   "selected_candidate": selected, "alignment": aligned["rows"],
                   "selection_basis": "minimum mock ttft_ms.p99 among feasible candidates" if task == "9.2" else "first feasible mock candidate",
                   "note": "Scripted fixture student and hardware stand-in, not a live LLM or a real GPU."}
        atomic(lab.root / "demo-summary.json", summary)
        return summary
    finally:
        lab.store.project()
        lab.close()


def run_command(args, lab):
    if args.command == "next":
        return lab.next()
    if args.command == "task":
        guide = lab.root / "task/GUIDE.md"
        return {"task": lab.task["id"], "title": lab.task["title"], "status": lab.task["status"],
                "path": str(lab.root / "task/task.yaml"), "key_metrics": lab.task["key_metrics"], "duration_s": lab.task["duration_s"],
                "holdouts": lab.task["holdouts"], "hardware_budget": lab.hardware_budget(),
                **({"guide_path": str(guide)} if guide.exists() else {})}
    if args.command == "workflow":
        action = args.action
        if action == "new":
            return lab.new_workflow(args.example, args.source)
        if action == "check":
            return lab.check()
        if action == "approve":
            require(args.plan, "E_SCHEMA", "--plan required", 2)
            return lab.approve(args.plan)
        if action == "submit":
            return lab.submit()
        if action == "wait":
            require(0 <= args.max <= 60, "E_SCHEMA", "--max must be between 0 and 60 seconds", 2)
            return lab.wait(args.max)
        if action == "status":
            return lab.status()
        if action == "decide":
            return lab.decide(args.candidate)
        if action == "unlock":
            return lab.unlock()
        if action == "cancel":
            from ..backends.local import LocalBackend
            for run in lab.records(lab.status()["run_ids"]):
                LocalBackend(lab.root, run["backend"]).cancel({"run_id": run["id"]})
            return lab.status()
        w = lab.workflow()
        return {"workflow": w["id"], "status": w["status"], "locked": w["locked"], "path": str(lab.folder()), "errors": w["errors"]}
    if args.command == "measure":
        action = args.action
        if action == "new":
            return lab.measure_new()
        if action == "check":
            return lab.measure_check()
        if action == "approve":
            require(args.plan, "E_SCHEMA", "--plan required", 2)
            return lab.measure_approve(args.plan)
        if action == "submit":
            return lab.measure_submit()
        if action == "wait":
            require(0 <= args.max <= 60, "E_SCHEMA", "--max must be between 0 and 60 seconds", 2)
            return lab.measure_wait(args.max)
        if action == "status":
            return lab.measure_status()
        if action == "unlock":
            return lab.measure_unlock()
        if action == "cancel":
            from ..backends.local import LocalBackend
            for run in lab.records(lab.measure_status()["run_ids"]):
                LocalBackend(lab.root, run["backend"]).cancel({"run_id": run["id"]})
            return lab.measure_status()
        m = lab.measurement()
        return {"measurement": m["id"], "status": m["status"], "locked": m["locked"], "path": str(lab.mfolder(m)), "errors": m["errors"],
                "hardware_budget": lab.hardware_budget()}
    if args.command == "align":
        return lab.align(args.measurement)
    if args.command == "conclude":
        return lab.conclude(args.candidate)
    if args.command == "validate":
        return lab.submit(args.split, args.candidate)
    if args.command == "compare":
        return lab.compare(args.runs)
    if args.command == "run":
        result, spec = lab.result(args.id)
        record = lab.store.get("run", args.id)
        path = lab.root / record["folder"]
        if args.timeline:
            require(args.page >= 1, "E_SCHEMA", "--page must be positive", 2)
            rows = [json.loads(line) for line in (path / "requests.jsonl").read_text(encoding="utf-8").splitlines() if line]
            rows.sort(key=lambda row: (row["ready_ms"], row["request_id"]))
            start = (args.page - 1) * 4
            return {"run_id": args.id, "page": args.page, "evidence_level": result["evidence_level"],
                    "rows": [{key: row.get(key) for key in ("request_id", "ready_ms", "first_token_ms", "finished_ms", "status")} for row in rows[start:start + 4]],
                    "has_more": start + 4 < len(rows), "critical_path_s": result["metrics"].get("critical_path_s")}
        if args.logs:
            require(args.page >= 1, "E_SCHEMA", "--page must be positive", 2)
            raw = (path / "logs.txt").read_text(encoding="utf-8")
            # Character bounds still work if a backend emits one extremely long line.
            start = (args.page - 1) * 280
            return {"run_id": args.id, "page": args.page, "text": raw[start:start + 280], "has_more": start + 280 < len(raw)}
        return {"run_id": args.id, "role": record.get("role", "simulator"), "candidate": record["candidate"], "split": record["split"],
                "holdout": record["holdout"],
                "status": result["status"], "verdict": result["verdict"], "evidence_level": result["evidence_level"],
                "data": result["constraints"] if args.constraints else result["metrics"] if args.metrics else {"failure_class": result["failure_class"]},
                "result_path": str(path / "result.json")}
    if args.command == "lint":
        from ..core.evidence import check_claims
        text = (lab.folder() / "analysis.md").read_text(encoding="utf-8")
        known = {r["id"] for r in lab.store.all("run")}
        unknown = sorted(set(RUN_ID.findall(text)) - known)
        numbers = re.findall(r"(?<![\w])\d+(?:\.\d+)?", RUN_ID.sub("", text))
        warning = ["unknown run IDs: " + ",".join(unknown)] if unknown else []
        if numbers:
            warning.append("Review numeric claims against result fields: " + ",".join(numbers[:12]))
        return {"warnings": warning, "blocking": False, "claims": check_claims(lab),
                "note": "Free-text lint is heuristic; structured claims are checked against run artifacts."}
    if args.command == "report":
        from ..core.reporting import build_report
        return build_report(lab)
    if args.command == "history":
        from ..core.history import history
        return history(lab, args.page)
    if args.command == "show":
        from ..core.history import show
        return show(lab, args.id, args.part, args.page)
    raise LabError("E_SCHEMA", "unknown command", 2)


def emit(output, hook=False, directory=None):
    envelope = {"schema": "mlab.v1", "ok": True, "status": "ok", "notice": NOTICE, **output}
    if hook:
        envelope = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": json.dumps(envelope, ensure_ascii=False)}}
    text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if not hook and len(text.encode("utf-8")) > 1500:
        # Detailed output is always saved; short tool response points to the artifact.
        destination = Path(directory or Path.cwd()) / ".mlab/last-output.json"
        atomic(destination, envelope)
        # Orientation fields survive, so a truncated reply still says where the work stands.
        keep = ("schema", "ok", "status", "notice", "stage", "measurement_stage", "workflow", "measurement", "plan_hash", "conclusion")
        envelope = {k: envelope[k] for k in keep if k in envelope}
        if "error" in output:
            envelope["error"] = {**output["error"], "message": output["error"]["message"][:100]}
        if "next_actions" in output:
            envelope["next_actions"] = [{**action, "say": action["say"][:400]} for action in output["next_actions"]]
        envelope.update(result_path=str(destination), message="Detailed output saved; use paginated commands or read this file.")
        text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    print(text)


def record_prompt(argv):
    """UserPromptSubmit hook. It prints nothing and always exits 0, so it can never block the student's message."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        log_prompt(find_workspace(payload.get("cwd") or None), payload)
    except Exception:
        pass
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[-2:] == ["hook", "user-prompt"]:
        return record_prompt(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lab = None
    args = None
    try:
        args = parser().parse_args(argv)
        if args.command == "init":
            output = initialize(args.directory, args.task, args.simulator, args.provider, args.hardware, args.mode)
        elif args.command == "doctor":
            output = doctor(args.web, args.agent)
        elif args.command == "hook":
            return record_prompt(argv)
        elif args.command == "schema":
            models = [TaskDefinition, Candidate, Plan, RunSpec, RequestObservation, RunResult, Capabilities, MeasurementPlan]
            for model in models:
                atomic(args.out / (model.__name__ + ".schema.json"), model.model_json_schema(by_alias=True))
            output = {"schemas": len(models), "result_path": str(args.out.resolve())}
        elif args.command == "demo":
            output = demo(args.out, args.task)
        elif args.command == "web":
            from ..web.server import serve
            return serve(args.directory, args.simulator, args.provider, args.agent, args.port, not args.no_browser,
                         not args.no_opening, args.auto_continue)
        elif args.command == "generate-workload":
            from ..core.workspace import TASKS
            from ..core.workloads import generate, write_workload
            source = ROOT / "tasks" / TASKS[args.task]
            task = load(source / "task.yaml")
            path = source / task["frozen"]["workload_requests"]["path"]
            require(not args.out.exists(), "E_SCHEMA", "output already exists", 2)
            base = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            output = write_workload(args.out, generate(args.task, base, args.seed), args.task, args.seed, "development")
        else:
            lab = Lab(args.workspace)
            output = run_command(args, lab)
            output.setdefault("next_actions", lab.next()["next_actions"])
            lab.store.project()
        emit(output, bool(args and args.command == "next" and args.hook), lab.root if lab else None)
        return 0
    except (LabError, ValidationError, ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as error:
        if not isinstance(error, LabError):
            error = LabError("E_SCHEMA", str(error)[:350], 2)
        if lab:
            lab.store.rollback()
            if error.code in {"E_SCHEMA", "E_INCOMPARABLE", "E_REPEAT_LIMIT"}:
                try:
                    lab.note_error(error.code, "measurement" if args and args.command in MEASUREMENT_COMMANDS else "workflow")
                except LabError:
                    pass
        # A hook only reaches the session through this envelope and exit 0.
        hook = bool(args and args.command == "next" and args.hook)
        emit({"ok": False, "status": "rejected", "error": {"code": error.code, "message": str(error)[:350], "field": error.field}, "next_actions": error.actions}, hook, directory=lab.root if lab else None)
        if hook:
            return 0
        return error.exit_code
    finally:
        if lab:
            lab.close()
