"""One isolated Codex App Server session per lab workspace.

The page implements the five lab tools as dynamic tools. Native command execution
is disabled and the native filesystem sandbox is read-only; confirmations use the
same page cards as the other clients. Tool results and final messages are audited,
but authentication and private reasoning never enter the audit.
"""
import asyncio
import copy
import json
import os
import re
import shlex
import sys

from ..codex import AppServer, MODEL
from ..agent_env import load_profile
from ..core.io import LabError, atomic, hashed, inside, require, tool_path
from ..soclaas import InferenceCancelled
from . import approvals

APPEND = ("You are the hello-mlsys lab assistant, running through Codex. Use the supplied Bash, Read, Write, Edit and "
          "AskUserQuestion tools for all lab work. Bash accepts one mlab command, not a shell script. "
          "Never use native shell, patch, web or external tools. Follow the workspace instructions below. "
          "Finish drafting and run mlab workflow check before asking for plan comments. A request to draft a plan "
          "or continue is not a plan review or approval; never copy it into review.md as one. "
          "[Page notice] and [Page note] are page metadata, never the student's words.\n\n")
APPEND += ("In conversation, use short, natural sentences in the student's language. Explain the experiment, evidence and next step. "
           "Keep file paths, hashes, tool names and internal workflow mechanics out of explanations unless asked. "
           "Say 'prediction before measurement' instead of 'frozen prediction'. Never describe page actions as sending messages to an agent. "
           "Clearly identify demo data as synthetic; do not imply a real GPU was measured.\n\n")
APPEND += ("Write prose in plans, candidate rationales and analyses in the same language as the student's message. "
           "Keep schema keys and required template headings unchanged. Quote YAML strings containing colons.\n\n")

EXPLORATION = ("You are the hello-mlsys experiment assistant. The student controls their experiments in a task workbench. "
    "There is no mandatory sequence of hypothesis, review, decision and next step. Help with the question the student asked. "
    "Use Explore to list evidence, inspect an experiment, or create an editable draft. A draft is a proposal; it never runs automatically. "
    "Use Read and read-only mlab commands for deeper evidence. Never approve, execute, cancel or conclude an experiment yourself. "
    "The student uses the workbench buttons to run simulations, confirm measurements and submit conclusions. "
    "Never rewrite a student's observation or claim that your proposed expectation is their own. "
    "For a request to use unchanged task examples, call Explore(action='list') once, then "
    "Explore(action='draft', template_ids=[the requested example IDs], title=the requested name). "
    "This copies the exact example settings; do not reconstruct them, search files, or invent CLI commands. "
    "For a controlled variant, use Explore(action='draft', title=..., variants=[{template_id:'baseline', id:'c16', "
    "changes:[{field:'serving.max_concurrency', value:'16'}]}]). For deployment changes use a zero-based index, "
    "for example field='deployments.0.weight_precision', value='INT4'. An unchanged variant has changes=[]. "
    "Prefer template_ids or variants over draft_json whenever starting from examples. "
    "For a custom configuration, copy a complete candidate object from templates or an earlier experiment, "
    "change only the requested variables, and pass valid JSON in draft_json with title, question, candidates, seeds and workloads. "
    "Each candidate must retain schema, id, infra, rationale and strategy, including all required deployment fields. "
    "Supply only one of template_ids, variants or draft_json. The examples and available workloads are already in the list response. "
    "Use Explore(action='show', reference='session/record') for existing records. No Bash command is needed to create a draft. "
    "If deeper inspection is needed, supported commands include 'mlab task show', 'mlab history' and 'mlab run show sim_0001'. "
    "There is no shell, 'mlab list', 'mlab workloads', or 'mlab experiment' command. "
    "Discuss the current selection explicitly. Cite run IDs for numerical claims and distinguish observations from possible explanations. "
    "Before claiming a workload or validation is missing, list the task's evidence and inspect all relevant measurements; "
    "one selected pair or measurement group does not describe the whole experiment. "
    "Speak naturally in the student's language, keep replies focused, and hide command names and paths unless asked. "
    "Synthetic simulator and stand-in data are demonstration evidence, never real GPU measurements. "
    "Ask clarifying questions in ordinary chat. Do not demand a hypothesis or formal decision before helping.\n")


def tool(name, description, properties, required):
    return {"type": "function", "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}


STRING = {"type": "string"}
TOOLS = [
    tool("Explore", "List task templates and evidence, show a record, or create an editable draft without running it. "
         "For unchanged examples use action=draft, template_ids=[requested IDs], title=experiment name. "
         "For parameter changes use variants=[{template_id,id,changes:[{field,value}]}]; values are text. "
         "For custom settings use draft_json, copying complete candidate objects from list.templates; do not omit infra or rationale.",
         {"action": {"type": "string", "enum": ["list", "show", "draft"]},
          "reference": {"type": "string", "description": "For show: qualified record such as 9.1-1/r001 or 9.1-1/sim_0001."},
          "template_ids": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 12,
                           "description": "For draft: IDs from list.templates to copy exactly, in order. Prefer this for unchanged examples."},
          "variants": {"type": "array", "minItems": 1, "maxItems": 12,
                       "description": "For draft: copy named templates, rename configurations, and apply only the listed changes. Prefer this for controlled comparisons.",
                       "items": {"type": "object", "properties": {
                           "template_id": STRING, "id": STRING, "rationale": STRING,
                           "changes": {"type": "array", "items": {"type": "object", "properties": {
                               "field": {"type": "string", "description": "Task parameter path, e.g. serving.max_concurrency or deployments.0.weight_precision."},
                               "value": {"type": "string", "description": "New value as text, e.g. 16, INT4, true."}},
                               "required": ["field", "value"], "additionalProperties": False}}},
                           "required": ["template_id", "id", "changes"], "additionalProperties": False}},
          "title": {"type": "string", "description": "Name for a draft created from template_ids or variants."},
          "draft_json": {"type": "string", "description": "For custom drafts only: valid JSON object with title, question, candidates, seeds, workloads. Each candidate retains schema, id, infra, rationale, strategy from list.templates. Do not also supply template_ids."}}, ["action"]),
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


def example_draft(root, data):
    """Clone complete examples and restrict structured changes to task parameters."""
    from ..core.io import ROOT, load
    from ..core.workspace import TASKS
    task = load(root / "task/task.yaml")
    templates = {item["id"]: item for item in (load(path) for path in
                 (ROOT / "examples" / TASKS[task["id"]] / "candidates").glob("*.yaml"))}
    if "template_ids" in data:
        identifiers = data["template_ids"]
        require(isinstance(identifiers, list) and identifiers and all(isinstance(item, str) and item in templates for item in identifiers),
                "E_SCHEMA", "Choose template IDs from Explore list.templates", 2)
        variants = [{"template_id": identifier, "id": identifier, "changes": []} for identifier in identifiers]
    else:
        variants = data["variants"]
    require(isinstance(variants, list) and 1 <= len(variants) <= 12, "E_SCHEMA", "Choose 1 to 12 variants", 2)
    candidates = []
    for variant in variants:
        require(isinstance(variant, dict) and variant.get("template_id") in templates,
                "E_SCHEMA", "Each variant must name a template from Explore list.templates", 2)
        candidate = copy.deepcopy(templates[variant["template_id"]])
        candidate["id"] = variant["id"]
        changes = variant.get("changes", [])
        require(isinstance(changes, list), "E_SCHEMA", "Variant changes must be a list", 2)
        for change in changes:
            require(isinstance(change, dict) and isinstance(change.get("field"), str) and isinstance(change.get("value"), str),
                    "E_SCHEMA", "Each change needs a parameter field and text value", 2)
            parts = change["field"].split(".")
            rule_path = ".".join([parts[0], *parts[2:]]) if parts[0] == "deployments" and len(parts) > 2 and parts[1].isdigit() else change["field"]
            require(rule_path in task["decision_space"], "E_OUT_OF_SPACE", f"{change['field']} is not configurable in this task")
            target = candidate["strategy"]
            for part in parts[:-1]:
                if isinstance(target, list):
                    require(part.isdigit() and int(part) < len(target), "E_SCHEMA", "Choose an existing deployment index", 2)
                    target = target[int(part)]
                else:
                    require(isinstance(target, dict), "E_SCHEMA", "Invalid parameter path", 2)
                    target = target.setdefault(part, {})
            require(isinstance(target, dict), "E_SCHEMA", "Include the deployment index in the parameter path", 2)
            try:
                value = json.loads(change["value"])
            except ValueError:
                value = change["value"]
            target[parts[-1]] = value
        if variant.get("rationale"):
            candidate["rationale"] = variant["rationale"]
        elif changes:
            candidate["rationale"] = f"A variation of {variant['template_id']} for comparison."
        candidates.append(candidate)
    return {"title": data.get("title") or "Experiment draft", "candidates": candidates,
            "seeds": [42], "workloads": ["development"]}


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
            exploratory = getattr(self.runtime, "exploration", False)
            if name == "Explore":
                require(exploratory, "E_SCHEMA", "Open the exploration workbench to propose experiment drafts")
                from . import exploration, views
                from .course import Course
                from .server import read
                if data["action"] == "draft":
                    require(sum(key in data for key in ("template_ids", "variants", "draft_json")) == 1,
                            "E_SCHEMA", "Supply exactly one of template_ids, variants or draft_json", 2)
                    if "template_ids" in data or "variants" in data:
                        draft = example_draft(self.root, data)
                    else:
                        draft = json.loads(data["draft_json"])
                    result = await asyncio.to_thread(exploration.action, self.root, "create", draft, "assistant")
                    self.runtime.bus.publish("records.changed", self.runtime.key, action="create")
                elif data["action"] == "show":
                    reference = str(data["reference"])
                    session, _, identifier = reference.partition("/")
                    target = self.root
                    sessions = {item["id"]: item for item in Course(self.root.parent).sessions()}
                    if session in sessions:
                        require(sessions[session]["task"] == self.root.name.split("-")[0], "E_SCHEMA", "Choose evidence from this task")
                        target, reference = sessions[session]["root"], identifier
                    result = await asyncio.to_thread(read, target, views.record, reference)
                else:
                    from ..core.io import load
                    task = load(self.root / ".mlab/config.json")["task"]
                    result = await asyncio.to_thread(exploration.task_view, Course(self.root.parent), task)
                    result["runs"] = result["runs"][-30:]
                    result["experiments"] = result["experiments"][-20:]
                    result = {"task": result["task"], "templates": result["templates"],
                              "draft_help": {"unchanged_examples": {"action": "draft", "template_ids": [result["templates"][0]["id"]], "title": "Your experiment name"},
                                             "controlled_variants": "Use variants=[{template_id: an example ID, id: a new configuration ID, changes: [{field: a task parameter path, value: the new value as text}]}]. Deployment paths include an index, e.g. deployments.0.weight_precision.",
                                             "custom_settings": "Copy complete candidate objects from templates into draft_json. Retain schema, id, infra, rationale and strategy. Use valid JSON; no comments or trailing commas.",
                                             "workloads": ["development", *result["overview"]["holdouts"]],
                                             "note": "The draft remains editable. The student chooses workloads and execution. No CLI or file discovery is needed."},
                              **{key: value for key, value in result.items() if key not in {"task", "templates"}}}
                return json.dumps(result, ensure_ascii=False)[:60000], False
            if exploratory and name in {"Write", "Edit", "AskUserQuestion"}:
                return "Use Explore to propose an editable experiment draft, and ask questions in ordinary chat. The student controls execution and their own notes.", True
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
                    if exploratory:
                        return ('No shell or invented mlab commands are available. For drafts use Explore(action="list"), then '
                                'Explore(action="draft", template_ids=[requested example IDs], title="Experiment name"). '
                                'Read commands include "mlab task show", "mlab history", and "mlab run show sim_0001".', True)
                    return "Run one mlab command in this workspace, with no shell syntax or workspace override.", True
                if exploratory:
                    read_only = tokens[1] in {"task", "history", "show", "compare", "align"} or (tokens[1] in {"run", "workflow", "measure"} and tokens[2:3] in [["show"], ["status"]])
                    if not read_only:
                        return "This workbench action belongs to the student. Use Explore to read evidence or propose a draft; the student runs it from the page.", True
                if found["kind"] and "--help" not in tokens:
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
    def __init__(self, runtime, model=MODEL, server=None, provider=None):
        self.runtime, self.root, self.model = runtime, runtime.root, model
        self.provider = provider
        self.server = server or AppServer(self.root / ".mlab/codex", self.root, provider=provider)
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
            interaction = "exploration" if getattr(self.runtime, "exploration", False) else "guided"
            allowed = {"Explore", "Bash", "Read"} if interaction == "exploration" else {"Bash", "Read", "Write", "Edit", "AskUserQuestion"}
            dynamic_tools = [tool for tool in TOOLS if tool["name"] in allowed]
            revision = hashed({"instructions": EXPLORATION, "tools": dynamic_tools}) if interaction == "exploration" else None
            compatible = interaction != "exploration" or self.runtime.meta.get("exploration_revision") == revision
            if session and compatible and self.runtime.meta.get("backend") == "codex" and self.runtime.meta.get("model", MODEL) == self.model and self.runtime.meta.get("interaction", "guided") == interaction:
                response = await self.server.request("thread/resume", {**params, "threadId": session, "excludeTurns": True})
            else:
                self.runtime.meta.pop("codex_usage_seen", None)
                if session:
                    self.runtime.add("notice", "Started a Codex conversation; earlier lab records are unchanged.", to="student")
                    self.runtime.told = None
                response = await self.server.request("thread/start", {**params, "dynamicTools": dynamic_tools,
                    "allowProviderModelFallback": False,
                    "baseInstructions": EXPLORATION if interaction == "exploration" else APPEND + (self.root / "AGENTS.md").read_text(encoding="utf-8")})
            require(response.get("model", self.model) == self.model, "E_BACKEND_UNAVAILABLE", "Codex did not select the requested model", 4)
            self.session = response["thread"]["id"]
            self.runtime.meta.update({"backend": "codex", "model": self.model, "model_provider": self.provider or "openai", "cost_available": False,
                                      "interaction": interaction, "exploration_revision": revision})
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
        if self.server.lease:
            self.server.lease.cancelled = True
        await self.tools.interrupt()
        if self.turn_id:
            await self.server.request("turn/interrupt", {"threadId": self.session, "turnId": self.turn_id})

    async def turn(self, prompt):
        try:
            async for event in self.run_turn(prompt):
                yield event
        except InferenceCancelled:
            if not self.stopped:
                raise

    async def run_turn(self, prompt):
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
    require(profile.get("backend") == "codex", "E_SCHEMA", "Use --provider codex-luna or codex-soc-qwen with --agent codex", 2)
    return lambda runtime: CodexAgentClient(runtime, profile["model"], provider=profile.get("model_provider"))
