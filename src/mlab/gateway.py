"""Small text/function-tool Anthropic -> OpenAI Chat Completions bridge.

Only loopback is served. The upstream key never enters Claude's environment.
Upstream replies are buffered; SSE heartbeats keep the Claude connection alive.
This is a CLI-demo adapter, not a general Anthropic protocol implementation.
"""
import hmac
import json
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class GatewayError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text_content(value):
    if isinstance(value, str):
        return value
    parts = []
    for block in value or []:
        if block.get("type") != "text":
            raise GatewayError("Only text and function tools are supported; unsupported content block.")
        parts.append(block.get("text", ""))
    return "\n".join(parts)


def to_openai(body, model, max_tokens):
    messages = []
    if body.get("system"):
        messages.append({"role": "system", "content": text_content(body["system"])})
    for message in body.get("messages", []):
        role = message["role"]
        content = message.get("content", [])
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if role == "assistant":
            texts, calls = [], []
            for block in content:
                kind = block.get("type")
                if kind == "text":
                    texts.append(block["text"])
                elif kind == "tool_use":
                    calls.append({"id": block["id"], "type": "function", "function": {
                        "name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
                else:
                    raise GatewayError("Unsupported assistant block; use the text/tool CLI preset.")
            item = {"role": role, "content": "\n".join(texts) or None}
            if calls:
                item["tool_calls"] = calls
            messages.append(item)
        elif role == "user":
            # Tool results must precede the next user turn in OpenAI conversations.
            results = [b for b in content if b.get("type") == "tool_result"]
            for block in results:
                result = text_content(block.get("content", ""))
                if block.get("is_error"):
                    result = "Tool error: " + result
                messages.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": result})
            remaining = [b for b in content if b.get("type") != "tool_result"]
            if remaining:
                messages.append({"role": "user", "content": text_content(remaining)})
        elif role == "system":
            # Claude Code with its default prompt adds system messages mid-conversation; OpenAI accepts them anywhere.
            messages.append({"role": "system", "content": text_content(content)})
        else:
            raise GatewayError("Unsupported message role.")
    result = {"model": model, "messages": messages, "stream": False,
              "max_tokens": min(int(body.get("max_tokens", max_tokens)), max_tokens)}
    tools = []
    for tool in body.get("tools", []):
        if "input_schema" not in tool or tool.get("type", "custom") != "custom":
            raise GatewayError("Only custom function tools are supported.")
        tools.append({"type": "function", "function": {"name": tool["name"],
                      "description": tool.get("description", ""), "parameters": tool["input_schema"]}})
    if tools:
        result["tools"] = tools
        choice = body.get("tool_choice", {"type": "auto"})
        kind = choice.get("type", "auto")
        if kind == "tool":
            result["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
        elif kind in {"auto", "any", "none"}:
            result["tool_choice"] = {"any": "required"}.get(kind, kind)
        else:
            raise GatewayError("Unsupported tool_choice.")
    # Anthropic cache hints, thinking and context-management options have no
    # equivalent here. The launcher disables thinking/betas for this preset.
    return result


def from_openai(data, model):
    try:
        choice = data["choices"][0]
        message = choice["message"]
        content = []
        if message.get("content"):
            content.append({"type": "text", "text": text_content(message["content"])})
        for call in message.get("tool_calls", []):
            args = json.loads(call["function"]["arguments"])
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be an object")
            content.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": args})
        if not content:
            raise ValueError("empty assistant reply")
        reason = "tool_use" if any(b["type"] == "tool_use" for b in content) else (
            "max_tokens" if choice.get("finish_reason") == "length" else "end_turn")
        usage = data.get("usage") or {}
        return {"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant", "model": model,
                "content": content, "stop_reason": reason, "stop_sequence": None,
                "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}}
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise GatewayError("Invalid upstream reply: expected text or valid JSON function arguments.", 502) from error


class Gateway:
    protocol = "openai"
    auth_mode = "bearer"
    def __init__(self, base_url, model, api_key, token, max_tokens=4096, timeout=180, extra_body=None):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise GatewayError("API URL must use HTTPS (HTTP permitted only for local tests).")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("Use a plain API base URL without embedded credentials/query/fragment.")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.token = model, api_key, token
        self.max_tokens, self.timeout = max_tokens, timeout
        self.extra_body = extra_body or {}
        if set(self.extra_body) & {"model", "messages", "tools", "tool_choice", "stream", "max_tokens"}:
            raise GatewayError("extra_body cannot override routing, messages, tools or output limits.")

    def complete(self, body):
        payload = {**to_openai(body, self.model, self.max_tokens), **self.extra_body}
        request = urllib.request.Request(self.url, json.dumps(payload, ensure_ascii=False).encode(),
            {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key}, method="POST")
        try:
            # Never forward credentials to redirects to another endpoint.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            with urllib.request.build_opener(NoRedirect).open(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            status = error.code if error.code in {400, 401, 403, 404, 429} else 502
            raise GatewayError(f"Upstream HTTP {error.code}; check URL, model, key, tool support and quota. Response body omitted.", status) from error
        except (OSError, ValueError) as error:
            raise GatewayError("Upstream connection failed, timed out, or returned invalid JSON.", 502) from error
        return from_openai(data, self.model)

    def serve(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # No prompts, headers, keys or tool results in HTTP logs.

            def reply(self, status, value):
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authorized(self):
                value = self.headers.get("Authorization", "").removeprefix("Bearer ")
                return hmac.compare_digest(value, gateway.token) or hmac.compare_digest(
                    self.headers.get("x-api-key", ""), gateway.token)

            def do_HEAD(self):
                self.send_response(200 if self.path == "/api/hello" else 404)
                self.end_headers()

            def do_GET(self):
                if not self.authorized():
                    return self.reply(401, {"error": "local gateway token required"})
                if self.path == "/v1/models":
                    return self.reply(200, {"data": [{"id": gateway.model, "type": "model", "display_name": gateway.model}], "has_more": False})
                self.reply(404, {"error": "not found"})

            def event(self, name, value):
                self.wfile.write(("event: " + name + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()

            def do_POST(self):
                if not self.authorized():
                    return self.reply(401, {"error": {"type": "authentication_error", "message": "Invalid local token"}})
                if urllib.parse.urlsplit(self.path).path != "/v1/messages":
                    return self.reply(404, {"error": {"type": "not_found_error", "message": "Endpoint not implemented"}})
                streaming = False
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 8_000_000:
                        raise GatewayError("Invalid request size.")
                    body = json.loads(self.rfile.read(length))
                    to_openai(body, gateway.model, gateway.max_tokens)  # Validate before SSE headers.
                    if not body.get("stream"):
                        return self.reply(200, gateway.complete(body))
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    streaming = True
                    self.event("message_start", {"type": "message_start", "message": {
                        "id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant", "model": gateway.model,
                        "content": [], "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0}}})
                    results = queue.Queue()
                    def work():
                        try:
                            results.put(gateway.complete(body))
                        except Exception as error:
                            results.put(error)
                    threading.Thread(target=work, daemon=True).start()
                    while True:
                        try:
                            result = results.get(timeout=3)
                            break
                        except queue.Empty:
                            self.event("ping", {"type": "ping"})
                    if isinstance(result, Exception):
                        raise result
                    for index, block in enumerate(result["content"]):
                        start = {**block, "text": ""} if block["type"] == "text" else {**block, "input": {}}
                        self.event("content_block_start", {"type": "content_block_start", "index": index, "content_block": start})
                        delta = {"type": "text_delta", "text": block["text"]} if block["type"] == "text" else {
                            "type": "input_json_delta", "partial_json": json.dumps(block["input"], ensure_ascii=False)}
                        self.event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
                        self.event("content_block_stop", {"type": "content_block_stop", "index": index})
                    self.event("message_delta", {"type": "message_delta", "delta": {
                        "stop_reason": result["stop_reason"], "stop_sequence": None}, "usage": result["usage"]})
                    self.event("message_stop", {"type": "message_stop"})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as error:
                    known = isinstance(error, GatewayError)
                    value = {"type": "error", "error": {"type": "api_error", "message": str(error) if known else "Invalid gateway request or response."}}
                    if streaming:
                        self.event("error", value)
                    else:
                        self.reply(error.status if known else 400, value)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


class AnthropicEndpoint(Gateway):
    """Native Messages endpoint: no local conversion server is started."""
    protocol = "anthropic"
    auth_mode = "api_key"

    def __init__(self, base_url, *args, **kwargs):
        super().__init__(base_url, *args, **kwargs)
        self.url = base_url.rstrip("/")
        if self.extra_body:
            raise GatewayError("extra_body is only supported by the OpenAI adapter; native Claude requests go directly to the provider.")

    def complete(self, body):
        payload = {**body, "model": self.model, "stream": False}
        payload["max_tokens"] = min(int(body.get("max_tokens", self.max_tokens)), self.max_tokens)
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        headers["Authorization" if self.auth_mode == "bearer" else "x-api-key"] = (
            "Bearer " + self.api_key if self.auth_mode == "bearer" else self.api_key)
        request = urllib.request.Request(self.url + "/v1/messages", json.dumps(payload, ensure_ascii=False).encode(), headers, method="POST")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=self.timeout) as response:
                value = json.load(response)
            if not isinstance(value.get("content"), list) or not value["content"]:
                raise ValueError("empty message")
            return value
        except urllib.error.HTTPError as error:
            raise GatewayError(f"Native Messages HTTP {error.code}; check endpoint/model/key/tool support. Body omitted.", error.code) from None
        except (OSError, ValueError, TypeError):
            raise GatewayError("Native Messages API connection failed or returned an invalid message.", 502) from None


def connect_claude(endpoint):
    if endpoint.protocol in {"anthropic", "login"}:
        return None, endpoint.url, endpoint.api_key
    server = endpoint.serve()
    return server, f"http://127.0.0.1:{server.server_port}", endpoint.token
