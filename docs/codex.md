# Codex integration

The default agent is Codex App Server 0.167.2 with `gpt-6-luna`, selected by the `codex-luna` profile. The web page and terminal share the same tool implementation. The simulator still defaults to mock evidence; changing the agent does not turn fixture or stand-in results into GPU measurements.

## Start

```bash
scripts/setup.sh
source .venv/bin/activate
mlab doctor --web
mlab web workspaces/luna-course --simulator fixture --agent codex --provider codex-luna
```

For terminal chat:

```bash
mlab init --task 9.2 --simulator fixture workspaces/luna-sla
cd workspaces/luna-sla
./codex
```

`scripts/start-agent.sh --workspace workspaces/luna-sla` starts the same terminal interface from the repository. Existing courses retain the profile recorded in `course.json`; use a new course directory when changing from a legacy Claude profile. Earlier course records and videos remain readable with `--agent fake`.

## Isolation and authentication

Every workspace has a private `.mlab/codex` directory. Only the Codex child process receives that directory as `CODEX_HOME`; the parent's environment is unchanged. Version checks also run with temporary state. The application does not copy personal configuration, skills, plugins, rules, sessions or caches. Built-in command execution, plugins, apps, hooks, web search and subagents are disabled for the lab thread. The host supplies the workspace's `AGENTS.md` explicitly and keeps native filesystem access read-only.

With `OPENAI_API_KEY`, the host passes the key to the App Server over its private standard-input pipe. With account login, it reads the existing Codex `auth.json` and supplies the access token through `chatgptAuthTokens`. `MLAB_CODEX_AUTH_FILE` can point to another existing login file. Both modes set `cli_auth_credentials_store="ephemeral"`; no credential file or token is written into the workspace, audit, command arguments or child environment.

The personal Codex remains responsible for renewing its login. If the App Server asks for refreshed tokens, mlab only re-reads the file: it accepts a renewed token for the same account, otherwise it asks for login renewal outside mlab. It never uses a refresh token or writes to the source login. Reading existing credentials does not isolate subscription usage: model requests still use that account or API key.

The protocol's dynamic tools and external-token mode are experimental. The integration is pinned to the tested CLI schema; changing the pin requires verifying the protocol again. See the official [App Server protocol](https://learn.chatgpt.com/docs/app-server), [credential-store configuration](https://learn.chatgpt.com/docs/config-file/config-reference) and [model names](https://learn.chatgpt.com/docs/models).

## Tools and records

Codex receives `Bash`, `Read`, `Write`, `Edit` and `AskUserQuestion` as host-executed dynamic tools. Bash accepts one `mlab` command in the current workspace and executes an argument vector without a shell. Approval, decision, unlock and conclusion commands wait for the same confirmation cards used by the page. Refusals return to the model verbatim; stopping a turn cancels pending cards. File writes are limited to editable lab records; private state, task definitions and frozen evidence cannot be changed through these tools.

The Codex thread ID is saved in `.mlab/web/agent.json` and resumed after reconnecting. `.mlab/web/codex.jsonl` records prompts, visible replies, lab tool calls/results and usage, excluding authentication and private reasoning. Tokens are accumulated from differences in the thread's cumulative usage. App Server does not report a USD cost here, so the page shows that cost is unavailable instead of displaying a zero-dollar claim.

The `llm` simulator also uses an isolated Codex App Server with GPT-6 Luna by default, with no tools and an ephemeral conversation. Explicit `MLAB_LLM_*` API settings can still select a separate estimator. All these estimates remain mock observations.

## Verification and recording

```bash
.venv/bin/python -m pytest -q tests
cd workspaces/web-demo
uv run --no-project --with websockets python record.py run-luna --agent codex --mode mixed --provider codex-luna
```

The recording requires outgoing model access and local TCP sockets for the page and Chromium. `--agent fake --mode forms` checks the page without a model, but still requires local TCP. The video script labels that mode as an offline example.

`tests/test_codex.py` uses a scripted App Server protocol peer to check isolation, credential handling, tool restrictions, decisions, question answers, resume, usage accounting, process failure and interruption. These tests do not establish real-model teaching quality. The legacy SDK and real Claude tests remain explicit opt-ins; normal setup does not install or run them.
