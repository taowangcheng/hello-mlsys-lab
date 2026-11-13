# hello-mlsys-lab

The learning workspace for weeks 6–8 of hello-mlsys, available as a local course page and in the terminal. Students learn through conversation with a Codex assistant using GPT-6 Luna or SoC Qwen 27B: ask about the task, work through an idea, plan and run experiments, investigate earlier results, and compare explanations. Experiment editors, execution history and charts support that conversation, with direct controls available throughout.

The page keeps task and session navigation on the left, task information and evidence in the center, and conversation on the right. Configuration histories, measurement analysis, optional reflections and full reports stay in the workspace. A new conversation starts a clean session; earlier work remains in its original session, and only the task's hardware budget is shared.

Experiments can use simulation or hardware measurement. Simulation has no total run budget, but each batch has a size limit. Measurements share a task budget and currently use a stand-in until the AMD sandbox is connected. The fixture, LLM simulator and stand-in all produce demonstration numbers.

| To read about | Go to |
| --- | --- |
| Implementation design and plan | [docs/plan.md](docs/plan.md) (Chinese copy: [docs/plan.zh-CN.md](docs/plan.zh-CN.md)) |
| Workbench design, build and browser checks | [Frontend guide](docs/web-frontend.md) |
| Course page API | [docs/web-api.md](docs/web-api.md) |
| Backend interface and artifact formats | [docs/contract-v0.1.md](docs/contract-v0.1.md) |
| External simulator API review and coordination | [Integration findings](integrations/mlsys-simulator/README.md) · [Forwardable Chinese note](integrations/mlsys-simulator/REQUESTS.zh-CN.md) |
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

The first run creates the course directory and opens a page served on 127.0.0.1. For a remote machine, use an SSH or editor port forward. Each task opens with its overview and a persistent conversation beside it. Ask a conceptual question without preparing an experiment, request a simulation, or bring earlier evidence from this session into the discussion with **Add from history** or an `@` reference. The assistant can find the session's earlier work even when nothing is attached. Requested sandbox measurements show their configuration, workload and budget for confirmation before execution. Students can also edit and run experiments directly, inspect request timings, compare results and write their own observations. There is no required sequence of questions, experiments or conclusions.

Use **New conversation** to start a clean session within the same task. Reopen an earlier session from the session list to recover its conversation, drafts and execution records. **Add from history** attaches evidence from the current session. Only the measurement budget remains shared across the task. Chinese/English and light/dark settings are retained in the browser. Add `--port` if the default port is busy.

The React workbench is included as built static assets. Developers rebuild it with `scripts/build-web.sh`; the toolchain and npm cache stay inside the project. See [Frontend design and verification](docs/web-frontend.md). Configuration records, measurement analysis and full reports open directly in the central pane; `/classic` remains available for compatibility.

`mlab doctor --web` checks the CLI version, authentication availability, web dependencies and local TCP access, without calling a model. `--agent fake` provides an offline preview with scripted simulation and measurement requests; it cannot provide model explanations. Demo steps and the TA checklist are in [Demo steps](docs/demo.md#course-page-demo); the API is in [docs/web-api.md](docs/web-api.md).

The [recording procedure](docs/demo.md#record-the-conversation-experience) produces a local review gallery at `workspaces/studio-review/index.html` and an optional `studio-review.zip` in the same directory. It includes full real SoC conversations, screenshots and normal-speed video; experiment performance remains fixture and stand-in data. The gallery labels missing or partial recordings.

Do not use the same workspace from the course page and the terminal at the same time.

## Talking to the agent directly

The terminal uses the same isolated Codex App Server, with the earlier guided approval flow:

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
frontend/        React workbench source, pinned dependencies and browser tests
scripts/         setup, launch and regression scripts
template/        student workspace template
tasks/           draft configurations of the three tasks; thresholds, workloads and quality tables are all draft
catalog/         hardware, models and infra, with a source for every number
examples/        paper examples; all numbers are made up
agent/           model profiles and framework version compatibility records
config/          API templates; local configurations with keys are ignored by Git
evals/           direction checks and entry points for evaluation scenarios
```
