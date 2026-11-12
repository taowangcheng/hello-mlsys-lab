"""Claude Code through the Agent SDK, one long-lived session per task workspace.

The pinned CLI runs in the workspace with project and local settings, so AGENTS.md, the hooks and the model from
settings.local.json apply as in the terminal. The template's permission rules are passed again as flag settings,
which hold even when the workspace is not trusted. Gated commands reach can_use_tool and become cards on the page;
a PreToolUse hook refuses gated commands hidden in variant or compound forms. The raw message stream is appended
to .mlab/web/sdk.jsonl for audit.
"""
import dataclasses
import json
import os
from ..agent_env import load_profile, provider_key, sanitize_process_environment, web_environment
from ..core.io import ROOT, load, project_claude
from . import approvals

TOOLS = ["Bash", "Read", "Edit", "Write", "AskUserQuestion"]
APPEND = ("This session runs behind the course web page. Text marked [Page notice] or [Page note] comes from the page, not from "
          "the student; follow the section 'Web page mode' of AGENTS.md. You are talking with the student: address them directly "
          "in the second person, never as 'the student'.")


def template_settings():
    settings = load(ROOT / "template/.claude/settings.json")
    return {"permissions": settings["permissions"], "hooks": settings.get("hooks", {})}


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(str(block.get("text", "")) for block in content or [] if isinstance(block, dict))


def plain(value, limit=4000):
    """A message as JSON-safe data for the audit log, with long strings cut."""
    if dataclasses.is_dataclass(value):
        value = {"type": type(value).__name__, **{f.name: getattr(value, f.name) for f in dataclasses.fields(value)}}
    if isinstance(value, dict):
        return {str(k): plain(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v, limit) for v in value]
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… ({len(value)} characters)"
    return value if isinstance(value, (int, float, bool)) or value is None else str(value)


class SdkAgentClient:
    def __init__(self, runtime, env, max_turns=60):
        self.runtime, self.root, self.env, self.max_turns = runtime, runtime.root, env, max_turns
        self.client = None
        self.audit = self.root / ".mlab/web/sdk.jsonl"
        self.errors = []
        self.cost_seen = 0.0  # total_cost_usd is cumulative within a session; turns get the difference.

    def options(self, resume):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
        settings = template_settings()
        claude = project_claude()
        return ClaudeAgentOptions(
            cwd=str(self.root), cli_path=str(claude) if claude.exists() else None, setting_sources=["project", "local"],
            system_prompt={"type": "preset", "preset": "claude_code", "append": APPEND}, tools=TOOLS,
            allowed_tools=list(settings["permissions"]["allow"]), settings=json.dumps(settings, ensure_ascii=False),
            can_use_tool=self.can_use_tool, hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[self.guard])]},
            env=self.env, include_partial_messages=True, max_turns=self.max_turns, resume=resume or None, stderr=self.stderr)

    def stderr(self, line):
        self.errors = (self.errors + [line])[-40:]

    def log(self, message):
        with open(self.audit, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(plain(message), ensure_ascii=False) + "\n")

    async def connect(self, session):
        from claude_agent_sdk import ClaudeSDKClient
        self.cost_seen = float(self.runtime.meta.get("session_cost_seen") or 0) if session else 0.0
        self.client = ClaudeSDKClient(self.options(session))
        try:
            await self.client.connect()
        except Exception:
            if not session:
                raise RuntimeError("; ".join(self.errors[-3:]) or "Claude Code did not start")
            # The saved session may be gone, for example after the config directory changed; start a new one.
            self.runtime.add("notice", "Could not resume the previous conversation; started a new one. The records are unchanged.", to="student")
            self.client = ClaudeSDKClient(self.options(None))
            await self.client.connect()
            session = None
        return session

    async def disconnect(self):
        if self.client:
            client, self.client = self.client, None
            await client.disconnect()

    async def interrupt(self):
        if self.client:
            await self.client.interrupt()

    async def can_use_tool(self, tool, data, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        decision = await self.runtime.permission(tool, data)
        if decision["allow"]:
            return PermissionResultAllow(updated_input=decision.get("updated_input"))
        return PermissionResultDeny(message=decision["message"])

    async def guard(self, data, tool_use_id, context):
        """Gated commands only in their standard form, run alone; the ask rules then turn them into cards."""
        command = str((data.get("tool_input") or {}).get("command", ""))
        found = approvals.classify(command)
        if found["kind"] and not found["plain"]:
            self.runtime.add("notice", f"Blocked agent command: {command}", to="student", reason=found["reason"])
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": found["reason"]}}
        return {}

    async def turn(self, prompt):
        from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
        self.log({"type": "prompt", "text": prompt})
        await self.client.query(prompt)
        async for message in self.client.receive_response():
            if not isinstance(message, StreamEvent):
                self.log(message)
            if isinstance(message, StreamEvent):
                delta = message.event.get("delta") or {}
                if message.event.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and not message.parent_tool_use_id:
                    yield {"type": "delta", "text": delta.get("text", "")}
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield {"type": "text", "text": block.text}
                    elif isinstance(block, ToolUseBlock):
                        yield {"type": "tool", "id": block.id, "name": block.name, "input": block.input}
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        yield {"type": "tool_result", "id": block.tool_use_id, "text": text_of(block.content), "is_error": bool(block.is_error)}
            elif isinstance(message, ResultMessage):
                # Claude Code prices tokens at Anthropic API rates; through another provider or an account it is an estimate.
                # The figure is the session's running total: a lower one means the count started again.
                total = float(message.total_cost_usd or 0)
                cost = total - self.cost_seen if total >= self.cost_seen else total
                self.cost_seen = total
                yield {"type": "result", "usage": message.usage or {}, "cost_usd": round(cost, 6), "cost_seen": total, "cost_estimated": True,
                       "session": message.session_id, "is_error": message.is_error}


def factory(provider=None, env=None):
    """A client factory for mlab web. The key is read once from the environment, then provider variables are removed
    from the server process so that nothing reaches an agent except what is passed here."""
    own_config = False
    if env is None:
        profile = load_profile(provider) if provider else None
        key = provider_key(profile) if profile else None
        simulator = {name: value for name, value in os.environ.items() if name.startswith("MLAB_LLM_")}
        sanitize_process_environment()
        env = {**web_environment(key, None), **simulator}
        own_config = bool(key)
    def make(runtime):
        task_env = dict(env)
        if own_config:
            # With an API key, each task gets its own Claude Code config directory, away from the student's own.
            directory = runtime.root / ".mlab/web/claude-config"
            directory.mkdir(parents=True, exist_ok=True)
            task_env["CLAUDE_CONFIG_DIR"] = str(directory)
        return SdkAgentClient(runtime, task_env)
    return make
