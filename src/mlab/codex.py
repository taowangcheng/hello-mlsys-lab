"""Codex App Server transport with workspace-local state and in-memory credentials.

Only the child process receives a private CODEX_HOME. The host's login is read-only:
OAuth tokens are supplied through the external-token protocol, never copied to disk
or refreshed by mlab. API keys come only from OPENAI_API_KEY.
"""
import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .core.io import ROOT, load, require, tool_path

MODEL = "gpt-6-luna"
VERSION = load(ROOT / "agent/compatibility.json")["codex"]["pinned_version"]
CONFIG = {
    "cli_auth_credentials_store": "ephemeral",
    "model": MODEL,
    "model_reasoning_effort": "medium",
    "web_search": "disabled",
    "check_for_update_on_startup": False,
    "project_doc_max_bytes": 0,
    "features.apps": False,
    "features.plugins": False,
    "features.hooks": False,
    "features.multi_agent": False,
    "features.shell_tool": False,
    "features.shell_snapshot": False,
    "features.memories": False,
    "features.skill_search": False,
    "features.skip_host_skill_discovery": True,
}


def installation():
    path = os.environ.get("MLAB_CODEX_BIN") or shutil.which("codex", path=tool_path())
    if not path:
        return None, None
    try:
        with tempfile.TemporaryDirectory(prefix="mlab-codex-version-") as directory:
            result = subprocess.run([path, "--version"], env=environment(directory), capture_output=True, text=True, timeout=10)
        version = result.stdout.strip().removeprefix("codex-cli ") if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return path, version


def executable():
    path, version = installation()
    require(version == VERSION, "E_BACKEND_UNAVAILABLE",
            f"Codex {VERSION} is required (found {version or 'none'}). Run scripts/setup.sh.", 4)
    return path


def login_file():
    return Path(os.environ.get("MLAB_CODEX_AUTH_FILE") or
                Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json").expanduser().resolve()


def credentials():
    if os.environ.get("OPENAI_API_KEY"):
        return {"type": "apiKey", "apiKey": os.environ["OPENAI_API_KEY"]}
    try:
        tokens = load(login_file()).get("tokens") or {}
    except (OSError, ValueError):
        tokens = {}
    require(tokens.get("access_token") and tokens.get("account_id"), "E_BACKEND_UNAVAILABLE",
            "Log in with codex login, or set OPENAI_API_KEY. mlab reads the login without changing it.", 4)
    return {"type": "chatgptAuthTokens", "accessToken": tokens["access_token"], "chatgptAccountId": tokens["account_id"]}


def environment(directory):
    """Use CODEX_HOME for its documented child-process state location, without changing the parent's environment."""
    directory = Path(directory).resolve()
    require(directory != login_file().parent, "E_SCHEMA", "The lab Codex directory must differ from the personal Codex directory", 2)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "CLAUDE", "ANTHROPIC_", "MLAB_LLM_", "QWEN", "SOCLAAS_"))
           and k not in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "MLAB_CODEX_AUTH_FILE"}}
    env.update({"CODEX_HOME": str(directory), "PATH": tool_path(), "PYTHONUTF8": "1"})
    return env


class AppServer:
    """Multiplex JSONL requests and notifications, including interruption during a pending tool call."""

    def __init__(self, directory, cwd, command=None, authenticate=True, provider=None):
        self.directory, self.cwd = Path(directory), Path(cwd)
        self.command, self.authenticate = command, authenticate
        self.process = self.reader = self.stderr_reader = None
        self.pending, self.errors = {}, []
        self.events = asyncio.Queue()
        self.serial = 0
        self.last_access_token = None
        self.provider = provider
        self.tool_requests = set()
        self.lease = None
        if provider == "soclaas":
            from .soclaas import InferenceLease
            self.lease = InferenceLease()

    def configuration(self):
        config = dict(CONFIG)
        if self.provider == "soclaas":
            from .soclaas import BASE_URL, MODEL as SOC_MODEL
            base = os.environ.get("SOCLAAS_BASE_URL", BASE_URL).rstrip("/")
            require(base.startswith("https://"), "E_SCHEMA", "SoCLaaS requires an HTTPS API root", 2)
            require(os.environ.get("SOCLAAS_MODEL", SOC_MODEL) == SOC_MODEL, "E_SCHEMA",
                    f"This profile pins {SOC_MODEL}; unset SOCLAAS_MODEL or use that exact model.", 2)
            config.update({"model": SOC_MODEL, "model_provider": "soclaas", "model_reasoning_effort": "low",
                "model_catalog_json": str(ROOT / "agent/soclaas-models.json"),
                "model_providers.soclaas.name": "SoCLaaS", "model_providers.soclaas.base_url": base,
                "model_providers.soclaas.wire_api": "responses", "model_providers.soclaas.env_key": "SOCLAAS_API_KEY",
                "model_providers.soclaas.requires_openai_auth": False,
                "model_providers.soclaas.supports_websockets": False,
                "model_providers.soclaas.request_max_retries": 2,
                "model_providers.soclaas.stream_max_retries": 0,
                "model_providers.soclaas.stream_idle_timeout_ms": 180000})
        return config

    async def start(self):
        env = environment(self.directory)
        if self.provider == "soclaas":
            require(os.environ.get("SOCLAAS_API_KEY"), "E_BACKEND_UNAVAILABLE", "Set SOCLAAS_API_KEY for the SoC profile", 4)
            env["SOCLAAS_API_KEY"] = os.environ["SOCLAAS_API_KEY"]
        args = self.command or [executable(), "app-server", "--stdio"]
        if self.command is None:
            for key, value in self.configuration().items():
                args += ["-c", f"{key}={json.dumps(value)}"]
        self.process = await asyncio.create_subprocess_exec(*args, cwd=self.cwd, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=4 * 1024 * 1024)
        self.reader = asyncio.create_task(self.read())
        self.stderr_reader = asyncio.create_task(self.read_stderr())
        try:
            await self.request("initialize", {"clientInfo": {"name": "mlab", "version": "0.1.0"},
                                               "capabilities": {"experimentalApi": True}})
            await self.send({"method": "initialized", "params": {}})
            if self.authenticate and self.provider != "soclaas":
                auth = credentials()
                self.last_access_token = auth.get("accessToken")
                # Authentication messages never enter the agent audit log.
                await self.request("account/login/start", auth)
        except BaseException:
            await self.close()
            raise

    async def send(self, message):
        if self.lease and message.get("id") in self.tool_requests and "method" not in message:
            await self.lease.acquire()
            self.tool_requests.discard(message["id"])
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params, timeout=60):
        if self.lease and method == "turn/start":
            self.lease.cancelled = False
            await self.lease.acquire()
        self.serial += 1
        identifier = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            await self.send({"id": identifier, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except BaseException:
            if self.lease and method == "turn/start":
                self.lease.release()
            raise
        finally:
            self.pending.pop(identifier, None)

    async def read_stderr(self):
        async for line in self.process.stderr:
            self.errors = (self.errors + [line.decode(errors="replace").strip()])[-10:]

    async def read(self):
        error = None
        try:
            async for line in self.process.stdout:
                message = json.loads(line)
                if self.lease and message.get("method") in {"item/tool/call", "turn/completed"}:
                    self.lease.release()
                    if message["method"] == "item/tool/call":
                        self.tool_requests.add(message["id"])
                if "method" not in message and message.get("id") in self.pending:
                    future = self.pending[message["id"]]
                    if not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(message["error"].get("message", "Codex request failed")))
                        else:
                            future.set_result(message.get("result") or {})
                elif message.get("method") == "account/chatgptAuthTokens/refresh" and self.provider != "soclaas":
                    # The personal Codex owns refresh. Re-read only if it has already renewed the token.
                    try:
                        auth = credentials()
                        require(auth.get("accessToken") and auth["accessToken"] != self.last_access_token,
                                "E_BACKEND_UNAVAILABLE", "Codex login expired; renew it with codex login, then reconnect.", 4)
                        previous = message.get("params", {}).get("previousAccountId")
                        require(not previous or previous == auth["chatgptAccountId"], "E_BACKEND_UNAVAILABLE",
                                "The Codex account changed; restart the lab agent.", 4)
                        self.last_access_token = auth["accessToken"]
                        await self.send({"id": message["id"], "result": {k: v for k, v in auth.items() if k != "type"}})
                    except Exception:
                        await self.send({"id": message["id"], "error": {"code": -32000,
                            "message": "Renew the Codex login outside mlab, then reconnect. The personal login was not changed."}})
                else:
                    await self.events.put(message)
        except (OSError, ValueError) as exc:
            error = str(exc)
        finally:
            if self.lease:
                self.lease.release()
            error = RuntimeError(error or "; ".join(self.errors[-2:]) or "Codex App Server disconnected")
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            await self.events.put(error)

    async def close(self):
        if self.lease:
            self.lease.cancelled = True
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.lease:
            self.lease.release()
        for task in (self.reader, self.stderr_reader):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
