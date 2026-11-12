# hello-mlsys-lab

The teaching assistant for weeks 6–8 of hello-mlsys, available as a local course page and in the terminal. Students propose hypotheses, review plans, choose measurement points and make the research decisions. Codex uses GPT-6 Luna or SoC Qwen 27B to help plan experiments, run them and compare the evidence.

Experiments run on two flywheels: the fast flywheel uses the simulator (the LLM simulator or a fixture), with no limit on runs; the slow flywheel uses hardware measurement (the hardware stand-in until the AMD sandbox is connected), with a budget. All numbers are placeholder numbers.

| To read about | Go to |
| --- | --- |
| Implementation design and plan | [docs/plan.md](docs/plan.md) (Chinese copy: [docs/plan.zh-CN.md](docs/plan.zh-CN.md)) |
| Course page API | [docs/web-api.md](docs/web-api.md) |
| Backend interface and artifact formats | [docs/contract-v0.1.md](docs/contract-v0.1.md) |
| Scope of the v0.1 implementation (fast flywheel only, the student reports numeric predictions) | [Initial implementation notes](docs/implementation-v0.1.md) |
| Item-by-item check of v0.1 against the plan and the contract | [Conformance check record](docs/conformance-v0.1.md) |
| v0.1 architecture, student interaction, startup and recovery | [Initial agent architecture](docs/initial-agent-architecture.md) |
| Demo steps, inputs and expected results | [Demo steps](docs/demo.md) |

## Environment

Linux, Python 3.12. Install [uv](https://docs.astral.sh/uv/) first, then create the environment from the repository root:

```bash
scripts/setup.sh
source .venv/bin/activate
mlab doctor
```

`scripts/setup.sh` installs the Python dependencies and checks Codex 0.167.2. If that version is missing, it installs a project-local copy under `.venv/lib/mlab-codex` using npm. It does not replace a global Codex installation. The supported version is recorded in `agent/compatibility.json`.

The default agent is **Codex / GPT-6 Luna** (`gpt-6-luna`). Each workspace uses its own `.mlab/codex` directory for configuration, conversations, logs and caches. The parent process reads the existing Codex login without changing it and supplies credentials in memory; alternatively set `OPENAI_API_KEY`. No credentials are copied into the workspace. See [Codex integration](docs/codex.md) for isolation, authentication and verification details.

To use **SoC / Qwen 3.8 27B** with the same pinned Codex, run `scripts/start-soc.sh workspaces/soc-course`. It asks for the key privately if `SOCLAAS_API_KEY` is not set. The `codex-soc-qwen` profile uses SoC's native Responses API, keeps its state inside the workspace, and serializes inference across local lab processes. It does not use the personal Codex login. The SoC limits and setup are described in [Codex integration](docs/codex.md#soclaas--qwen).

## Course page

```bash
mlab web workspaces/my-course --simulator fixture --provider codex-luna
```

The first run creates the course directory, prints the page address and opens the browser; the server listens only on 127.0.0.1. When connected to a remote server (ssh, VS Code Remote or a tunnel), open the address given by port forwarding. The left column is the session list: a session does one task from the start and has its own records and agent, and the task is chosen when clicking "New session"; sessions of the same task share the hardware budget. At the top of the middle column is the session's directory of records, and below it are the records, charts and, when it is the student's turn, the forms; the right column is the session's agent. The student's words can go into the forms or into the chat; steps the student must confirm appear as confirmation cards. The top bar switches the page between Chinese and English, and the agent replies in the page's language. If the port is taken, add `--port`.

`mlab doctor --web` checks the CLI version, authentication availability, web dependencies and local TCP access, without calling a model. `--agent fake` switches to the offline example agent for walking through the page. Demo steps and the TA checklist are in [Demo steps](docs/demo.md#course-page-demo); the API is in [docs/web-api.md](docs/web-api.md).

Do not use the same workspace from the course page and the terminal at the same time.

## Talking to the agent directly

The terminal uses the same isolated Codex App Server and confirmation flow as the page:

```bash
mlab init --task 9.2 --simulator fixture --provider codex-luna workspaces/my-sla
cd workspaces/my-sla && ./codex
```

`./codex` starts the lab terminal chat. It records your words, keeps the conversation across restarts and asks before approvals, decisions and conclusions. Type `/quit` to exit. It is a lab interface backed by Codex, not the native Codex TUI.

Replace `--simulator fixture` with `--simulator llm` to have the same model estimate performance; the numbers are still labeled mock. The slow flywheel defaults to the hardware stand-in (`--hardware standin`), which creates four kinds of gap by fixed rules, so comparing simulation with measurement can be practiced in the meantime.

Start terminal chat from the repository with:

```bash
scripts/start-agent.sh --workspace workspaces/my-classroom
```

The default simulator is the fixture; `--backend llm` uses Codex / GPT-6 Luna for mock estimates. The former stage-by-stage Claude classroom remains in `scripts/qwen_demo.py` as a legacy entry point, not the default launcher.

Claude/Qwen/DeepSeek profiles and their old evidence remain for comparison. The legacy web client requires an explicit `--agent sdk --provider <legacy-profile>` and the `legacy-claude` extra; setup and normal lab runs do not start Claude Code.

## Offline checks

To check only the mlab tool workflow (a scripted student goes through one simulation round, validation on the simulator and one measurement, and compares simulation with measurement):

```bash
mlab demo --out workspaces/my-first-demo
```

Run the regression suite with:

```bash
.venv/bin/python -m pytest -q tests
```

Codex lifecycle tests use a scripted JSONL protocol peer and run without a model. Legacy tests that execute Claude Code remain opt-in. Passing offline tests does not establish the quality or availability of the real model.

## Repository layout

```text
docs/            design, plan, contract and demo steps
src/mlab/        the mlab CLI, state machine, contracts, backends and course page (src/mlab/web)
scripts/         setup, launch and regression scripts
template/        student workspace template
tasks/           draft configurations of the three tasks; thresholds, workloads and quality tables are all draft
catalog/         hardware, models and infra, with a source for every number
examples/        paper examples; all numbers are made up
agent/           model profiles and framework version compatibility records
config/          API templates; local configurations with keys are ignored by Git
evals/           direction checks and entry points for evaluation scenarios
```
