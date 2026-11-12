"""Environment for the Claude Code agents mlab starts: the terminal classroom and the web page."""
import os
import shutil
from pathlib import Path
from .core.io import ROOT, LabError, inside, load, tool_path
from .gateway import GatewayError

FOREIGN_PREFIXES = ("ANTHROPIC_", "MLAB_LLM_", "QWEN", "SOCLAAS_")
PROVIDER_SWITCHES = {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDECODE"}


def child_environment(model, url, token, key_env, config_dir, auth_mode="bearer"):
    """Terminal classroom: a full environment for `claude -p` against a gateway, a native endpoint or the login."""
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(FOREIGN_PREFIXES) or name == key_env or name in PROVIDER_SWITCHES:
            env.pop(name, None)
    env.update({"ANTHROPIC_BASE_URL": url, "ANTHROPIC_AUTH_TOKEN": token, "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model, "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model, "CLAUDE_CODE_SUBAGENT_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "MAX_THINKING_TOKENS": "0", "DISABLE_UPDATES": "1",
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "PATH": tool_path(),
        "PYTHONUTF8": "1"})
    if not shutil.which("mlab", path=env["PATH"]):
        raise GatewayError("mlab is not installed for this Python. Run scripts/setup.sh and start with scripts/start-agent.sh.")
    if auth_mode == "api_key":
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env["ANTHROPIC_API_KEY"] = token
    if auth_mode == "login":
        for name in [n for n in env if n.startswith("ANTHROPIC_")] + ["CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SUBAGENT_MODEL"]:
            env.pop(name, None)
        env["ANTHROPIC_MODEL"] = model
    return env


def load_profile(name):
    path = inside(ROOT / "agent/profiles", f"{name}.json")
    if not path.is_file():
        raise LabError("E_SCHEMA", f"unknown provider profile {name}", 2)
    return load(path)


def provider_key(profile):
    """The API key of a provider profile, from the variable the profile names; None for the account login."""
    variable = profile.get("api_key_env")
    if not variable:
        return None
    key = os.environ.get(variable) or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LabError("E_BACKEND_UNAVAILABLE", f"set {variable} for the {profile['id']} profile; keys are read from the environment only", 4)
    return key


def sanitize_process_environment():
    """The web server's own environment: provider variables it inherited must not reach the agents."""
    for name in list(os.environ):
        if name.startswith(FOREIGN_PREFIXES) or name in PROVIDER_SWITCHES:
            os.environ.pop(name, None)


def web_environment(key=None, config_dir=None):
    """Variables a web task agent adds on top of the sanitized process environment.

    The model and endpoint come from the workspace's settings.local.json, written from the profile by `mlab init`.
    With a key, Claude Code also gets its own config directory, so the student's own Claude settings stay out.
    """
    env = {"DISABLE_UPDATES": "1", "PATH": tool_path(), "PYTHONUTF8": "1", "MLAB_PROMPT_SOURCE": "web"}
    if key:
        env["ANTHROPIC_AUTH_TOKEN"] = key
        if config_dir:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env
