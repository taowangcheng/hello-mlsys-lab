"""Offline checks for Codex isolation, tool boundaries, approvals, resume and interruption."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mlab.codex import AppServer, MODEL, credentials, environment
from mlab.cli import parser
from mlab.core.io import LabError, load
from mlab.core.workspace import initialize
from mlab.web.agent import TaskRuntime
from mlab.web.bus import Bus
from mlab.web.codex_client import CodexAgentClient, LabTools, factory


def make_runtime(root, authenticate=False):
    def client(runtime):
        server = AppServer(root / ".mlab/codex", root,
                           command=[sys.executable, str(Path(__file__).with_name("fake_codex_server.py"))], authenticate=authenticate)
        return CodexAgentClient(runtime, server=server)
    return TaskRuntime("9.2-1", root, Bus(), client, opening=False)


async def until(predicate):
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(.01)


def test_default_workspace_and_cli_use_codex(workspace_path):
    result = initialize(workspace_path, "9.2", "fixture")
    assert load(workspace_path / ".mlab/config.json")["provider"] == "codex-luna"
    assert "./codex" in result["next_actions"][0]["say"]
    assert (workspace_path / "codex").is_file() and not (workspace_path / ".claude").exists()
    assert parser().parse_args(["web"]).agent == "codex"
    with pytest.raises(LabError, match="codex-luna"):
        factory("claude-haiku")


def test_login_is_read_only_and_credentials_stay_in_memory(tmp_path, monkeypatch, workspace_path):
    personal = tmp_path / "personal"
    personal.mkdir()
    auth = personal / "auth.json"
    original = json.dumps({"tokens": {"access_token": "test-oauth-secret", "account_id": "test-account"}})
    auth.write_text(original)
    (personal / "config.toml").write_text('model = "personal-model"\n')
    monkeypatch.setenv("CODEX_HOME", str(personal))
    monkeypatch.delenv("MLAB_CODEX_AUTH_FILE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-secret")
    monkeypatch.setenv("CODEX_THREAD_ID", "personal-thread")
    env = environment(tmp_path / "private")
    assert os.environ["CODEX_HOME"] == str(personal)
    assert env["CODEX_HOME"] == str(tmp_path / "private")
    assert "ANTHROPIC_API_KEY" not in env and "CODEX_THREAD_ID" not in env
    assert credentials() == {"type": "chatgptAuthTokens", "accessToken": "test-oauth-secret", "chatgptAccountId": "test-account"}
    with pytest.raises(LabError):
        environment(personal)
    initialize(workspace_path, "9.2", "fixture")
    async def check():
        runtime = make_runtime(workspace_path, authenticate=True)
        try:
            await runtime.say("hello")
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle"
        finally:
            await runtime.stop()
    asyncio.run(check())
    assert auth.read_text() == original
    assert (personal / "config.toml").read_text() == 'model = "personal-model"\n'
    assert not (workspace_path / ".mlab/codex/auth.json").exists()
    for path in workspace_path.rglob("*"):
        if path.is_file():
            assert b"test-oauth-secret" not in path.read_bytes(), path
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-secret")
    assert credentials() == {"type": "apiKey", "apiKey": "test-api-secret"}
    assert "OPENAI_API_KEY" not in environment(tmp_path / "private")


def test_runtime_approvals_questions_resume_usage_and_stop(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    async def check():
        runtime = make_runtime(workspace_path)
        try:
            await runtime.say("begin")
            await until(lambda: runtime.cards)
            card = next(iter(runtime.cards.values()))
            assert card["kind"] == "approval" and card["data"]["command"] == "mlab conclude"
            runtime.resolve(card["id"], {"allow": False, "reason": "Need more evidence"})
            await until(lambda: any(c["kind"] == "question" for c in runtime.cards.values()))
            card = next(c for c in runtime.cards.values() if c["kind"] == "question")
            runtime.resolve(card["id"], {"answers": {"Which candidate?": "c16"}})
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle" and "c16" in runtime.chat.read()[-1]["text"]
            assert runtime.meta["input_tokens"] == 10 and runtime.meta["cost_available"] is False
            assert any(e.get("is_error") for e in runtime.chat.read() if "--workspace" in e["text"])
            await runtime.stop()
            runtime = make_runtime(workspace_path)
            await runtime.say("second turn")
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle" and "Remembers begin: True" in runtime.chat.read()[-1]["text"]
            assert runtime.meta["input_tokens"] == 20 and runtime.meta["output_tokens"] == 10
            await runtime.say("stop")
            await until(lambda: runtime.cards)
            await runtime.interrupt()
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle" and not runtime.cards
            assert any(e.get("state") == "cancelled" for e in runtime.chat.read())
        finally:
            await runtime.stop()
    asyncio.run(check())
    rows = [json.loads(x) for x in (workspace_path / ".mlab/student.jsonl").read_text().splitlines()]
    assert [x["text"] for x in rows] == ["begin", "Need more evidence", "Which candidate?: c16", "second turn", "stop"]


def test_app_server_exit_unblocks_the_runtime(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    async def check():
        runtime = make_runtime(workspace_path)
        try:
            await runtime.say("crash")
            await until(lambda: runtime.status == "error")
            assert not runtime.busy() and not runtime.client
            assert runtime.chat.read()[-1]["role"] == "error"
        finally:
            await runtime.stop()
    asyncio.run(check())


def test_approved_command_runs_only_after_the_card_is_confirmed(workspace_path):
    from test_workflow import ready, REVIEW
    from mlab.core.io import atomic
    lab = ready(workspace_path)
    try:
        digest = lab.check()["plan_hash"]
        atomic(lab.folder() / "review.md", REVIEW, text=True)
        approved = lab.folder() / "approval.json"
    finally:
        lab.close()
    async def check():
        runtime = make_runtime(workspace_path)
        tools = LabTools(runtime)
        pending = asyncio.create_task(tools.execute("Bash", {"command": f"mlab workflow approve --plan {digest}"}))
        await until(lambda: runtime.cards)
        assert not approved.exists() and not pending.done()
        card = next(iter(runtime.cards.values()))
        runtime.resolve(card["id"], {"allow": True})
        output, failed = await pending
        assert not failed and json.loads(output)["status"] == "approved" and approved.exists()
        assert (await tools.execute("Write", {"file_path": "rounds/r001/plan.yaml", "content": "changed"}))[1]
    asyncio.run(check())


def test_tools_use_real_cli_and_protect_records(workspace_path, tmp_path):
    initialize(workspace_path, "9.2", "fixture")
    async def check():
        runtime = make_runtime(workspace_path)
        tools = LabTools(runtime)
        output, failed = await tools.execute("Bash", {"command": "mlab workflow new"})
        assert not failed and json.loads(output)["ok"]
        for command in ["mlab next; mlab conclude", "mlab --workspace /tmp next", "mlab demo --out /tmp/demo", "cat task/task.yaml"]:
            assert (await tools.execute("Bash", {"command": command}))[1]
        for name in ["task/task.yaml", "rounds/r001/state.yaml", ".mlab/config.json", "../outside.md"]:
            assert (await tools.execute("Write", {"file_path": name, "content": "changed"}))[1]
        outside = tmp_path / "outside"
        outside.write_text("unchanged")
        (workspace_path / "analysis").mkdir(exist_ok=True)
        (workspace_path / "analysis/link.md").symlink_to(outside)
        assert (await tools.execute("Write", {"file_path": "analysis/link.md", "content": "changed"}))[1]
        assert outside.read_text() == "unchanged"
        assert not (await tools.execute("Write", {"file_path": "analysis/note.md", "content": "first"}))[1]
        assert not (await tools.execute("Edit", {"file_path": "analysis/note.md", "old_string": "first", "new_string": "second"}))[1]
        assert await tools.execute("Read", {"file_path": "analysis/note.md"}) == ("second", False)
        assert (await tools.execute("Edit", {"file_path": "analysis/note.md", "old_string": "missing", "new_string": "third"}))[1]
    asyncio.run(check())


def test_llm_default_and_mock_cache_use_codex(monkeypatch, workspace_path):
    from mlab.backends import llm
    for name in list(os.environ):
        if name.startswith(("MLAB_LLM_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    assert llm.configuration()["protocol"] == "codex" and llm.configuration()["model"] == MODEL
    monkeypatch.setenv("ANTHROPIC_MODEL", "haiku")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://legacy.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-secret")
    assert llm.configuration()["protocol"] == "codex" and llm.configuration()["model"] == MODEL
    calls = []
    data = {"requests": [{"request_id": "r1", "ttft_ms": 10, "tpot_ms": 2, "status": "completed"}], "vram_peak_gib": 3, "kv_peak_gib": 1}
    def answer(config, instruction, timeout, directory):
        calls.append(config)
        return json.dumps(data)
    monkeypatch.setattr(llm, "ask_codex", answer)
    first, provenance = llm.estimate({}, [{"request_id": "r1"}], workspace_path / "cache")
    second, cached = llm.estimate({}, [{"request_id": "r1"}], workspace_path / "cache")
    assert first == second == data and len(calls) == 1 and cached["cached"] and provenance["model"] == MODEL
