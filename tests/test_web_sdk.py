"""Opt-in: the SDK runtime with the pinned Claude Code and a local fake model (no API key).

A confirmation card round trip, a refused variant form, a multiple-choice question whose answer reaches the model,
a session resumed after a restart, and stopping a turn while a card waits. Set MLAB_RUN_CLAUDE_TESTS=1.
"""
import asyncio
import json
import os
import sys
import pytest
from mlab.core.io import ROOT, tool_path
from mlab.core.workspace import initialize
from mlab.web.agent import TaskRuntime
from mlab.web.bus import Bus
from mlab.web.sdk_client import factory

sys.path.insert(0, str(ROOT / "evals"))
pytestmark = pytest.mark.skipif(os.environ.get("MLAB_RUN_CLAUDE_TESTS") != "1", reason="Set MLAB_RUN_CLAUDE_TESTS=1; runs the pinned Claude Code")

QUESTION = {"question": "Which one first?", "header": "Pick", "multiSelect": False,
            "options": [{"label": "c16", "description": "concurrency 16"}, {"label": "c20", "description": "margin 3%"}]}


def latest_prompt(messages):
    for message in reversed(messages):
        if message["role"] == "user":
            content = message.get("content")
            return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return ""


def script(messages):
    from fake_model import tool_call, tool_results
    prompt, last = latest_prompt(messages), messages[-1]
    if "third turn" in prompt:
        return {"content": "Stopped."} if last["role"] == "tool" else tool_call("Bash", command="mlab conclude", description="conclude")
    if "second turn" in prompt:
        earlier = any("begin" in str(message.get("content")) for message in messages if message["role"] == "user")
        return {"content": f"second turn: remembers the first={earlier}"}
    steps = [tool_call("Bash", command="mlab next", description="state"), tool_call("Bash", command="mlab conclude", description="conclude"),
             tool_call("Bash", command="mlab --workspace . conclude", description="variant"), tool_call("AskUserQuestion", questions=[QUESTION])]
    count = tool_results(messages)
    return steps[count] if count < len(steps) else {"content": "Got: " + str(last.get("content"))[:300]}


async def scenario(workspace, env):
    runtime = TaskRuntime("9.2", workspace, Bus(), factory(env=env), opening=False)

    async def card(kind):
        for _ in range(900):
            pending = [item for item in runtime.cards.values() if item["kind"] == kind]
            if pending:
                return pending[0]
            await asyncio.sleep(.1)
        raise AssertionError(f"no {kind} card: {runtime.snapshot()} {runtime.chat.read()[-4:]}")

    async def idle():
        for _ in range(1800):
            if not runtime.busy() and runtime.status in {"idle", "offline", "error"}:
                return
            await asyncio.sleep(.1)
        raise AssertionError(runtime.snapshot())

    try:
        await runtime.say("begin")
        approval = await card("approval")
        assert approval["data"]["summary"]["title"] == "Submit the conclusion" and approval["data"]["command"] == "mlab conclude"
        runtime.resolve(approval["id"], {"allow": True})
        question = await card("question")
        assert question["data"]["questions"][0]["question"] == "Which one first?"
        runtime.resolve(question["id"], {"answers": {"Which one first?": "c20"}})
        await idle()
        chat = runtime.chat.read()
        assert runtime.status == "idle", (runtime.snapshot(), chat[-3:])
        blocked = [entry for entry in chat if entry["role"] == "notice" and entry.get("to") == "student"]
        assert any("--workspace" in entry["text"] and "standard form" in entry.get("reason", "") for entry in blocked), blocked
        assert chat[-1]["role"] == "agent" and "c20" in chat[-1]["text"], chat[-1]
        assert runtime.meta["session"] and runtime.meta["turns"] == 1
        await runtime.restart()
        await runtime.say("second turn")
        await idle()
        assert runtime.chat.read()[-1]["text"] == "second turn: remembers the first=True"
        await runtime.say("third turn")
        pending = await card("approval")
        await runtime.interrupt()
        await idle()
        entry = next(item for item in runtime.chat.read() if item.get("card_id") == pending["id"])
        assert entry["state"] == "cancelled"
    finally:
        await runtime.stop()


def test_sdk_runtime_cards_questions_resume_and_stop(workspace_path):
    from fake_model import FakeModel, trusted_home
    model = FakeModel(script)
    try:
        workspace = workspace_path / "9.2-sla"
        initialize(workspace, "9.2", "fixture", "claude-haiku")
        env = {**model.environment(trusted_home(workspace_path / "home", workspace)), "PATH": tool_path(), "MLAB_PROMPT_SOURCE": "web",
               "PYTHONUTF8": "1"}
        asyncio.run(scenario(workspace, env))
    finally:
        model.close()
    words = [json.loads(line) for line in (workspace / ".mlab/student.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(entry["source"], entry["text"]) for entry in words] == [("web-chat", "begin"), ("web-question", "Which one first?: c20"),
                                                                    ("web-chat", "second turn"), ("web-chat", "third turn")]
    assert (workspace / ".mlab/web/sdk.jsonl").exists() and "ResultMessage" in (workspace / ".mlab/web/sdk.jsonl").read_text(encoding="utf-8")
