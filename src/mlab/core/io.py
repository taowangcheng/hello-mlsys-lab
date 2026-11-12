import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]
BIN_DIR = Path(sys.executable).parent  # Holds the mlab script and, after scripts/setup.sh, the pinned Claude Code.
NOTICE = "Placeholder numbers: the simulator and the hardware stand-in report made-up numbers, only for walking through the workflow."
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def tool_path():
    """PATH with this interpreter's bin directory first."""
    entries = [str(BIN_DIR), *os.environ.get("PATH", "").split(os.pathsep)]
    return os.pathsep.join(dict.fromkeys(filter(None, entries)))


class LabError(Exception):
    def __init__(self, code, message, exit_code=3, field=None, actions=None):
        super().__init__(message)
        self.code, self.exit_code, self.field = code, exit_code, field
        self.actions = actions or default_actions(exit_code, message)


def default_actions(exit_code, message):
    if exit_code == 3:  # Blocked by a rule: the agent stops and the student decides.
        return [{"actor": "agent", "do": "end_turn", "say": "Explain this to the student. Do not change files or the plan to get around it."},
                {"actor": "student", "do": "review", "say": message}]
    return [{"actor": "agent", "do": "review", "say": message}]


def require(condition, code, message, exit_code=3, field=None):
    if not condition:
        raise LabError(code, message, exit_code, field)


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key, value in node.value:
        key = loader.construct_object(key, deep=deep)
        if key in result:
            raise LabError("E_SCHEMA", f"duplicate YAML key: {key}", 2)
        result[key] = loader.construct_object(value, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    return yaml.load(text, Loader=UniqueLoader) if path.suffix in {".yaml", ".yml"} else json.loads(text)


def pinned_claude_version():
    return load(ROOT / "agent/compatibility.json")["pinned_claude_version"]


def project_claude():
    """Where scripts/setup.sh installs the pinned Claude Code."""
    return BIN_DIR / "claude"


def claude_install():
    """Path and version of the Claude Code to run: the project copy when present, else PATH."""
    path = shutil.which("claude", path=tool_path())
    if not path:
        return None, None
    try:
        output = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        output = []
    return path, output[0] if output else None


def claude_logged_in(path):
    """Whether Claude Code has an account login; None when it cannot tell."""
    try:
        output = subprocess.run([path, "auth", "status"], capture_output=True, text=True, timeout=15).stdout
        return bool(json.loads(output).get("loggedIn"))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def require_pinned_claude():
    path, version = claude_install()
    pinned = pinned_claude_version()
    require(version == pinned, "E_BACKEND_UNAVAILABLE", f"Claude Code {pinned} is required, found {version or 'none'}. Run scripts/setup.sh.", 4)
    return path


def atomic(path, value, text=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    content = value if text else (yaml.safe_dump(value, allow_unicode=True, sort_keys=False) if path.suffix == ".yaml"
                                 else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def hashed(value):
    if isinstance(value, Path):
        raw = value.read_bytes()
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def inside(root, relative):
    path = (Path(root) / relative).resolve()
    require(path.is_relative_to(Path(root).resolve()), "E_SCHEMA", "path escapes allowed directory", 2)
    return path


def dump(model):
    return model.model_dump(by_alias=True, mode="json")
