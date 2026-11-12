import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from mlab.backends.llm import estimate
from mlab.core.io import LabError, pinned_claude_version


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_fake_api_protocol_and_deterministic_cache(workspace_path, monkeypatch, protocol):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            result = {"requests": [{"request_id": "r1", "ttft_ms": 10, "tpot_ms": 2, "status": "completed"}], "vram_peak_gib": 3, "kv_peak_gib": .5}
            text = json.dumps(result)
            payload = {"content": [{"type": "text", "text": text}]} if protocol == "anthropic" else {"choices": [{"message": {"content": text}}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MLAB_LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}" + ("/v1" if protocol == "openai" else ""))
    monkeypatch.setenv("MLAB_LLM_API_KEY", "fake-test-key")
    monkeypatch.setenv("MLAB_LLM_MODEL", "fake-qwen")
    monkeypatch.setenv("MLAB_LLM_PROTOCOL", protocol)
    try:
        first, provenance = estimate({"test": 1}, [{"request_id": "r1"}], workspace_path / "cache")
        second, cached = estimate({"test": 1}, [{"request_id": "r1"}], workspace_path / "cache")
        assert first == second and cached["cached"]
        assert len(calls) == 1
        assert "fake-test-key" not in json.dumps(provenance)
    finally:
        server.shutdown()
        server.server_close()


REPLY = json.dumps({"requests": [{"request_id": "r1", "ttft_ms": 10, "tpot_ms": 2, "status": "completed"}], "vram_peak_gib": 3, "kv_peak_gib": 1})


@pytest.fixture
def account_login(tmp_path, monkeypatch):
    """No API key anywhere and a stand-in Claude Code, as inside the agent's Bash tool."""
    for name in ("MLAB_LLM_BASE_URL", "MLAB_LLM_API_KEY", "MLAB_LLM_MODEL", "MLAB_LLM_PROTOCOL", "ANTHROPIC_BASE_URL",
                 "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr("mlab.core.io.BIN_DIR", tmp_path)

    def install(reply):
        path = tmp_path / "claude"
        path.write_text(f'''#!/bin/sh
if [ "$1" = "--version" ]; then echo "{pinned_claude_version()} (Claude Code)"; exit 0; fi
printf '%s\\n' "$@" > "{tmp_path}/args"
pwd > "{tmp_path}/cwd"
env > "{tmp_path}/env"
cat > "{tmp_path}/stdin"
{reply}
''')
        path.chmod(0o755)
        return tmp_path
    return install


def test_account_login_estimates_through_the_pinned_claude(account_login, workspace_path):
    place = account_login(f"cat <<'JSON'\n{REPLY}\nJSON")
    cache = workspace_path / "cache"
    first, provenance = estimate({"test": 1}, [{"request_id": "r1"}], cache)
    assert first["vram_peak_gib"] == 3 and provenance == {"model": "haiku", "cached": False, "identity": provenance["identity"]}
    args = (place / "args").read_text().split("\n")
    assert args[:3] == ["-p", "--model", "haiku"]
    assert args[args.index("--tools") + 1] == "" and args[args.index("--setting-sources") + 1] == ""
    assert (place / "cwd").read_text().strip() == str(cache.resolve())
    env = (place / "env").read_text()
    assert "CLAUDECODE=" not in env and "CLAUDE_CODE_" not in env and "DISABLE_UPDATES=1" in env
    assert "MOCK performance estimator" in (place / "stdin").read_text()
    (place / "args").unlink()
    second, cached = estimate({"test": 1}, [{"request_id": "r1"}], cache)
    assert second == first and cached["cached"] and not (place / "args").exists()


@pytest.mark.parametrize("reply, code", [("exit 1", "E_BACKEND_UNAVAILABLE"), ("echo not-json", "E_MOCK_INVALID"), ("echo '{}'", "E_MOCK_INVALID")])
def test_account_login_failures_keep_their_error_codes(account_login, workspace_path, reply, code):
    account_login(reply)
    with pytest.raises(LabError) as error:
        estimate({"test": 1}, [{"request_id": "r1"}], workspace_path / "cache")
    assert error.value.code == code


def test_account_login_needs_the_pinned_version(tmp_path, monkeypatch, workspace_path):
    for name in ("MLAB_LLM_BASE_URL", "MLAB_LLM_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "claude"
    path.write_text('#!/bin/sh\necho "9.9.9 (Claude Code)"\n')
    path.chmod(0o755)
    monkeypatch.setattr("mlab.core.io.BIN_DIR", tmp_path)
    with pytest.raises(LabError) as error:
        estimate({"test": 1}, [{"request_id": "r1"}], workspace_path / "cache")
    assert error.value.code == "E_BACKEND_UNAVAILABLE" and "scripts/setup.sh" in str(error.value)
