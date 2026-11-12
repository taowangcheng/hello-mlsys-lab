"""Scripted App Server protocol peer for offline lifecycle tests; never a real model."""
import json
import os
import sys
from pathlib import Path

state = Path(os.environ["CODEX_HOME"])
saved = state / "test-session.json"
history = json.loads(saved.read_text()) if saved.exists() else []
session = "test-codex-session"
turn = None
steps = []
answers = []
counter = 0


def send(message):
    print(json.dumps(message), flush=True)


def finish(status="completed"):
    send({"method": "thread/tokenUsage/updated", "params": {"threadId": session,
         "tokenUsage": {"total": {"inputTokens": len(history) * 10, "outputTokens": len(history) * 5}}}})
    send({"method": "turn/completed", "params": {"threadId": session, "turn": {"id": turn, "status": status}}})


def advance():
    global counter
    if steps:
        name, args = steps.pop(0)
        counter += 1
        send({"id": f"call-{counter}", "method": "item/tool/call", "params": {
            "threadId": session, "turnId": turn, "callId": f"tool-{counter}", "tool": name, "arguments": args}})
    else:
        text = "Remembers begin: " + str(any("begin" in text for text in history)) + "; " + str(answers[-1:] or [])
        send({"method": "item/agentMessage/delta", "params": {"threadId": session, "delta": text}})
        send({"method": "item/completed", "params": {"threadId": session, "item": {"type": "agentMessage", "text": text}}})
        finish()


for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        send({"id": message["id"], "result": {"userAgent": "offline-protocol-peer"}})
    elif method == "account/login/start":
        assert params.get("accessToken") or params.get("apiKey")
        send({"id": message["id"], "result": {"type": params["type"]}})
    elif method in {"thread/start", "thread/resume"}:
        assert params["model"] == "gpt-6-luna" and params["sandbox"] == "read-only"
        if method == "thread/start":
            assert {x["name"] for x in params["dynamicTools"]} == {"Bash", "Read", "Write", "Edit", "AskUserQuestion"}
            assert all(x["type"] == "function" for x in params["dynamicTools"])
        else:
            assert params["threadId"] == session and params["excludeTurns"]
        send({"id": message["id"], "result": {"thread": {"id": session}}})
    elif method == "turn/start":
        prompt = params["input"][0]["text"]
        if "crash" in prompt:
            sys.exit(23)
        history.append(prompt)
        saved.write_text(json.dumps(history))
        turn = f"turn-{len(history)}"
        send({"id": message["id"], "result": {"turn": {"id": turn}}})
        if "begin" in prompt:
            steps = [("Bash", {"command": "mlab next"}), ("Bash", {"command": "mlab conclude"}),
                     ("Bash", {"command": "mlab --workspace . conclude"}),
                     ("AskUserQuestion", {"questions": [{"question": "Which candidate?", "header": "Pick", "multiSelect": False,
                         "options": [{"label": "c16", "description": "More margin"}, {"label": "c20", "description": "Less margin"}]}]})]
        elif "stop" in prompt:
            steps = [("Bash", {"command": "mlab conclude"})]
        else:
            steps = []
        advance()
    elif method == "turn/interrupt":
        send({"id": message["id"], "result": {}})
        finish("interrupted")
    elif method is None and str(message.get("id", "")).startswith("call-"):
        answers.append(message.get("result"))
        if "stop" not in history[-1]:
            advance()
