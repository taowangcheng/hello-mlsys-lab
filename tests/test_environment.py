"""mlab has to resolve for the agent no matter how the terminal was set up."""
import shutil
import subprocess
import sys
import time
from pathlib import Path
import pytest
from mlab.backends.local import alive
from mlab.cli import doctor
from mlab.core.io import ROOT, LabError, claude_install, load, pinned_claude_version, project_claude, require_pinned_claude
from mlab.core.workspace import initialize

sys.path.insert(0, str(ROOT / "scripts"))
from qwen_demo import LoginEndpoint, child_environment
from mlab.gateway import connect_claude

BIN = Path(sys.executable).parent


def test_classroom_agent_finds_mlab_without_an_activated_environment(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = child_environment("model", "http://127.0.0.1:1", "token", "SOME_KEY", ROOT / ".test-runs/env-config")
    assert shutil.which("mlab", path=env["PATH"]) == str(BIN / "mlab")


def test_workspace_settings_find_mlab_from_any_terminal(workspace_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    initialize(workspace_path, "9.2", "fixture", "claude-haiku")
    env = load(workspace_path / ".claude/settings.local.json")["env"]
    assert shutil.which("mlab", path=env["PATH"]) == str(BIN / "mlab")
    assert env["PATH"].split(":")[0] == str(BIN) and "/bin" in env["PATH"].split(":")


def test_provider_profile_adds_to_workspace_settings_without_a_key(workspace_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret-value-not-for-disk")
    initialize(workspace_path, "9.2", "fixture", "deepseek-flash")
    raw = (workspace_path / ".claude/settings.local.json").read_text(encoding="utf-8")
    env = load(workspace_path / ".claude/settings.local.json")["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert shutil.which("mlab", path=env["PATH"])
    assert "secret-value-not-for-disk" not in raw


def test_exited_worker_counts_as_dead_before_it_is_reaped():
    process = subprocess.Popen([sys.executable, "-c", ""])
    try:
        deadline = time.monotonic() + 10
        while alive(process.pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not alive(process.pid)
        assert Path(f"/proc/{process.pid}/stat").exists()  # Still unreaped: a zombie, not a missing PID.
    finally:
        process.wait(timeout=10)
    assert not alive(process.pid)


def fake_claude(directory, version):
    path = directory / "claude"
    path.write_text(f'#!/bin/sh\necho "{version} (Claude Code)"\n')
    path.chmod(0o755)
    return path


def test_project_copy_of_claude_wins_over_other_installs(tmp_path, monkeypatch):
    other = tmp_path / "other"
    project = tmp_path / "project"
    other.mkdir()
    project.mkdir()
    fake_claude(other, "9.9.9")
    copy = fake_claude(project, pinned_claude_version())
    monkeypatch.setenv("PATH", f"{other}:/usr/bin:/bin")
    monkeypatch.setattr("mlab.core.io.BIN_DIR", project)
    assert claude_install() == (str(copy), pinned_claude_version())
    assert require_pinned_claude() == str(copy)
    assert doctor(agent="sdk")["claude_version_matches_pin"] is True


def test_any_other_claude_version_is_refused(tmp_path, monkeypatch):
    fake_claude(tmp_path, "9.9.9")
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr("mlab.core.io.BIN_DIR", tmp_path / "no-project-copy")
    with pytest.raises(LabError) as error:
        require_pinned_claude()
    assert error.value.code == "E_BACKEND_UNAVAILABLE" and pinned_claude_version() in str(error.value) and "scripts/setup.sh" in str(error.value)
    report = doctor(agent="sdk")
    assert report["claude_version_matches_pin"] is False and "scripts/setup.sh" in report["next_actions"][0]["say"]


def test_missing_claude_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("mlab.core.io.BIN_DIR", tmp_path / "no-project-copy")
    with pytest.raises(LabError):
        require_pinned_claude()
    assert doctor(agent="sdk")["claude"] is None


def test_workspace_launcher_runs_the_project_copy_with_project_settings_only(tmp_path, workspace_path, monkeypatch):
    copy = tmp_path / "claude"
    copy.write_text('#!/bin/sh\necho "$0 $*"\n')
    copy.chmod(0o755)
    monkeypatch.setattr("mlab.core.io.BIN_DIR", tmp_path)
    assert project_claude() == copy
    output = initialize(workspace_path, "9.2", "fixture", "claude-haiku")
    assert "./claude" in output["next_actions"][0]["say"]
    printed = subprocess.run([workspace_path / "claude", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    assert printed == f"{copy} --setting-sources project,local --tools Bash,Read,Edit,Write --version"


def test_workspace_settings_leave_out_personal_claude_ai_items(workspace_path):
    initialize(workspace_path, "9.2", "fixture", "claude-haiku")
    settings = load(workspace_path / ".claude/settings.local.json")
    assert settings["env"]["ANTHROPIC_MODEL"] == "haiku"
    assert settings["syncClaudeAiSkills"] is False and settings["syncClaudeAiPlugins"] is False
    assert settings["disableClaudeAiConnectors"] is True


def test_workspace_and_classroom_block_claude_updates():
    assert load(ROOT / "template/.claude/settings.json")["env"]["DISABLE_UPDATES"] == "1"
    env = child_environment("model", "http://127.0.0.1:1", "token", "SOME_KEY", ROOT / ".test-runs/env-config")
    assert env["DISABLE_UPDATES"] == "1"


def test_account_login_keeps_claude_on_its_own_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-claude")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")
    endpoint = LoginEndpoint("haiku")
    server, url, token = connect_claude(endpoint)
    assert server is None
    env = child_environment(endpoint.model, url, token, "", ROOT / ".test-runs/login-config", endpoint.auth_mode)
    assert env["ANTHROPIC_MODEL"] == "haiku"
    assert not [name for name in env if name.startswith("ANTHROPIC_") and name != "ANTHROPIC_MODEL"]
    assert "CLAUDE_CONFIG_DIR" not in env and env["DISABLE_UPDATES"] == "1"
