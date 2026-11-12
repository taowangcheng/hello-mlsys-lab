import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mlab.gateway import AnthropicEndpoint, connect_claude
from mlab.core.io import ROOT
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from qwen_demo import child_environment, make_endpoint


def test_native_messages_route_headers_and_no_gateway():
    received = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("x-api-key"), body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"content": [{"type": "text", "text": "native reply"}]}).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        endpoint = AnthropicEndpoint(f"http://127.0.0.1:{server.server_port}", "native-model", "fake-native-key", "unused")
        process, url, token = connect_claude(endpoint)
        assert process is None and url == endpoint.url and token == "fake-native-key"
        value = endpoint.complete({"messages": [{"role": "user", "content": "hello"}], "max_tokens": 64})
        assert value["content"][0]["text"] == "native reply"
        assert received[0][0] == "/v1/messages" and received[0][1] == "fake-native-key"
        assert received[0][2]["model"] == "native-model"
        env = child_environment("native-model", url, token, "ANTHROPIC_API_KEY", ROOT / ".test-runs/native-config", "api_key")
        assert env["ANTHROPIC_API_KEY"] == token and "ANTHROPIC_AUTH_TOKEN" not in env
        bearer = make_endpoint({"protocol": "anthropic", "auth_mode": "bearer"}, url, "native-model", token)
        assert isinstance(bearer, AnthropicEndpoint) and bearer.auth_mode == "bearer"
        bearer_env = child_environment("native-model", url, token, "ANTHROPIC_AUTH_TOKEN", ROOT / ".test-runs/native-config", bearer.auth_mode)
        assert bearer_env["ANTHROPIC_AUTH_TOKEN"] == token and "ANTHROPIC_API_KEY" not in bearer_env
    finally:
        server.shutdown()
        server.server_close()
