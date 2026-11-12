# hello-mlsys-lab

The teaching agent for weeks 6–8 of hello-mlsys, with two entry points: a local course page and the terminal. The student states hypotheses and falsification conditions, comments on plans, chooses hardware points and makes the research decisions; Claude Code calls a remote model and mlab to carry out planning, experiments, analysis and validation, and when the student wants to know why simulation and measurement disagree, it helps analyze the data.

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

`scripts/setup.sh` also installs the pinned Claude Code version into `.venv/bin` and checks its SHA256 against the official release manifest; other Claude Code installations on the machine are not affected. The version is `pinned_claude_version` in `agent/compatibility.json`, currently 2.1.317. To upgrade, change this version, rerun `scripts/setup.sh`, then run `scripts/verify-cli.sh --with-claude`.

The course page uses the Claude Agent SDK, which `scripts/setup.sh` installs as well (the SDK version matches the pinned Claude Code version). No local model weights are needed.

## Course page

```bash
mlab web workspaces/my-course --simulator fixture --provider claude-haiku
```

The first run creates the course directory, prints the page address and opens the browser; the server listens only on 127.0.0.1. When connected to a remote server (ssh, VS Code Remote or a tunnel), open the address given by port forwarding. The left column is the session list: a session does one task from the start and has its own records and agent, and the task is chosen when clicking "New session"; sessions of the same task share the hardware budget. At the top of the middle column is the session's directory of records, and below it are the records, charts and, when it is the student's turn, the forms; the right column is the session's agent. The student's words can go into the forms or into the chat; steps the student must confirm appear as confirmation cards. The top bar switches the page between Chinese and English, and the agent replies in the page's language. If the port is taken, add `--port`.

`mlab doctor --web` checks the page's dependencies and port. For a model profile that uses an API key, first set the key the profile requires in an environment variable (for example `DEEPSEEK_API_KEY`), then change `--provider` to the profile name; the key is never written to any file. `--agent fake` switches to the offline fake agent, which calls no model, for walking through the page. Demo steps and the TA checklist are in [Demo steps](docs/demo.md#course-page-demo); the API is in [docs/web-api.md](docs/web-api.md).

Do not use the same workspace from the course page and the terminal at the same time.

## Talking to the agent directly

The current model is Claude Haiku, accessed by logging in with a Claude Code account:

```bash
mlab init --task 9.2 --simulator fixture --provider claude-haiku workspaces/my-sla
cd workspaces/my-sla && ./claude
```

On first launch, trust the workspace (No is selected by default; choose Yes). `./claude` starts the pinned version and loads only the project configuration, keeping personal configuration, plugins and skills out of the experiment; the workspace also records where `mlab` is and which model was chosen, so `./claude` can later be run in it from any terminal.

Replace `--simulator fixture` with `--simulator llm` to have the same model estimate performance; the numbers are still labeled mock. The slow flywheel defaults to the hardware stand-in (`--hardware standin`), which creates four kinds of gap by fixed rules, so comparing simulation with measurement can be practiced in the meantime.

The main interface is Claude Code's native Anthropic Messages API. Profiles for compatible APIs such as Qwen and DeepSeek are also in `agent/profiles/`; to use one, set `ANTHROPIC_AUTH_TOKEN` and change `--provider` to its name.

## Terminal classroom mode

Questions come stage by stage and the student answers the prompts; the agent works in background turns:

```bash
scripts/start-agent.sh --workspace workspaces/my-classroom
```

By default it uses the Claude Code account login and Haiku; `--model` changes the model. To use an API key, copy `config/claude.example.json` to `config/agent.local.json` and fill in the actual URL, model ID and key.
The default is the classroom mode with a real student taking part; `--scripted-student` is a clearly labeled automated acceptance-test student.
When only an OpenAI-compatible API is available, use the configuration template `config/qwen.example.json`, with the local converter as an optional adapter layer; see section 10 of the architecture notes.
The classroom launcher uses the fixture by default, and `--backend llm` switches to the LLM mock; only the fast flywheel is available for now. The numbers serve only to check the workflow and the teaching.

## Offline checks

To check only the mlab tool workflow (a scripted student goes through one simulation round, validation on the simulator and one measurement, and compares simulation with measurement):

```bash
mlab demo --out workspaces/my-first-demo
```

A complete two-round demo of the Claude toolchain that needs no API key:

```bash
python evals/local_classroom.py --protocol anthropic --backend fixture
```

Add `--interactive --workspace workspaces/class-demo-9-2` to type the student's hypotheses, comments and decisions yourself.
This entry point uses a pre-scripted local fake model and does not reflect the real capability of Qwen. Demo steps and commands for a real API are in [section 12 of the architecture notes](docs/initial-agent-architecture.md#12-demo-entry-points-and-cli-additions-in-this-version).

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
