# hello-mlsys: implementation notes for the initial Coding Agent CLI version

Current runtime update (2026-11-12): the default agent is now isolated Codex / GPT-6 Luna. See [Codex integration](codex.md) for the active configuration and entry points. Claude-specific descriptions below document the earlier implementation.

## 1. Goals and scope

This version implements the CLI teaching loop in `docs/plan.md`: the student states a hypothesis and design intent; the agent writes a declarative plan,
calls the experiment tools, and analyzes the evidence; the student enters predictions, approves, and makes a decision, then revises or validates again.

This version uses 9.2 as the complete demo entry point and also provides task drafts for 9.1 and 9.3 with a unified CLI. Execution data comes from fixture or the LLM mock,
and no real simulation or hardware validation conclusions are produced. The Web, the real simulator, the AMD sandbox, and the student pilot still proceed as in the original Plan.

**Delivery boundary**: this is a runnable initial version and code-review branch; it does not mean that the whole six-week Plan has been accepted.
The item-by-item conformance conclusions are in the [check against the contract and the Plan](conformance-v0.1.md); the walkthrough examples are in
[Architecture and the 9.2 student demo](initial-agent-architecture.md).

## 2. Implementation decisions

| Decision | Implementation in this version | Effect |
| --- | --- | --- |
| Agent base | Claude Code CLI + remote model | No Agent SDK; no model weights downloaded |
| Main model interface | Anthropic Messages | Natively compatible services connect directly to Claude Code |
| Student interaction | The student talks to the agent directly in `./claude`; `scripts/start-agent.sh` is the classroom mode that asks questions stage by stage | Predictions, approvals, and decisions are explicitly entered and confirmed by the student |
| Current model | Claude Haiku, using a Claude Code account login | DeepSeek/Qwen profiles are kept, to be connected and tested in practice later |
| Rule enforcement | mlab validation, ledger, budget, and hashes | Prompts do not serve as business-rule gates |
| Authoritative state | SQLite WAL + FULL | JSONL and state.yaml are rebuildable views |
| Experiment backends | fixture / LLM mock | Both have evidence level mock |
| Backend interface | capabilities / submit / poll / cancel / fetch | Interface tests shared by both backends are provided |
| Reports | Markdown and JSON generated from frozen artifacts | The model's free-form summary is not the final source of facts |

## 3. Overall architecture

```text
Student: hypotheses, predictions, confirmations, research decisions
              ↕
classroom.py: stage interaction, student actions, session recovery
              ↕
Claude Code CLI ⇄ Anthropic Messages ⇄ remote model
       ↓ Read / Edit / Write / Bash
mlab: contracts, validation, state, prediction freezing, budget, comparison, reports
       ↓ Backend interface
fixture / LLM mock → spec and five kinds of run artifacts
       ↓
Authoritative SQLite ledger + workspace files
```

The workspace template contains AGENTS.md, CLAUDE.md, permissions, SessionStart, and thin wrapper skills.
The model uses only the main agent, with no subagents or external MCP enabled. The pinned version of Claude Code is installed into `.venv/bin` by `scripts/setup.sh`.

## 4. Student flow and states

One round goes: hypothesis → deriving requirements → candidates → student prediction and approval → runs → analysis → student decision → validation or the next round.

| Stage | Agent | Student/controller |
| --- | --- | --- |
| Hypothesis | Reads the frozen task and the student's intent | Enters the hypothesis and an alternative explanation |
| Plan | Edits the Plan/candidates and runs check | Reviews the configuration, budget, and hash |
| Approval | Cannot approve on the student's behalf in classroom mode | Predicts for each candidate and confirms |
| Run | submit, bounded wait, compare | Can exit during long tasks and resume later |
| Analysis | Writes the analysis and structured numeric citations | Reviews the evidence and the attribution |
| Decision | Does not choose a solution for the real student | Explains what is kept/revised/rejected, chooses a candidate, and confirms |
| Revision | A new iteration keeps the old evidence | States a revised hypothesis |
| Validation | repeat and holdout | Confirms closing, or continues revising |

In `./claude`, approval, decision, unlock, and close are confirmed in the same window through `permissions.ask`; this path has been run through two rounds with Haiku in a real terminal.
In classroom mode, the controller performs student actions through the same mlab core interface, and the agent's permissions forbid it from performing these actions on the student's behalf.
The scripted student is used only for automated demos and evaluations, and all related records are explicitly marked with its identity.

## 5. CLI, contracts, and evidence

Main commands: `init`, `next`, `task show`, `workflow check/approve/submit/wait/status/decide`,
`compare`, `validate`, `lint`, `report`. The full list of commands is shown by `mlab --help`.

- A Plan must contain the question, a hypothesis reference, derived_requirements, candidates, and seeds; candidates are constrained by the frozen task's decision space.
- Approval binds the plan, candidate, and hypothesis hashes; submission is rejected once approved content has changed.
- The backend input is the six-tuple workload, infra, strategy, calibration, evaluation, execution.
- Each run saves spec.json, manifest.json, result.json, requests.jsonl, events.jsonl, and logs.txt.
- Metrics are computed by the shared metrics module from per-request observations; failed/rejected/truncated requests stay in the completion-rate denominator.
- status, verdict, and evidence_level represent the run state, the constraint verdict, and the evidence source respectively.
- Unsupported metrics are explicitly null with a reason attached; active mechanisms that are not implemented are rejected before submission.
- Numeric citations in the analysis can be checked by run ID, metric field, and value; after confirmation, snapshots of the analysis and the decision are saved.
- report is generated from hash-checked evidence and records the student's predictions, the errors on development runs, the decision, and the validation results.

Non-TTY CLI output stays short JSON; large output goes to a file and its path is returned. The classroom interface itself uses readable text.
The current backends have no calibration domain, and passing in a calibration profile is rejected; future calibration/hardware-measurement adapters are not completed items in this version.

## 6. Backends and result boundaries

| Backend | Supported scope | Limitations |
| --- | --- | --- |
| fixture | Fixed seeds, chunking/concurrency teaching directions, rough model/TP/replica and DAG scheduling, fault injection | Hand-written rules; do not represent physical performance |
| LLM mock | Model-generated per-request estimates, validation and caching, unified artifacts | Valid JSON does not mean sound directions; direction evaluations with a real model must be run |

fixture does not support KV budget, preemption, or prefix cache, and returns E_MECHANISM_UNSUPPORTED.
The LLM can generate mock estimates for these mechanisms, but direction and reliability checks are still required per the Plan.
Unmodeled metrics such as per_device in 9.3 explicitly return null; the holdouts are synthetic drafts generated at initialization.
A seeded workload generator for the three tasks has been added: for 9.2 it changes the short/long prompt ratio; for 9.3 it separately validates a single-dimension concurrency change and a new DAG from the same distribution.
Run snapshots and reports store holdout_change; `run show --timeline --page N` shows the request timeline page by page.

The backend version and the model/endpoint identity are part of the cache or idempotency keys; a conclusion cannot mix validation evidence from different backend versions.

## 7. Launch configuration

The main configuration template is `config/claude.example.json`; copy it to `config/agent.local.json`, which Git ignores,
and fill in the service's Messages endpoint, the real model ID, and the key. The key can come from the process environment, the local JSON, or hidden input,
and is not written to the generated runtime settings or logs. A key saved in the local JSON is plaintext and should not be shared or committed.

```bash
# From the repository root; install uv first. setup also installs the pinned Claude Code version
scripts/setup.sh
cp config/claude.example.json config/agent.local.json
# After editing the local configuration
scripts/start-agent.sh --check
scripts/start-agent.sh --workspace workspaces/my-classroom --task 9.2
# To also use the model as the performance-estimation backend, use a new workspace
scripts/start-agent.sh --workspace workspaces/my-llm-classroom --backend llm
```

The runtime environment is Linux and Python 3.12. Claude Code is pinned to `pinned_claude_version` in `agent/compatibility.json`;
`scripts/setup.sh` verifies it against the official release manifest and installs it into `.venv/bin`, leaving other Claude Code installations on the machine unaffected;
both the workspace and the classroom set `DISABLE_UPDATES=1`, and the launcher refuses to start when it finds a version mismatch.
To upgrade, change this version number, rerun setup, then run `scripts/verify-cli.sh --with-claude`.

`mlab init` creates `./claude` in the workspace: it launches the pinned version and uses `--setting-sources project,local` to load only the project configuration.
`.claude/settings.local.json` records the directory containing `mlab` and turns off skills, plugins, and connectors synced from claude.ai.
When `./claude` is run in the workspace from any terminal, both the hooks and the agent's Bash tool can find `mlab`; the classroom launcher likewise puts the current Python's bin directory at the front of PATH.
The LLM backend uses HTTP when there is an API key; with account login it calls the pinned version's `claude -p`, using an empty `--setting-sources` to keep out the workspace's AGENTS.md and hooks.
Each candidate is estimated separately, and the prompt gives a common scale; under account login, Haiku passed the direction check 5 times in a row.
A model interface error does not use up budget; `mlab next` enters `retry_backend`, and `mlab workflow submit` reruns only the failed runs.

The classroom defaults to fixture and `mlab init` defaults to LLM; either can be chosen explicitly, and offline numbers are not passed off as model estimates.
Without `config/agent.local.json`, classroom mode uses the Claude Code account login (Haiku by default; `--model` changes it).
Qwen3.8-27B is kept as a candidate model name; the platform's real model ID must be confirmed separately.

## 8. File structure

```text
src/mlab/contracts/       Pydantic contracts and JSON Schema
src/mlab/core/            workspace, ledger, metrics, evidence checks, and reports
src/mlab/cli/             CLI and error output
src/mlab/backends/        unified interface, capability checks, fixture, and LLM
src/mlab/worker.py        asynchronous worker
scripts/classroom.py     student controller and Claude turns
scripts/start-agent.sh   classroom launch entry point
scripts/setup.sh         creates the Python environment, installs the pinned Claude Code version
scripts/verify-cli.sh    CLI regression
template/                student workspace template
tasks/, examples/        frozen task drafts and Plan/candidate examples
config/, agent/          API templates, model profiles, version records
tests/, evals/           automated tests and model evaluation entry points
```

The current installation method is a repository checkout plus an editable install; distribution of data resources packaged in a standalone wheel has not been accepted yet.

## 9. Verification and acceptance status

```bash
source .venv/bin/activate
python -m pytest -q
python -m pytest tests/test_backends.py --backend llm -q
python evals/local_classroom.py --protocol anthropic --backend llm
python evals/direction_check.py --config config/agent.local.json
python evals/run.py --profile config/agent.local.json --scenarios E1,E6,E10 --repeat 1
```

On Linux, 75 CLI regression tests pass (43 core and CLI acceptance, 21 backend and API interface, 11 runtime environment); with `--with-claude`, 79 in total.
The two-round, 9-run demo with real Claude Code 2.1.317 and a local fake API passes; this is not equivalent to acceptance of real-model effectiveness.
CI provides pull_request and manual triggers; the fixture and local fake API tests do not need a real key.
The directions of other models, the pass rate of weaker models over repeated runs, and the student pilot still need to be accepted separately.

## 10. Differences from the Plan and later work

| Item | Current status | Later |
| --- | --- | --- |
| CLI teaching loop and second-round revision | The current 75 CLI regression tests pass; two rounds pass with a real model | Teaching quality and real-model evaluation |
| Native Messages | Main path | Re-verify on every model/version change |
| Student confirmation | Ask confirmation in `./claude` has been verified in a real terminal; in classroom mode the controller confirms | Re-verify on more models |
| State recovery | Force-kill before/after transaction commit, lost worker, and recovery via next have been verified | Power loss and the remaining failure windows |
| W2 fields and 9.2 goodput | Explicitly adopts the TTFT-only exception per Plan 5.5 | Restore the dual threshold once TPOT is finalized |
| Three tasks | Loop for all three tasks; seeded workloads, 9.3-specific holdouts, and a paginated timeline | Real scheduling and per-device utilization are later backend work |
| Evaluation | E1–E11 entry points provided | Multi-model 10/30 runs and manual review |
| Later backends | Interface reserved; currently only fixture/LLM | Real simulator, AMD, calibration, and cross-evidence comparison |
| Web and pilot | Not implemented/not accepted | Proceed in later phases of the original Plan |

## 11. Optional: OpenAI-compatible interface translation

When only an OpenAI Chat Completions interface is available, create a local configuration from `config/qwen.example.json` and set protocol=openai.
Claude Code still uses Messages; the local translator handles text and the function-tool protocol, and the remote key stays in the parent process.

```bash
scripts/start-agent.sh --config config/qwen.local.json --workspace workspaces/openai-classroom
```

The translator supports only the current text/function-tool subset, sends heartbeats while waiting for the complete upstream response, and does not support the full Claude platform features.
Native Messages services do not start this translator. The complete step-by-step 9.2 input example remains in section 11 of the architecture document.
