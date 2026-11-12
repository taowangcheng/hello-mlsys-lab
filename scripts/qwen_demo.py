"""Launch a native Messages classroom or adapt an OpenAI-compatible API."""
import argparse
import datetime
import getpass
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mlab.core.io import claude_logged_in, require, require_pinned_claude, tool_path
from mlab.gateway import Gateway, GatewayError, AnthropicEndpoint, connect_claude
from mlab.agent_env import child_environment  # noqa: F401  (re-exported for classroom.py)


class LoginEndpoint:
    """Claude Code's own account login: no API key, and requests go through Claude Code."""
    protocol = auth_mode = "login"
    url = api_key = token = ""
    timeout, max_tokens = 180, 4096

    def __init__(self, model):
        self.model = model


def check_api(gateway):
    """Verify a harmless function call AND the following tool-result turn."""
    request = {"messages": [{"role": "user", "content": "Call check_status with experiment_id='probe'."}],
        "max_tokens": 256, "tools": [{"name": "check_status", "description": "Harmless test, no experiment runs.",
        "input_schema": {"type": "object", "properties": {"experiment_id": {"type": "string"}},
                         "required": ["experiment_id"]}}],
        "tool_choice": {"type": "tool", "name": "check_status"}}
    answer = gateway.complete(request)
    calls = [b for b in answer["content"] if b["type"] == "tool_use"]
    if len(calls) != 1 or calls[0]["name"] != "check_status" or calls[0]["input"] != {"experiment_id": "probe"}:
        raise GatewayError("API replied, but failed the required function-tool test.")
    request["messages"] += [{"role": "assistant", "content": answer["content"]}, {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": calls[0]["id"], "content": '{"status":"ready"}'}]}]
    request["tool_choice"] = {"type": "none"}
    reply = gateway.complete(request)
    if not any(b["type"] == "text" and b["text"].strip() for b in reply["content"]):
        raise GatewayError("API failed the tool-result round trip.")
    print("API check passed: function call + tool-result round trip.", flush=True)


def run_demo(gateway, key_env):
    claude = require_pinned_claude()
    label = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    relative = "workspaces/qwen-" + label
    audit = ROOT / "workspaces" / ("qwen-session-" + label)
    audit.mkdir(parents=True)
    commands = [f"mlab demo --task 9.2 --out {relative}",
                f"mlab --workspace {relative} compare sim_0001 sim_0002 sim_0003", f"mlab --workspace {relative} next",
                f"mlab --workspace {relative} report"]
    # Exact command allowlist, no global permissions bypass. Only Bash is exposed.
    settings = {"permissions": {"allow": [f"Bash({c})" for c in commands]},
                "enableAllProjectMcpServers": False}
    settings_path = audit / "settings.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    mcp_path = audit / "mcp.json"
    mcp_path.write_text('{"mcpServers":{}}', encoding="utf-8")
    prompt = """Perform an authorized offline teaching demonstration using the Bash tool.
No subagents. The student in this demo is explicitly SCRIPTED, not a real student.
Execute the following exact commands separately, in order. Do not prepend cd or chain commands.
If any fails, stop and report the real error. Do not invent tool output.
""" + "\n".join(commands) + """
The demo command implements configuration validation, three development runs,
comparison, one repeat and two holdout runs. The next command reopens saved state
in a separate mlab process; it verifies workflow recovery, not chat-session recovery.
Finally summarize actual run count, chosen candidate, conclusion and report path
in Chinese. All numbers are mock placeholders; state mock_only explicitly.
"""
    server, url, token = connect_claude(gateway)
    env = child_environment(gateway.model, url, token, key_env, audit / "claude-config", gateway.auth_mode)
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(gateway.max_tokens)
    argv = [claude, "-p", "--model", gateway.model, "--tools", "Bash",
        "--settings", str(settings_path), "--setting-sources", "project,local",
        "--strict-mcp-config", "--mcp-config", str(mcp_path),
        "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--max-turns", "12"]
    print("Starting Claude Code with Qwen API; no Claude login.\nAudit: " + str(audit), flush=True)
    result = None
    executed = []
    tool_ids = {}
    successful_tools = set()
    try:
        with (audit / "claude-events.jsonl").open("w", encoding="utf-8") as log, (audit / "claude-stderr.txt").open("w", encoding="utf-8") as err:
            with subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                  text=True, encoding="utf-8", errors="replace") as process:
                try:
                    process.stdin.write(prompt)
                    process.stdin.close()
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("type") == "assistant":
                            for block in event.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use":
                                    command = block.get("input", {}).get("command", "")
                                    executed.append(command)
                                    tool_ids[block["id"]] = command
                                    print("Tool request: " + command, flush=True)
                                elif block.get("type") == "text":
                                    print(block["text"], flush=True)
                        if event.get("type") == "user":
                            for block in event.get("message", {}).get("content", []):
                                if block.get("type") == "tool_result" and not block.get("is_error"):
                                    successful_tools.add(tool_ids.get(block.get("tool_use_id")))
                        if event.get("type") == "result":
                            result = event
                    exit_code = process.wait()
                except BaseException:
                    process.terminate()
                    raise
        summary_path = ROOT / relative / "demo-summary.json"
        if exit_code or not result or result.get("is_error") or not summary_path.exists():
            raise GatewayError("Claude did not complete the demo. Inspect this session's claude-events.jsonl and claude-stderr.txt.")
        # Verify independent artifacts, not just the model's final text.
        from mlab.core.workspace import Lab
        lab = Lab(ROOT / relative)
        try:
            runs = lab.store.all("run")
            if len(runs) != 6 or any(r["status"] != "succeeded" for r in runs):
                raise GatewayError("Demo artifacts are incomplete: expected six completed fixture runs.")
            recovered = lab.next()
            conclusion = lab.active()["conclusion"]
        finally:
            lab.close()
        if conclusion != "mock_only" or any(c not in successful_tools for c in commands):
            raise GatewayError("Missing expected Claude tool calls or mock_only conclusion; inspect audit log.")
        verification = {"ok": True, "model": gateway.model, "workspace": relative,
            "runs": len(runs), "conclusion": conclusion, "claude_tool_requests": executed,
            "recovered_state": recovered, "upstream_api_tool_loop_verified": True}
        (audit / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Verified six mock runs + saved-state recovery.\nReport: " + str(ROOT / relative / "report.md"), flush=True)
    finally:
        if server:
            server.shutdown()
            server.server_close()


def load_settings(path):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    base = (os.environ.get(config.get("base_url_env", "QWEN_BASE_URL")) or config.get("base_url", "")).strip()
    model = (os.environ.get(config.get("model_env", "QWEN_MODEL")) or config.get("model", "")).strip()
    if not base or not model or "YOUR-" in base or "YOUR-" in model:
        raise GatewayError("Fill base_url/model in your local config or its configured environment variables.")
    key_env = config.get("api_key_env", "QWEN_API_KEY")
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise GatewayError("api_key_env must be an environment variable NAME, e.g. SOCLAAS_API_KEY. Put a JSON key value in api_key instead.")
    return config, base, model, key_env


def read_key(config, key_env):
    key = (os.environ.get(key_env) or "").strip() or str(config.get("api_key") or "").strip()
    return key or getpass.getpass("Model API Key (hidden, not saved): ")


def make_endpoint(config, base, model, key):
    protocol = config.get("protocol", "openai")
    if protocol not in {"anthropic", "openai"}:
        raise GatewayError("protocol must be anthropic or openai.")
    endpoint_class = AnthropicEndpoint if protocol == "anthropic" else Gateway
    endpoint = endpoint_class(base, model, key.strip(), secrets.token_urlsafe(32),
        max_tokens=int(config.get("max_output_tokens", 4096)),
        timeout=int(config.get("request_timeout_seconds", 180)), extra_body=config.get("extra_body", {}))
    endpoint.auth_mode = config.get("auth_mode", endpoint.auth_mode)
    if endpoint.auth_mode not in {"api_key", "bearer"} or (protocol == "openai" and endpoint.auth_mode != "bearer"):
        raise GatewayError("Use api_key/bearer for native Anthropic; OpenAI adapter requires bearer.")
    return endpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="API config; default config/agent.local.json, else Claude Code account login")
    parser.add_argument("--model", default="haiku", help="Model when using account login")
    parser.add_argument("--check", action="store_true", help="Only test API tools; do not launch Claude.")
    parser.add_argument("--mode", choices=["classroom", "smoke"], default="classroom")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--task", choices=["9.1", "9.2", "9.3"], default="9.2")
    parser.add_argument("--backend", choices=["fixture", "llm"], default="fixture")
    parser.add_argument("--scripted-student", action="store_true")
    parser.add_argument("--rounds", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    path = ROOT / (args.config or "config/agent.local.json")
    try:
        if path.exists():
            config, base, model, key_env = load_settings(path)
            key = read_key(config, key_env)
            if not key.strip():
                raise GatewayError("API key is empty.")
            gateway = make_endpoint(config, base, model, key)
            check_api(gateway)
        elif args.config:
            raise GatewayError("Copy config/claude.example.json (native) or qwen.example.json (OpenAI) to the requested local config; fill base_url/model first.")
        else:
            gateway, key_env = LoginEndpoint(args.model), ""
            require(claude_logged_in(require_pinned_claude()) is not False, "E_BACKEND_UNAVAILABLE", "Claude Code is not logged in. Run it once to log in.", 4)
            print("Using Claude Code account login, model " + args.model, flush=True)
        if not args.check:
            if args.mode == "smoke":
                run_demo(gateway, key_env)
            else:
                from classroom import run_classroom
                workspace = args.workspace or Path("workspaces") / ("classroom-" + args.task)
                if not workspace.is_absolute():
                    workspace = ROOT / workspace
                run_classroom(gateway, key_env, workspace, args.task, args.backend, args.scripted_student, args.rounds)
        return 0
    except (GatewayError, OSError, ValueError, KeyError) as error:
        # Gateway failures are sanitized before this point; never print raw HTTP bodies.
        print("ERROR: " + (str(error) if isinstance(error, GatewayError) else "Invalid configuration or local process/file error."), file=sys.stderr)
        return 1
    except Exception as error:
        from mlab.core.io import LabError
        if not isinstance(error, LabError):
            raise
        print("ERROR " + error.code + ": " + str(error), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("Stopped; gateway closed. Completed mock artifacts remain on disk.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
