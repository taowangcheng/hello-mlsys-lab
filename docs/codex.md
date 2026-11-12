# Codex integration

The default agent is Codex App Server 0.167.2 with `gpt-6-luna`, selected by the `codex-luna` profile. The web page and terminal share the host tool implementation, with separate exploration and guided tool policies. The simulator still defaults to mock evidence; changing the agent does not turn fixture or stand-in results into GPU measurements.

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

Every workspace has a private `.mlab/codex` directory. Only the Codex child process receives that directory as `CODEX_HOME`; the parent's environment is unchanged. Version checks also run with temporary state. The application does not copy personal configuration, skills, plugins, rules, sessions or caches. Built-in command execution, plugins, apps, hooks, web search and subagents are disabled for the lab thread. Guided conversations receive the workspace's `AGENTS.md`; workbench conversations receive the exploration policy. Native filesystem access stays read-only.

With `OPENAI_API_KEY`, the host passes the key to the App Server over its private standard-input pipe. With account login, it reads the existing Codex `auth.json` and supplies the access token through `chatgptAuthTokens`. `MLAB_CODEX_AUTH_FILE` can point to another existing login file. Both modes set `cli_auth_credentials_store="ephemeral"`; no credential file or token is written into the workspace, audit, command arguments or child environment.

The personal Codex remains responsible for renewing its login. If the App Server asks for refreshed tokens, mlab only re-reads the file: it accepts a renewed token for the same account, otherwise it asks for login renewal outside mlab. It never uses a refresh token or writes to the source login. Reading existing credentials does not isolate subscription usage: model requests still use that account or API key.

The protocol's dynamic tools and external-token mode are experimental. The integration is pinned to the tested CLI schema; changing the pin requires verifying the protocol again. See the official [App Server protocol](https://learn.chatgpt.com/docs/app-server), [credential-store configuration](https://learn.chatgpt.com/docs/config-file/config-reference) and [model names](https://learn.chatgpt.com/docs/models).

## Tools and records

The workbench exposes `Explore`, `Bash` and `Read`. The assistant can answer task questions without a tool call. `Explore` lists the current session's evidence, reads its qualified references, creates editable drafts, runs requested simulations, prepares sandbox measurements, waits for results and cancels requested runs. Execution names the exact experiment or run within that session. Bash remains limited to read operations, and direct file writes are unavailable. Students write their own observations and submit their own conclusions. Changing from a guided conversation starts a new exploration conversation while retaining that session's experiment records.

A simulation requested in conversation runs through the same validation and admission code as the page controls. A draft alone never runs. Sandbox measurements first prepare matching predictions, then pause on an in-chat execution card tied to the exact plan. The card shows the selected work and budget; confirmation resumes the same model turn, which receives the execution result. While confirmation is pending, declining, stopping the turn or sending a follow-up instead of confirming leaves the measurement unsubmitted. There is no prerequisite hypothesis, plan-review or decision sequence.

Every message receives task context, including questions with no attachments. **New conversation** starts a clean session with no earlier experiments, runs, references, notes or report. Earlier sessions remain available by explicitly reopening them; only the task's hardware budget is shared. The assistant can inspect records in its current session. Students can attach up to 12 records or type references such as `@sim_0001` or a qualified ID naming that session. Missing and other-session references are rejected.

For drafts based on examples, `Explore` accepts `template_ids` to copy complete candidates unchanged, or `variants` entries containing `template_id`, a new `id`, and `changes`. Each change names a task parameter, such as `serving.max_concurrency` or `deployments.0.weight_precision`, and supplies a text value such as `"16"` or `"INT4"`. Deployment indices start at zero. The host copies the template, applies only permitted parameter changes, and validates the complete candidate before saving. Custom configurations can still use `draft_json`; a call must choose exactly one draft format. Template drafts accept `seeds` and `workloads`, defaulting to seed 42 and development when omitted. Students can request those choices in conversation or edit them on the page.

In the terminal and guided interface, Codex receives `Bash`, `Read`, `Write`, `Edit` and `AskUserQuestion` as host-executed dynamic tools. Bash accepts one `mlab` command in the current workspace and executes an argument vector without a shell. Approval, decision, unlock and conclusion commands wait for the same confirmation cards used by the page. Refusals return to the model verbatim; stopping a turn cancels pending cards. File writes are limited to editable lab records; private state, task definitions and frozen evidence cannot be changed through these tools.

The `Read` tool can read `.mlab/last-output.json`, the detailed result artifact returned by long CLI responses, and content-addressed evidence snapshots under `.mlab/evidence/`. Other private state remains inaccessible; snapshots cannot be modified through the tool. Evidence previews identify omitted fields with exact continuation arguments. `Read` accepts a JSON pointer and character offset/limit, returns `complete` and `next` for paged reads, and checks the source version between pages. `Explore show` uses complete run logs before generating its preview, and large tool results remain valid JSON with a readable continuation. See [evidence reading](web-frontend.md) for the preview contract. `mlab align` includes `config_diff` even when empty, so the agent can distinguish no recorded mismatch from missing information.

The Codex thread ID is saved in `.mlab/web/agent.json` and resumed after reconnecting. Exploration conversations also store a fingerprint of their instructions and tool definitions; a missing or changed fingerprint starts a new thread while preserving the visible discussion and experiment records. The replacement thread receives a bounded preview of the latest eight visible messages and a readable snapshot of the full discussion from that same session. Historical messages are explicitly marked as context, not commands to repeat. This recovery context is part of thread creation, so it also survives disconnecting before the first reply. A fresh student-created session receives none of it. `.mlab/web/codex.jsonl` records prompts, visible replies, lab tool calls/results and usage, excluding authentication and private reasoning. Tokens are accumulated from differences in the thread's cumulative usage. App Server does not report a USD cost here, so the page shows that cost is unavailable instead of displaying a zero-dollar claim.

The `llm` simulator also uses an isolated Codex App Server with GPT-6 Luna by default, with no tools and an ephemeral conversation. Explicit `MLAB_LLM_*` API settings can still select a separate estimator. All these estimates remain mock observations.

## Verification and recording

```bash
.venv/bin/python -m pytest -q tests
cd frontend
npm run test:browser
```

Install the isolated test browser as described in [Frontend verification](web-frontend.md#build-and-verify) before the first browser run. These checks use scripted protocol peers and an offline page assistant. They validate tool restrictions and the student controls, not live-model availability or teaching quality.

`tests/test_codex.py` checks isolation, credential handling, tool restrictions, decisions, question answers, resume, usage accounting, process failure and interruption. Exploration tests distinguish draft creation from explicit execution and verify that measurement confirmation is required. The legacy SDK and real Claude tests remain explicit opt-ins; normal setup does not install or run them.

`tests/test_reading.py` follows the actual host tools beyond the 3,000-character preview, the 30-item list preview, and the former 60,000-character output boundary. It reconstructs complete logs and nested lists, checks immutable citation snapshots, and rejects invalid offsets, changed file versions, and private or other-workspace paths. These tests establish data accessibility, not a guarantee that a model always chooses to read every relevant page.

Earlier recordings and the workspace-local `workspaces/web-demo/record.py` script belong to the guided interface. They are retained as historical artifacts, not as the workbench's browser test. That recorder uses legacy selectors and must target `/classic` to be used with this version. A live-model recording requires outgoing provider access and local TCP sockets for the page and Chromium; its generated text is not guaranteed by offline tests.

## Page presentation

Each task opens in a three-pane workspace: task and session navigation, central task information or evidence, and a persistent conversation. Students can attach evidence from their current session and discuss it while inspecting configurations, comparisons, measurements or the full report. Optional reflections retain hypotheses and reasoning without imposing a sequence. The interface offers Chinese/English and light/dark themes, with local CSS, system fonts, a run table and charts. Main actions use experiment language rather than tool names or internal record mechanics; advanced configuration and log views retain technical detail.

The design references, component choices and current browser checks are documented in [the frontend guide](web-frontend.md). Live model replies remain unedited and may still contain technical terms.
