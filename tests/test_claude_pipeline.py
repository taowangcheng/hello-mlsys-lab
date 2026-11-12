"""Opt-in real harness acceptance. Local fake API; no remote credentials used."""
import os
import subprocess
import sys
import pytest
from mlab.core.io import ROOT


@pytest.mark.skipif(os.environ.get("MLAB_RUN_CLAUDE_TESTS") != "1", reason="Set MLAB_RUN_CLAUDE_TESTS=1; requires installed Claude Code")
@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
@pytest.mark.parametrize("backend", ["fixture", "llm"])
def test_two_round_real_claude_pipeline(protocol, backend, workspace_path):
    # The test's own workspace, removed afterwards; without --workspace the script would leave one under workspaces/.
    result = subprocess.run([sys.executable, str(ROOT / "evals/local_classroom.py"),
        "--protocol", protocol, "--backend", backend, "--workspace", str(workspace_path)], cwd=ROOT, capture_output=True,
        encoding="utf-8", errors="replace", timeout=600)
    assert result.returncode == 0, result.stdout[-5000:] + result.stderr[-2000:]
    assert "PASS classroom integration:" in result.stdout
