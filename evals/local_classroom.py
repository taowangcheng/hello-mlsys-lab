"""Real Claude Code + local fake model: two student/agent iterations, no API key."""
import json
import argparse
from pathlib import Path
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from qwen_demo import Gateway, AnthropicEndpoint, check_api
from classroom import run_classroom
from mlab.core.io import atomic, load
from mlab.core.workspace import Lab


class FakeTeacherAPI(BaseHTTPRequestHandler):
    workspace = None
    def log_message(self, *args):
        pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body["messages"]
        instruction = messages[-1].get("content") or ""
        if "You are a MOCK performance estimator" in instruction:
            from mlab.backends.local import fixture
            data = json.loads(instruction.split("\n", 1)[1])
            value = fixture(data["spec"], data["requests"], 4.3)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}]}).encode())
            return
        if any(t["function"]["name"] == "check_status" for t in body.get("tools", [])):
            actions = [("check_status", {"experiment_id": "probe"})]
            count = sum(m["role"] == "tool" for m in messages)
        else:
            prompts = [(i, m["content"]) for i, m in enumerate(messages) if m["role"] == "user" and "CLI classroom phase:" in (m.get("content") or "")]
            index, prompt = prompts[-1]
            phase = re.search(r"CLI classroom phase: (\w+)", prompt)[1]
            count = sum(m["role"] == "tool" for m in messages[index + 1:])
            lab = Lab(self.workspace)
            try:
                folder = lab.folder()
                def write(path, content):
                    return ("Write", {"file_path": str(path), "content": content})
                def read(path):
                    return ("Read", {"file_path": str(path)})
                def bash(command):
                    return ("Bash", {"command": command, "timeout": 120000, "description": "Classroom integration test"})
                if phase == "plan":
                    import yaml
                    plan = load(folder / "plan.yaml")
                    plan["question"] = "Test the student's overload hypothesis against prefill blocking"
                    plan["derived_requirements"] = [{"name": "TTFT bound", "value": "p99 under 2000 ms", "derivation": "frozen task SLA"}]
                    actions = [read(folder / "plan.yaml"), write(folder / "plan.yaml", yaml.safe_dump(plan, allow_unicode=True))]
                    if lab.workflow()["iteration"] > 1:
                        candidate = load(folder / "candidates/low_concurrency.yaml")
                        candidate["strategy"]["serving"]["max_concurrency"] = 4
                        actions += [read(folder / "candidates/low_concurrency.yaml"), write(folder / "candidates/low_concurrency.yaml", yaml.safe_dump(candidate, allow_unicode=True))]
                    actions += [bash("mlab workflow check")]
                elif phase == "run":
                    actions = [bash("mlab workflow submit"), bash("mlab workflow wait --max 60")]
                elif phase == "analysis":
                    run = next(r for r in lab.workflow()["runs"] if lab.store.get("run", r)["failure_class"] is None)
                    result, _ = lab.result(run)
                    claims = [{"run_id": run, "metric": "ttft_ms.p99", "value": result["metrics"]["ttft_ms"]["p99"]}]
                    actions = [bash("mlab compare"), read(folder / "analysis.md"), write(folder / "analysis.md", f"mock_only placeholder numbers. Evidence {run}; check the overload alternative and ask the student to revise."),
                               write(folder / "analysis.claims.json", json.dumps(claims)), bash("mlab lint")]
                elif phase == "validate":
                    candidate = lab.workflow()["selected_candidate"]
                    actions = [bash(f"mlab validate --split repeat --candidate {candidate}"), bash("mlab workflow wait --max 60"),
                               bash(f"mlab validate --split holdout --candidate {candidate}"), bash("mlab workflow wait --max 60")]
                else:
                    raise ValueError("Unknown phase")
            finally:
                lab.close()
        last = messages[-1]
        if last["role"] == "tool" and last["content"].startswith("Tool error:"):
            reply = {"content": "Tool failed; stopping integration test."}
        elif count < len(actions):
            name, args = actions[count]
            reply = {"tool_calls": [{"id": "test_" + uuid.uuid4().hex, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        else:
            reply = {"content": "Test phase complete. Student-only decisions remain in the controller. mock_only."}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": reply, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}).encode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["fixture", "llm"], default="fixture")
    parser.add_argument("--protocol", choices=["anthropic", "openai"], default="anthropic")
    parser.add_argument("--interactive", action="store_true", help="Real student input with a scripted local model (no API key)")
    parser.add_argument("--workspace", type=Path, help="Reuse this workspace to resume a student demo")
    args = parser.parse_args()
    workspace = args.workspace.resolve() if args.workspace else ROOT / "workspaces" / ("classroom-local-" + uuid.uuid4().hex[:10])
    FakeTeacherAPI.workspace = workspace
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTeacherAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    native_service = None
    try:
        gateway = Gateway(f"http://127.0.0.1:{server.server_port}/v1", "local-fake-teacher-not-qwen", "fake-key", "fake-token")
        if args.protocol == "anthropic":
            # This server is the fake provider. The classroom client connects
            # directly with Messages and starts no conversion gateway itself.
            gateway.token = "native-api-test-secret-123"
            native_service = gateway.serve()
            gateway = AnthropicEndpoint(f"http://127.0.0.1:{native_service.server_port}", gateway.model,
                "native-api-test-secret-123", "unused-local-token")
        check_api(gateway)
        report = run_classroom(gateway, "QWEN_API_KEY", workspace, backend=args.backend, scripted=not args.interactive)
        if report is None:
            print("Saved student demo: " + str(workspace))
            raise SystemExit(0)
        data = load(Path(report["data_path"]))
        if not args.interactive:
            assert len(data["iterations"]) == 2 and len(data["runs"]) == 9
            assert data["conclusion"] == "mock_only" and data["selected_candidate"] == "low_concurrency"
        runtime_env = load(workspace / ".mlab/classroom/settings.json")["env"]
        assert "ANTHROPIC_API_KEY" not in runtime_env and "ANTHROPIC_AUTH_TOKEN" not in runtime_env
        atomic(workspace / "classroom-verification.json", {"ok": True, "source": "real Claude Code + local fake API",
            "real_qwen": False, "scripted_student": not args.interactive, "protocol": args.protocol,
            "iterations": len(data["iterations"]), "runs": len(data["runs"]), "selected_candidate": data["selected_candidate"],
            "checks": ["verified report", "runtime credentials not saved"] if args.interactive else
                ["agent writes plans", "agent writes analysis", "student approvals outside agent", "session resume", "second-round revision", "repeat and holdout", "verified report"]})
        print("PASS classroom integration: " + str(workspace))
    finally:
        if native_service:
            native_service.shutdown()
            native_service.server_close()
        server.shutdown()
        server.server_close()
