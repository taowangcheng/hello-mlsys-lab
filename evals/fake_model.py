"""A scripted, OpenAI-shaped fake model behind the Anthropic gateway, for real Claude Code runs without an API key.

`script(messages)` returns the next reply: {"content": text} or {"tool_calls": [{"name": ..., "arguments": {...}}]}.
Claude Code talks Anthropic Messages to `Gateway.serve()`, which converts to OpenAI chat for this server.
"""
import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mlab.gateway import Gateway, connect_claude  # noqa: E402


def tool_call(name, **arguments):
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


class FakeModel:
    def __init__(self, script):
        self.script, self.requests = script, []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model.requests.append(body)
                reply = model.script(body["messages"])
                message = {"content": reply.get("content", "")}
                if reply.get("tool_calls"):
                    message = {"content": None, "tool_calls": [
                        {"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                         "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)}}
                        for call in reply["tool_calls"]]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": message, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 10}}).encode())

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        gateway = Gateway(f"http://127.0.0.1:{self.upstream.server_port}/v1", "fake-model", "fake-key", "fake-gateway-token-0123456789")
        self.gateway, self.url, self.token = connect_claude(gateway)

    def environment(self, home):
        """Variables that point Claude Code at this model, with an isolated config directory."""
        return {"ANTHROPIC_BASE_URL": self.url, "ANTHROPIC_AUTH_TOKEN": self.token, "ANTHROPIC_MODEL": "fake-model",
                "CLAUDE_CONFIG_DIR": str(home), "DISABLE_UPDATES": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "MAX_THINKING_TOKENS": "0"}

    def close(self):
        for server in (self.gateway, self.upstream):
            if server:
                server.shutdown()
                server.server_close()


def trusted_home(home, workspace):
    """A Claude Code config directory that trusts the workspace (and its parents, which some versions key on)."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    workspace = Path(workspace).resolve()
    (home / ".claude.json").write_text(json.dumps({"projects": {str(p): {"hasTrustDialogAccepted": True} for p in [workspace, *workspace.parents]}}))
    return home


def tool_results(messages):
    return sum(message["role"] == "tool" for message in messages)
