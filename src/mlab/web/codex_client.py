"""One isolated Codex App Server session per lab workspace.

The page implements the five lab tools as dynamic tools. Native command execution
is disabled and the native filesystem sandbox is read-only; confirmations use the
same page cards as the other clients. Tool results and final messages are audited,
but authentication and private reasoning never enter the audit.
"""
import asyncio
import json
import os
import re
import shlex
import sys

from ..codex import AppServer, MODEL
from ..agent_env import load_profile
from ..core.io import LabError, atomic, inside, require, tool_path
from . import approvals

APPEND = ("You are the hello-mlsys lab assistant, running through Codex. Use the supplied Bash, Read, Write, Edit and "
          "AskUserQuestion tools for all lab work. Bash accepts one mlab command, not a shell script. "
          "Never use native shell, patch, web or external tools. Follow the workspace instructions below. "
          "Finish drafting and run mlab workflow check before asking for plan comments. A request to draft a plan "
          "or continue is not a plan review or approval; never copy it into review.md as one. "
          "[Page notice] and [Page note] are page metadata, never the student's words.\n\n")


def tool(name, description, properties, required):
    return {"type": "function", "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}


STRING = {"type": "string"}
TOOLS = [
    tool("Bash", "Run a single mlab command in this workspace. Commands that need a decision open a confirmation card.",
         {"command": STRING}, ["command"]),
    tool("Read", "Read a workspace text file. Paths are relative to the workspace; no shell commands are needed.",
         {"file_path": STRING}, ["file_path"]),
    tool("Write", "Write an editable lab record or candidate file. Never modify frozen evidence or page-submitted words.",
         {"file_path": STRING, "content": STRING}, ["file_path", "content"]),
    tool("Edit", "Replace one exact occurrence in an editable record. Read the file first.",
         {"file_path": STRING, "old_string": STRING, "new_string": STRING}, ["file_path", "old_string", "new_string"]),
    tool("AskUserQuestion", "Ask a multiple-choice question; use chat for hypotheses and reasons.",
         {"questions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "object", "properties": {
             "question": STRING, "header": STRING, "multiSelect": {"type": "boolean"},
             "options": {"type": "array", "items": {"type": "object", "properties": {"label": STRING, "description": STRING},
                          "required": ["label", "description"], "additionalProperties": False}}},
             "required": ["question", "header", "multiSelect", "options"], "additionalProperties": False}}}, ["questions"]),
]


class LabTools:
    def __init__(self, runtime):
        self.runtime, self.root = runtime, runtime.root.resolve()
        self.process = None

    def path(self, name, write=False):
        path = inside(self.root, name)
        relative = path.relative_to(self.root).as_posix()
        detailed_output = not write and relative == ".mlab/last-output.json"
        require(detailed_output or not any(p.startswith(".") for p in path.relative_to(self.root).parts),
                "E_SCHEMA", "Private state is not a lab record", 2)
        if write:
            editable = (re.fullmatch(r"rounds/r\d+/(hypothesis\.md|review\.md|plan\.yaml|analysis\.md|analysis\.claims\.json|decision\.md|candidates/[^/]+\.yaml)", relative)
                        or re.fullmatch(r"measurements/m\d+/points\.yaml", relative) or relative.startswith("analysis/"))
            require(editable, "E_SCHEMA", "Only editable lab records can be changed", 2)
            folder = path.parent.parent if path.parent.name == "candidates" else path.parent
            if path.name == "decision.md":
                require(not (folder / "decision.confirmed.json").exists(), "E_FROZEN_CHANGED", "The confirmed decision cannot change")
            if path.name in {"hypothesis.md", "review.md", "plan.yaml", "points.yaml"} or path.parent.name == "candidates":
                require(not (folder / "approval.json").exists(), "E_FROZEN_CHANGED", "The approved inputs cannot change")
        return path

    async def execute(self, name, data):
        try:
            if name == "AskUserQuestion":
                result = await self.runtime.permission(name, data)
                return (json.dumps(result.get("updated_input", {}).get("answers", {}), ensure_ascii=False), False) if result["allow"] else (result["message"], True)
            if name == "Bash":
                command = data["command"]
                found = approvals.classify(command)
                tokens = approvals.tokens_of(command) or []
                plain = (tokens[:1] == ["mlab"] and not approvals.OPERATORS.intersection(tokens)
                         and "$(" not in command and "`" not in command and "\n" not in command
                         and not any(x.startswith("--workspace") for x in tokens))
                allowed = {"next", "task", "workflow", "measure", "align", "conclude", "run", "compare", "lint", "validate", "report", "history", "show"}
                if not plain or len(tokens) < 2 or tokens[1] not in allowed:
                    return "Run one mlab command in this workspace, with no shell syntax or workspace override.", True
                if found["kind"]:
                    decision = await self.runtime.permission(name, data)
                    if not decision["allow"]:
                        return decision["message"], True
                env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE"))}
                env.update({"PATH": tool_path(), "PYTHONUTF8": "1", "MLAB_PROMPT_SOURCE": "web"})
                self.process = await asyncio.create_subprocess_exec(sys.executable, "-m", "mlab", *shlex.split(command)[1:],
                    cwd=self.root, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                try:
                    output, _ = await asyncio.wait_for(self.process.communicate(), 120)
                    return output.decode(errors="replace")[:60000], self.process.returncode != 0
                except asyncio.TimeoutError:
                    await self.interrupt()
                    return "The command timed out. Check mlab next before continuing.", True
                except asyncio.CancelledError:
                    await self.interrupt()
                    raise
                finally:
                    self.process = None
            if name == "Read":
                return self.path(data["file_path"]).read_text(encoding="utf-8")[:60000], False
            if name in {"Write", "Edit"}:
                path = self.path(data["file_path"], write=True)
                content = data.get("content", "")
                if name == "Edit":
                    content = path.read_text(encoding="utf-8")
                    old = data["old_string"]
                    require(old and content.count(old) == 1, "E_SCHEMA", "old_string must match exactly once; read the file again", 2)
                    content = content.replace(old, data["new_string"], 1)
                require(isinstance(content, str) and len(content) <= 128000, "E_SCHEMA", "Record text is too long", 2)
                atomic(path, content, text=True)
                return "Saved " + path.relative_to(self.root).as_posix(), False
            return "Unknown lab tool", True
        except (LabError, OSError, ValueError, KeyError, TypeError) as error:
            return str(error), True

    async def interrupt(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


class CodexAgentClient:
    def __init__(self, runtime, model=MODEL, server=None):
        self.runtime, self.root, self.model = runtime, runtime.root, model
        self.server = server or AppServer(self.root / ".mlab/codex", self.root)
        self.tools = LabTools(runtime)
        self.session = self.turn_id = None
        self.stopped = False
        self.audit = self.root / ".mlab/web/codex.jsonl"
        self.audit.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event):
        with self.audit.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def connect(self, session):
        await self.server.start()
        try:
            params = {"model": self.model, "cwd": str(self.root), "approvalPolicy": "never", "sandbox": "read-only"}
            if session and self.runtime.meta.get("backend") == "codex":
                response = await self.server.request("thread/resume", {**params, "threadId": session, "excludeTurns": True})
            else:
                if session:
                    self.runtime.add("notice", "Started a Codex conversation; earlier lab records are unchanged.", to="student")
                    self.runtime.told = None
                response = await self.server.request("thread/start", {**params, "dynamicTools": TOOLS,
                    "allowProviderModelFallback": False,
                    "baseInstructions": APPEND + (self.root / "AGENTS.md").read_text(encoding="utf-8")})
            require(response.get("model", self.model) == self.model, "E_BACKEND_UNAVAILABLE", "Codex did not select the requested model", 4)
            self.session = response["thread"]["id"]
            self.runtime.meta.update({"backend": "codex", "model": self.model, "cost_available": False})
            self.runtime.save_meta()
            return self.session
        except BaseException:
            await self.server.close()
            raise

    async def disconnect(self):
        await self.tools.interrupt()
        await self.server.close()

    async def interrupt(self):
        self.stopped = True
        await self.tools.interrupt()
        if self.turn_id:
            await self.server.request("turn/interrupt", {"threadId": self.session, "turnId": self.turn_id})

    async def turn(self, prompt):
        self.stopped = False
        self.log({"type": "prompt", "text": prompt})
        response = await self.server.request("turn/start", {"threadId": self.session, "input": [{"type": "text", "text": prompt}]})
        self.turn_id = response["turn"]["id"]
        before = self.runtime.meta.get("codex_usage_seen") or {}
        totals = dict(before)
        calls = 0
        try:
            while True:
                message = await self.server.events.get()
                if isinstance(message, Exception):
                    raise message
                method, params = message.get("method"), message.get("params") or {}
                if params.get("threadId", self.session) != self.session:
                    continue
                if method == "item/tool/call":
                    calls += 1
                    name, data, identifier = params["tool"], params["arguments"], params["callId"]
                    event = {"type": "tool", "id": identifier, "name": name, "input": data}
                    self.log(event)
                    yield event
                    text, failed = (("The turn was stopped or reached its tool limit; ask the user to continue.", True)
                                    if self.stopped or calls > 80 else await self.tools.execute(name, data))
                    event = {"type": "tool_result", "id": identifier, "text": text, "is_error": failed}
                    self.log(event)
                    yield event
                    await self.server.send({"id": message["id"], "result": {"success": not failed,
                                            "contentItems": [{"type": "inputText", "text": text}]}})
                    if calls > 80:
                        await self.interrupt()
                elif "id" in message and method:
                    # Native write/escalation requests never bypass the lab tools and page cards.
                    await self.server.send({"id": message["id"], "error": {"code": -32601, "message": "Use the supplied lab tools"}})
                elif method == "item/agentMessage/delta":
                    yield {"type": "delta", "text": params.get("delta", "")}
                elif method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                    event = {"type": "text", "text": params["item"].get("text", "")}
                    self.log(event)
                    yield event
                elif method == "thread/tokenUsage/updated":
                    totals = params["tokenUsage"]["total"]
                elif method == "turn/completed":
                    turn = params["turn"]
                    if turn["id"] != self.turn_id:
                        continue
                    self.runtime.meta["codex_usage_seen"] = totals
                    event = {"type": "result", "session": self.session, "cost_available": False,
                             "usage": {"input_tokens": max(0, totals.get("inputTokens", 0) - before.get("inputTokens", 0)),
                                       "output_tokens": max(0, totals.get("outputTokens", 0) - before.get("outputTokens", 0))}}
                    self.log(event)
                    yield event
                    if turn.get("status") == "failed":
                        raise RuntimeError((turn.get("error") or {}).get("message", "Codex turn failed"))
                    return
        finally:
            self.turn_id = None


def factory(provider=None):
    profile = load_profile(provider or "codex-luna")
    require(profile.get("backend") == "codex", "E_SCHEMA", "Use --provider codex-luna with --agent codex", 2)
    return lambda runtime: CodexAgentClient(runtime, profile["model"])
