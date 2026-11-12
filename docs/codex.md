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

### SoCLaaS / Qwen

```bash
scripts/setup.sh
scripts/start-soc.sh workspaces/soc-course
```

The launcher prompts for `SOCLAAS_API_KEY` without echoing it when the variable is absent. It fixes the model to `qwen3.8:27b` and uses the same pinned Codex **0.167.2**. The default API root is `https://soclaas-api.comp.nus.edu.sg/v1`; `SOCLAAS_BASE_URL` may set an HTTPS gateway root. Do not put a key in a profile, shell command, configuration file or recording. A shell's existing `SOCLAAS_MODEL=default` is deliberately not accepted by the direct profile: unset it or set the pinned alias. The launch script selects the pinned alias explicitly.

For manual startup, provide the key through the environment and run `mlab web workspaces/soc-course --agent codex --provider codex-soc-qwen --simulator fixture`. Use a fresh course directory when switching providers. `--simulator llm` also selects SoC Qwen for the mock estimator; the fixture recording uses deterministic synthetic performance numbers and real model conversations.

SoC uses its native Responses API and the checked-in `agent/soclaas-models.json` catalog. The live catalog on 2026-11-12 lists `qwen3.8:27b` with a 262,144-token context and low/medium/xhigh reasoning. The profile uses low reasoning, disables parallel tool calls and WebSocket transport, and does not fall back to another model. It never reads the personal Codex login: only the SoC child process receives the key as an environment variable. No SoC setup script is run against `~/.codex`.

The local SoC guide lists a default **90 requests/minute** and says API-key concurrency limits also apply, but does not publish a numerical concurrency cap. The portal budget endpoint timed out from this machine during verification; the actual key policy is therefore unconfirmed. The lab conservatively allows **one active inference segment** across all SoC lab processes for this OS user. A process-shared file lease releases capacity while awaiting page decisions or executing tools, including estimator subprocesses, and spaces new segments by at least one second. It does not coordinate unrelated applications using the same key, and Codex-internal continuations can still encounter the server's rate limit. Codex can retry request failures up to twice; stream replay is disabled to avoid replaying partial tool work. A quota error remains an error, with no model fallback.

The lease directory is under the OS temporary directory and contains only a timestamp. A cancelled queued turn never starts later, and process exit releases the lock. SoC's published monetary figures are quota accounting, not a charge; the page reports tokens without claiming a USD bill. See [SoC usage limits](https://dochub.comp.nus.edu.sg/cf/guides/soclaas/usage-limits) and [Codex gateway compatibility](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility).

### OpenAI login

Every workspace has a private `.mlab/codex` directory. Only the Codex child process receives that directory as `CODEX_HOME`; the parent's environment is unchanged. Version checks also run with temporary state. The application does not copy personal configuration, skills, plugins, rules, sessions or caches. Built-in command execution, plugins, apps, hooks, web search and subagents are disabled for the lab thread. The host supplies the workspace's `AGENTS.md` explicitly and keeps native filesystem access read-only.

With `OPENAI_API_KEY`, the host passes the key to the App Server over its private standard-input pipe. With account login, it reads the existing Codex `auth.json` and supplies the access token through `chatgptAuthTokens`. `MLAB_CODEX_AUTH_FILE` can point to another existing login file. Both modes set `cli_auth_credentials_store="ephemeral"`; no credential file or token is written into the workspace, audit, command arguments or child environment.

The personal Codex remains responsible for renewing its login. If the App Server asks for refreshed tokens, mlab only re-reads the file: it accepts a renewed token for the same account, otherwise it asks for login renewal outside mlab. It never uses a refresh token or writes to the source login. Reading existing credentials does not isolate subscription usage: model requests still use that account or API key.

The protocol's dynamic tools and external-token mode are experimental. The integration is pinned to the tested CLI schema; changing the pin requires verifying the protocol again. See the official [App Server protocol](https://learn.chatgpt.com/docs/app-server), [credential-store configuration](https://learn.chatgpt.com/docs/config-file/config-reference) and [model names](https://learn.chatgpt.com/docs/models).

## Tools and records

Codex receives `Bash`, `Read`, `Write`, `Edit` and `AskUserQuestion` as host-executed dynamic tools. Bash accepts one `mlab` command in the current workspace and executes an argument vector without a shell. Approval, decision, unlock and conclusion commands wait for the same confirmation cards used by the page. Refusals return to the model verbatim; stopping a turn cancels pending cards. File writes are limited to editable lab records; private state, task definitions and frozen evidence cannot be changed through these tools.

The `Read` tool can read `.mlab/last-output.json`, the detailed result artifact returned by long CLI responses. Other private state remains inaccessible. `mlab align` includes `config_diff` even when empty, so the agent can distinguish no recorded mismatch from missing information.

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

## Page presentation

The page uses a quieter neutral palette, a single accent, clearer headings and more space around experiment results. The welcome page introduces the three tasks, while the discussion keeps technical activity and model usage in expandable details. Student notes remain distinct from computed results. Static English and Chinese text describes the action and its purpose, without exposing hashes, record filenames or agent messaging in the main flow. Model replies are real, unedited responses and may still use technical terms.

The visual references were [Linear](https://linear.app/) for workspace hierarchy and [Vercel Geist](https://vercel.com/geist/introduction) for typography, spacing and restrained component styling. The implementation remains local CSS and JavaScript with no external fonts or page dependencies. Desktop, dark-theme and 390-pixel mobile previews were checked; tables scroll within their containers.
