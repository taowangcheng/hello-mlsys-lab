"""SoC routing, private credentials, shared admission and cancellation."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from mlab.backends.llm import configuration
from mlab.codex import AppServer, VERSION, executable
from mlab.core.io import LabError
from mlab.core.workspace import initialize
from mlab.soclaas import InferenceCancelled, InferenceLease, MODEL
from mlab.web.agent import TaskRuntime
from mlab.web.bus import Bus
from mlab.web.codex_client import CodexAgentClient


def test_soc_model_and_pin_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    monkeypatch.setenv("MLAB_LLM_BASE_URL", "https://unrelated.invalid")
    config = configuration("codex-soc-qwen")
    assert config["model"] == MODEL and config["provider"] == "soclaas" and not config["key"]
    server = AppServer(tmp_path / "state", tmp_path, provider="soclaas")
    overrides = server.configuration()
    assert overrides["model_providers.soclaas.wire_api"] == "responses"
    assert overrides["model_providers.soclaas.env_key"] == "SOCLAAS_API_KEY"
    assert not overrides["model_providers.soclaas.requires_openai_auth"]
    assert overrides["model_providers.soclaas.stream_max_retries"] == 0
    monkeypatch.setenv("SOCLAAS_MODEL", "default")
    with pytest.raises(LabError, match="pins"):
        server.configuration()
    monkeypatch.setattr("mlab.codex.installation", lambda: ("codex", "0.0.0"))
    with pytest.raises(LabError, match=VERSION):
        executable()


def test_shared_lease_waits_cancels_and_recovers(tmp_path):
    async def check():
        first, second = [InferenceLease(tmp_path / "request.lock", interval=0) for _ in range(2)]
        await first.acquire()
        waiting = asyncio.create_task(second.acquire())
        await asyncio.sleep(.15)
        assert not waiting.done()
        second.cancelled = True
        with pytest.raises(InferenceCancelled):
            await waiting
        first.release()
        second.cancelled = False
        await asyncio.wait_for(second.acquire(), 1)
        second.release()
        await first.acquire()
        first.release()
    asyncio.run(check())


def test_soc_tools_release_lease_and_never_use_personal_login(workspace_path, monkeypatch, tmp_path):
    initialize(workspace_path, "9.2", "fixture", provider="codex-soc-qwen")
    monkeypatch.setenv("SOCLAAS_API_KEY", "test-soc-key-not-a-real-secret")
    monkeypatch.setattr("mlab.codex.credentials", lambda: pytest.fail("SoC must not read personal credentials"))
    servers = []
    def factory(runtime):
        server = AppServer(workspace_path / ".mlab/codex", workspace_path, provider="soclaas",
                           command=[sys.executable, str(Path(__file__).with_name("fake_codex_server.py"))])
        server.lease = InferenceLease(tmp_path / "request.lock", interval=0)
        servers.append(server)
        return CodexAgentClient(runtime, MODEL, server, provider="soclaas")
    async def until(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(.01)
    async def check():
        runtime = TaskRuntime("9.2-1", workspace_path, Bus(), factory, opening=False)
        other = InferenceLease(tmp_path / "request.lock", interval=0)
        try:
            await runtime.say("begin")
            await until(lambda: runtime.cards)
            # A pending page decision does not monopolize inference capacity.
            await asyncio.wait_for(other.acquire(), 1)
            other.release()
            await runtime.interrupt()
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle"
            assert runtime.meta["model"] == MODEL and runtime.meta["model_provider"] == "soclaas"
            # Stopping a queued turn must not submit it after the lease is freed.
            await other.acquire()
            await runtime.say("queued prompt")
            await until(lambda: runtime.turning)
            await runtime.interrupt()
            await until(lambda: not runtime.busy())
            other.release()
            history = json.loads((workspace_path / ".mlab/codex/test-session.json").read_text())
            assert not any("queued prompt" in prompt for prompt in history)
            await runtime.say("follow up")
            await until(lambda: not runtime.busy())
            assert runtime.status == "idle"
        finally:
            other.release()
            await runtime.stop()
        assert all(server.lease.stream is None for server in servers)
    asyncio.run(check())
    for path in workspace_path.rglob("*"):
        if path.is_file():
            assert b"test-soc-key-not-a-real-secret" not in path.read_bytes(), path
    assert not (workspace_path / ".mlab/codex/auth.json").exists()
