"""Opt-in: the template's ask rules hold in the pinned Claude Code, including variant and compound forms."""
import json
import os
import shutil
import subprocess
import sys
import pytest
from mlab.core.io import ROOT

sys.path.insert(0, str(ROOT / "evals"))

GATED = ["mlab workflow approve --plan abc12345", "mlab --workspace . workflow approve --plan abc12345",
         "cd . && mlab measure approve --plan abc12345", "mlab --workspace . measure unlock", "mlab conclude --candidate r001/baseline"]
ALLOWED = ["mlab workflow check", "mlab history"]


@pytest.mark.skipif(os.environ.get("MLAB_RUN_CLAUDE_TESTS") != "1", reason="Set MLAB_RUN_CLAUDE_TESTS=1; requires installed Claude Code")
def test_gated_commands_need_the_student_in_every_form(workspace_path):
    from fake_model import FakeModel, tool_call, tool_results, trusted_home
    commands = GATED + ALLOWED
    model = FakeModel(lambda messages: tool_call("Bash", command=commands[tool_results(messages)], description="probe")
                      if tool_results(messages) < len(commands) else {"content": "done"})
    try:
        workspace, bin_dir = workspace_path / "ws", workspace_path / "bin"
        (workspace / ".claude").mkdir(parents=True)
        bin_dir.mkdir()
        template = json.loads((ROOT / "template/.claude/settings.json").read_text(encoding="utf-8"))
        (workspace / ".claude/settings.json").write_text(json.dumps({"permissions": {**template["permissions"],
                                                                                    "allow": template["permissions"]["allow"] + ["Bash(cd *)"]}}))
        marker = workspace_path / "ran.txt"
        (bin_dir / "mlab").write_text(f'#!/bin/sh\necho "$@" >> {marker}\necho "{{}}"\n')
        (bin_dir / "mlab").chmod(0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) and k != "CLAUDECODE"}
        env.update(model.environment(trusted_home(workspace_path / "home", workspace)), PATH=f"{bin_dir}:{env['PATH']}")
        claude = shutil.which("claude", path=str(ROOT / ".venv/bin"))
        process = subprocess.run([claude, "-p", "go", "--tools", "Bash", "--setting-sources", "project", "--output-format", "json",
                                  "--max-turns", "12"], cwd=workspace, env=env, capture_output=True, text=True, timeout=300)
        result = json.loads(process.stdout)
        ran = marker.read_text().splitlines() if marker.exists() else []
        denied = [d["tool_input"]["command"] for d in result["permission_denials"]]
    finally:
        model.close()
    assert ran == ["workflow check", "history"], (ran, process.stderr[-500:])
    assert denied == GATED
