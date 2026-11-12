"""Explicit integration smoke: real Claude Code + fake local OpenAI API + real mlab.

No Qwen/network credentials. The fake model follows a fixed tool script, so this
checks protocol/tool execution only, not a real model's planning ability.
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from qwen_demo import Gateway, check_api, run_demo


class FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if any(t["function"]["name"] == "check_status" for t in body.get("tools", [])):
            if body["messages"][-1]["role"] == "tool":
                message = {"content": "ready"}
            else:
                message = {"tool_calls": [{"id": "probe", "type": "function", "function": {
                    "name": "check_status", "arguments": '{"experiment_id":"probe"}'}}]}
        else:
            text = "\n".join(m.get("content") or "" for m in body["messages"] if m["role"] == "user")
            commands = [c.strip() for c in re.findall(r"^mlab .+$", text, re.MULTILINE)]
            # Count actual tool results, not just attempted tool calls.
            count = sum(m["role"] == "tool" for m in body["messages"])
            if body["messages"][-1]["role"] == "tool" and body["messages"][-1]["content"].startswith("Tool error:"):
                message = {"content": "Tool failed; stopping local integration smoke."}
            elif count < len(commands):
                message = {"tool_calls": [{"id": f"step_{count}", "type": "function", "function": {
                    "name": "Bash", "arguments": json.dumps({"command": commands[count], "timeout": 120000, "description": "Run authorized mock demonstration"})}}]}
            else:
                message = {"content": "Local fake API integration finished; fixture results are mock_only. This is not real Qwen verification."}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}).encode())


if __name__ == "__main__":
    api = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    try:
        gateway = Gateway(f"http://127.0.0.1:{api.server_port}/v1", "local-fake-qwen-not-real", "fake-test-key", "fake-local-token")
        check_api(gateway)
        run_demo(gateway, "QWEN_API_KEY")
    finally:
        api.shutdown()
        api.server_close()
