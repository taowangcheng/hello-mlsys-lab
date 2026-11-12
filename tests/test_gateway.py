import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mlab.gateway import Gateway, GatewayError, from_openai, to_openai


def test_tool_history_and_schema_translation():
    body = {"system": [{"type": "text", "text": "teach"}], "messages": [
        {"role": "user", "content": "check"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"command": "mlab next"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": [{"type": "text", "text": "ready"}]}]}],
        "tools": [{"name": "Bash", "input_schema": {"type": "object"}}], "tool_choice": {"type": "any"}, "max_tokens": 99999}
    result = to_openai(body, "candidate", 4096)
    assert result["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "ready"}
    assert json.loads(result["messages"][-2]["tool_calls"][0]["function"]["arguments"]) == {"command": "mlab next"}
    assert result["tool_choice"] == "required"
    assert result["max_tokens"] == 4096


def test_reject_unsupported_images_and_malformed_tool_arguments():
    with pytest.raises(GatewayError):
        to_openai({"messages": [{"role": "user", "content": [{"type": "image"}]}]}, "model", 100)
    with pytest.raises(GatewayError):
        from_openai({"choices": [{"message": {"tool_calls": [{"id": "a", "function": {"name": "Bash", "arguments": "oops"}}]}}]}, "model")


def test_routing_and_secrets_are_not_configurable_per_request():
    with pytest.raises(GatewayError):
        Gateway("http://public.example/v1", "m", "secret", "token")
    with pytest.raises(GatewayError):
        Gateway("https://example.com/v1?key=secret", "m", "secret", "token")
    with pytest.raises(GatewayError):
        Gateway("https://example.com/v1", "m", "secret", "token", extra_body={"messages": []})


def test_http_stream_auth_error_sanitization_and_tool_roundtrip():
    requests = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            if body["messages"][-1]["content"] == "fail":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"DO_NOT_LEAK_SECRET")
                return
            if body["messages"][-1]["role"] == "tool":
                message = {"content": "Tool result received."}
            else:
                message = {"tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "Bash", "arguments": '{"command":"mlab next"}'}}]}
            data = {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    gateway = Gateway(f"http://127.0.0.1:{upstream.server_port}/v1", "m", "fake-upstream-key", "local-token")
    server = gateway.serve()
    url = f"http://127.0.0.1:{server.server_port}/v1/messages?beta=true"
    def call(body, token="local-token"):
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Authorization": "Bearer " + token})
        return urllib.request.urlopen(req, timeout=10)
    try:
        body = {"messages": [{"role": "user", "content": "start"}]}
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            call(body, "bad")
        assert unauthorized.value.code == 401
        with call(body) as response:
            answer = json.load(response)
        body["messages"] += [{"role": "assistant", "content": answer["content"]}, {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "real tool output"}]}]
        body["stream"] = True
        with call(body) as response:
            stream = response.read().decode()
        assert "message_start" in stream and "content_block_delta" in stream
        assert stream.index('event: message_delta') < stream.index('event: message_stop')
        assert "Tool result received." in stream
        assert requests[-1][0] == "/v1/chat/completions"
        assert requests[-1][1] == "Bearer fake-upstream-key"
        assert requests[-1][2]["messages"][-1]["role"] == "tool"
        with pytest.raises(urllib.error.HTTPError) as failure:
            call({"messages": [{"role": "user", "content": "fail"}]})
        assert failure.value.code == 401
        assert "DO_NOT_LEAK_SECRET" not in failure.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        upstream.shutdown()
        upstream.server_close()


def test_system_messages_inside_the_conversation_pass_through():
    # Claude Code with its default prompt inserts system messages between turns (seen with 2.1.317).
    body = {"system": [{"type": "text", "text": "base"}], "max_tokens": 100, "messages": [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "system", "content": [{"type": "text", "text": "reminder"}]}]}
    converted = to_openai(body, "m", 4096)
    assert [m["role"] for m in converted["messages"]] == ["system", "user", "system"]
    assert converted["messages"][2]["content"] == "reminder"
