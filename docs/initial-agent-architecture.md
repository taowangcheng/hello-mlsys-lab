# Initial agent architecture

The formal implementation scope and decisions of this version are in the [implementation notes](implementation-v0.1.md);
the item-by-item check against the Plan and the backend contract is in the [conformance check](conformance-v0.1.md). This document keeps the structural explanation and the student walkthrough examples.

## 1. What it is

This is a CLI assistant for teaching a machine learning systems course. The student is responsible for stating hypotheses, predicting experiment results, and making research decisions;
the agent is responsible for turning ideas into configurations, running experiments, reading evidence, and explaining results.

The initial version consists of three parts: Claude Code, the model, and the `mlab` experiment tool. The student talks to the agent directly in `./claude`, or answers stage by stage through the classroom controller. The demo steps are in [demo.md](demo.md).
The `mlab` in this repository is not a complete model execution framework; Claude Code is the off-the-shelf framework that runs the tool loop, and the model provides the reasoning.
For example, Claude Code can be made to use Qwen instead of a Claude model. With model API credentials, no Claude account login is needed.

```text
Student: hypotheses, predictions, approvals, decisions
          ↕
./claude, or the CLI classroom controller classroom.py
          ↕
Claude Code: read files, edit the plan, choose and run tools
          ↕ Anthropic Messages API
Remote model: understand the task, decide the next step, compose explanations

Claude Code
     ↓ Bash calls
mlab CLI: validation, state machine, prediction freezing, batches, budget, comparison, evidence checks
     ↓
Execution backend: fixture / LLM mock
     ↓
Workspace: plan, candidates, run artifacts, student decisions, reports, recovery state
```

**The main model interface is Anthropic Messages.** When the model service supports this interface natively, it is connected directly, with no protocol gateway.
A translation layer is added only when the service has only an OpenAI-compatible interface; see the last section.

A pinned version of Claude Code is installed into `.venv/bin` by `scripts/setup.sh`; its framework source code is not in this repository. The project runs the agent through its command-line program,
and currently needs neither the Claude Agent SDK nor downloaded model weights.

## 2. What each layer is responsible for

| Part | Main responsibilities | What it should not decide for others |
| --- | --- | --- |
| Student | States hypotheses and alternative explanations, enters numeric predictions, confirms the plan, revises based on results | Need not memorize mlab command syntax |
| Classroom controller | Shows the stage, collects student input, carries out student actions once explicitly confirmed, handles saving and recovery | Does not fill in predictions or choose candidates for the real student |
| Claude Code + model | Reads the task, writes the declarative plan, calls mlab, writes the analysis | Does not modify the frozen task or fabricate run results |
| mlab | Enforces rules, maintains the budget and ledger, freezes evidence, computes metrics and conclusions | Does not treat model text as experimental fact |
| Backend | Receives the RunSpec, produces per-request observations and run artifacts | Does not declare on its own that an SLA or a conclusion passes |

A weaker model can get commands or numbers wrong, so permissions and prompts are only aids. Approval hashes, input validation, budgets,
rejection of non-comparable data, run hashes, and the conclusion ceiling are all checked by code.

## 3. Project directory

```text
hello-mlsys-lab/
├── src/mlab/
│   ├── cli/                 # mlab command entry point, JSON output, exit codes
│   ├── contracts/           # Task, Candidate, Plan, RunSpec, results, and the Backend protocol
│   ├── core/
│   │   ├── workspace.py     # experiment/iteration state, checks, approval, execution, comparison, validation, recovery
│   │   ├── store.py         # authoritative SQLite ledger, derived JSONL
│   │   ├── metrics.py       # TTFT/TPOT, completion rate, goodput, DAG critical path, etc.
│   │   ├── evidence.py      # checks structured numeric citations in the analysis
│   │   ├── reporting.py     # generates reports from verified artifacts
│   │   └── io.py            # atomic file writes, hashing, paths, and error handling
│   ├── backends/
│   │   ├── local.py         # worker launch, fixture, artifacts, and backend adaptation
│   │   └── llm.py           # model-generated mock observations, response validation, and caching
│   ├── worker.py            # runs in the background without blocking the student's terminal
│   └── gateway.py           # native Messages entry point, plus the optional OpenAI translator
├── scripts/
│   ├── setup.sh            # creates the Python environment, installs the pinned Claude Code version
│   ├── start-agent.sh      # classroom launch entry point; passes arguments to qwen_demo.py
│   ├── verify-cli.sh       # CLI regression
│   ├── classroom.py        # student interaction, agent turns, confirmation, revision, and recovery
│   └── qwen_demo.py        # shared launch logic; supports Messages/OpenAI; keeps its old file name
├── template/               # AGENTS.md, CLAUDE.md, permissions, and skills for generating student workspaces
├── tasks/                  # frozen task drafts, quality tables, and workloads for 9.1/9.2/9.3
├── examples/               # sample Plans and candidates for each task
├── catalog/                # hardware, model sizes, infra configurations
├── config/                 # sample API configurations; the real local configuration is ignored by Git
├── agent/                  # model profiles and Claude Code version compatibility records
├── evals/                  # entry points for the local toolchain, the direction check, and E1–E11 scenario evaluations
├── tests/                  # contract, metrics, CLI, classroom, backend, and interface tests
└── workspaces/             # student workspaces and run artifacts; ignored by Git
```

`template/AGENTS.md` sets the role boundaries and the way of working; `CLAUDE.md` refers to it, for compatibility with different Claude Code versions.
Business rules are enforced in mlab; the teaching instructions in these files do not replace the gates.

## 4. Four experiment concepts

| Concept | Meaning |
| --- | --- |
| Task | A frozen task, for example 9.2; contains the workload, constraints, decision space, and budget |
| Experiment | A continuing exploration by the student around a task; can span multiple sessions |
| Workflow / iteration | One round of hypothesis, plan, a batch of runs, analysis, and decision |
| Run | A single run for one candidate, one seed, one backend, and one data split |

When the student revises the plan, a new iteration is created. The predictions, approvals, frozen plans, and run evidence of earlier rounds are kept;
the new plan of the second round cannot overwrite the results already seen in the first round.

The student workspace looks roughly like this:

```text
my-classroom/
├── task/                       # copy of the frozen task and catalog
├── AGENTS.md / CLAUDE.md
├── claude                      # launches the pinned Claude Code version, loading only the project configuration
├── .claude/                    # project rules, SessionStart, and skills
├── .mlab/
│   ├── config.json             # task, backend, run timeout
│   ├── ledger.sqlite3          # authoritative state and event ledger
│   ├── ledger.jsonl            # event view exported from the database
│   └── classroom/              # model session ID, isolated configuration, and per-turn tool logs
└── experiments/exp_001/
    ├── state.yaml              # rebuildable index of the current stage
    ├── report.md / report.json
    └── iterations/001/
        ├── hypothesis.md
        ├── plan.yaml / candidates/
        ├── prediction.json / approval.json / frozen_plan.json
        ├── analysis.md / analysis.claims.json / analysis.confirmed.json
        ├── decision.md / decision.confirmed.json
        └── runs/mock_0001/
            ├── spec.json / manifest.json / result.json
            ├── requests.jsonl / events.jsonl / logs.txt
            └── ...
```

## 5. Walking through the flow with one 9.2 experiment

This section explains the flow; the complete commands and input examples for following along with an existing OpenAI-compatible configuration are in section 11.

The student first says: "My guess is that long prefills are blocking short requests; another explanation is overall overload." The controller saves the student's own words.
The agent reads the frozen task and the existing format, fills in the question and `derived_requirements` in `plan.yaml`,
forms candidates such as baseline, chunked prefill, and reduced concurrency, and calls `mlab workflow check`.

Once the check passes, the controller shows the actual configuration, budget, and plan hash. The student fills in the prediction metric for each candidate in turn, for example TTFT p99,
then confirms the approval. mlab binds the predictions to the hash; changing a candidate or the hypothesis after approval invalidates the approval.

Next, the agent calls `submit`, `wait`, and `compare` in turn, reads the actual result files, and writes the analysis.
Explicit numbers in the analysis must also be written to `analysis.claims.json`, for example:

```json
[{"run_id":"mock_0001","metric":"ttft_ms.p99","value":1755.3410429531436}]
```

The number here is only a format example. Actual values must be copied from the current run's results; the code checks the run ID, field, and value.
The structured citation check cannot prove that every sentence of free text is correct, so the student still needs to review the explanation.

After reading the analysis, the student states the reasons for keeping or revising, chooses a candidate, and confirms the decision. The options are:

- **revise**: create a second round and state a revised hypothesis; after the agent adjusts the plan, it goes through approval and execution again.
- **validate**: run repeat and holdout on the selected candidate, then confirm closing.
- **close**: close on the existing evidence. When validation is insufficient or fails, the conclusion is `unresolved`; no successful solution is forced.
- **/quit**: save and exit, and resume later.

By default each round has three candidates and one seed: one development round plus one repeat and two holdouts makes six runs in total;
two development rounds followed by validation make nine. When the number of candidates or seeds changes, the actual count changes with the plan.
The final candidate, metrics, and report path are those given in `[verified final result]` and `report.json`.

## 6. Main interface: Anthropic Messages

The general entry point connects using Claude Code's native model protocol:

```text
Claude Code → model service /v1/messages
```

The model can be Qwen, DeepSeek, or another compatible model offered by the service; a compatible request protocol does not mean the model's capability meets the bar.
The current model is Claude Haiku, using a Claude Code account login, with no configuration file needed. The project records `Qwen3.8-27B` as a candidate model name. The `model` actually sent to the service must be the real ID provided by the platform;
the underlying model version cannot be confirmed from the candidate name or a `default` route alone.

First-time setup:

```bash
# From the repository root; the runtime environment is Linux and Python 3.12. Install uv first; setup creates the environment and installs the pinned Claude Code version
scripts/setup.sh
cp config/claude.example.json config/agent.local.json
```

In `agent.local.json`, fill in `base_url`, `model`, and `api_key` or the corresponding environment variable.
The base URL is the Messages endpoint prefix given by the provider, without `/v1/messages` appended.

```json
{
  "protocol": "anthropic",
  "base_url": "https://YOUR-PROVIDER/anthropic",
  "model": "YOUR-ACTUAL-MODEL-ID",
  "api_key_env": "ANTHROPIC_API_KEY",
  "auth_mode": "api_key",
  "api_key": ""
}
```

`auth_mode=api_key` uses native key authentication through `ANTHROPIC_API_KEY`; if the provider requires Bearer,
change it to `auth_mode=bearer` and, as needed, set `api_key_env` to `ANTHROPIC_AUTH_TOKEN`.
The key is read from, in order, the environment variable, the local JSON, and hidden terminal input. `api_key_env` takes the variable name, not the key.
The local JSON is ignored by Git, but a key saved there is still in a plaintext file; do not share it.

```bash
# Small-scale function-call and tool-result round-trip test
scripts/start-agent.sh --check
# Classroom mode with student participation
scripts/start-agent.sh --workspace workspaces/my-classroom --task 9.2
# Both the agent and the LLM simulator use the model service; performance results are still mock
scripts/start-agent.sh --workspace workspaces/my-llm-classroom --backend llm
# Two-round automated demo explicitly marked as "scripted student"; not equivalent to a trial with real students
scripts/start-agent.sh --workspace workspaces/scripted-classroom --scripted-student
```

With the native interface, model credentials are passed in through the environment of the Claude subprocess and are not written to the generated runtime settings or logs.
Native-protocol requests go directly to the provider, without passing through the local format translator.

## 7. Student confirmation and session recovery

Classroom mode collects student confirmations through the terminal. The agent runs in Claude Code headless turns,
and permission rules forbid it from doing approve, decide, unlock, and close on the student's behalf; after the student confirms, the controller calls the same mlab core interface.
The original workspace template also keeps the ask rules for using interactive Claude Code directly; the two confirmation interfaces are not conflated.
The workspace's `./claude` launches the pinned version and loads only the project configuration; `.claude/settings.local.json` records the directory containing `mlab` and turns off skills, plugins, and connectors synced from claude.ai. When `./claude` is run from any terminal, both the hooks and the Bash tool can find `mlab`.

At every stage the model learns the progress from `mlab next` and the current files; SessionStart injects the state.
A successful turn saves the session ID, and later turns use `--resume`. After exiting, specifying the same workspace resumes it,
with no need to rerun completed experiments from the start. State recovery relies on SQLite and the frozen artifacts, not only on chat memory.

Long tasks run in the background. If a task is still unfinished when `wait` expires, the current turn ends and is resumed later, so the model does not poll repeatedly.
Model requests themselves cannot resume from a checkpoint; lost runs, timeouts, and API errors are recorded and go down the retry/student-handling path.
A third error of the same kind causes a lock; the student must review the records and confirm the unlock.

## 8. Backends, metrics, and evidence levels

| Backend | Source of the numbers | Current use |
| --- | --- | --- |
| fixture | Per-request observations generated from fixed rules and the seed | Offline demos, fault injection, regression |
| llm | The model generates per-request performance estimates from the RunSpec | Verifying LLM simulator calls and the teaching loop |
| Real simulator / AMD | Provided by later adapters | Not yet connected |

The LLM backend uses HTTP when there is an API key; with account login it calls the pinned version's `claude -p`, and the workspace's AGENTS.md and hooks take no part in the estimates.

Backends output observations; the shared metrics module computes TTFT, TPOT, completion rate, throughput, goodput, and DAG metrics.
Rejected, failed, and truncated requests stay in the completion-rate denominator. The current quality tables and workloads are all draft,
and a small-sample p99 cannot serve as a reliable systems conclusion; unmodeled metrics such as per-device utilization in 9.3 are explicitly marked unsupported.

The three fields of a run each answer a different question:

- `status`: whether this run completed.
- `verdict`: whether these numbers satisfy the constraints.
- `evidence_level`: whether the numbers come from a mock, a simulation, or real hardware.

Both current backends are mock, so the experiment's conclusion is capped at `mock_only`. Without sufficient validation evidence it is `unresolved`.
The report keeps the student's original predictions and their error against the development runs; it does not miscount results on different holdout workloads as the accuracy of the same student prediction.

## 9. Implemented scope and later acceptance

Already provided: configurations and samples for the three tasks, contracts, the CLI, freeze gates, background runs, the two-round classroom controller, student confirmation,
repeat/holdout, session recovery, structured numeric checks, immutable analysis/decision snapshots, reports, and evaluation entry points.

```bash
source .venv/bin/activate
python -m pytest -q
# Real Claude Code + local fake Messages service, two-round teaching regression
python evals/local_classroom.py --protocol anthropic --backend llm
# Direction check of the LLM simulator; --fixture only checks the offline program and does not show that the model's directions are sound
python evals/direction_check.py --config config/agent.local.json
# E1–E11 scenario entry point: 10/30-run results for a specific model still require actual runs and manual review
python evals/run.py --profile config/agent.local.json --scenarios E1,E6,E10 --repeat 1
```

The evaluations cover field repair, unsupported mechanisms, repeated failures, long tasks, recovery, attempts to induce an SLA change, metric gaming, large logs, mock labeling, and DAG comparison.
Machine criteria cannot replace human judgment of free text, student intent, and teaching effectiveness. Nor can the whole Plan be claimed as accepted at this point:
full crash injection, the complete scheduling semantics of 9.3, real simulator and AMD adapters, the Web,
multi-model pass rates, and a pilot with 3–5 students are still later work. The current delivery focuses on the end-to-end CLI teaching demo.

## 10. Addendum: when only an OpenAI-compatible interface is available

A key that looks like an OpenAI key says nothing about the interface protocol; this case means a service that supports Chat Completions with function tool calling.
Claude Code uses Messages, so this one case needs translation:

```text
Claude Code → local Messages endpoint → OpenAI Chat Completions → remote model
```

Copy `config/qwen.example.json` to `config/qwen.local.json`, which Git ignores,
and fill in `protocol=openai`, the base URL, the real model, and the key. The base URL usually includes `/v1`; do not extend it to `/chat/completions`.

```bash
scripts/start-agent.sh --config config/qwen.local.json --workspace workspaces/openai-classroom
```

The translator supports text, function-tool round trips, tool-argument JSON, error redaction, and the SSE end events that Claude requires.
The upstream reply is fetched in full first, with heartbeats sent in the meantime, so text does not appear incrementally. Images, audio, server-side tools, and the full set of Claude features are not supported.
Only this mode opens a random local loopback port; the real remote key stays in the gateway's parent process, and Claude uses a local temporary token.
Configuring `extra_body` can add extension parameters the provider supports, but it cannot override key fields such as model, messages, tools, and stream.

Related protocols: [Claude Code gateway](https://code.claude.com/docs/en/llm-gateway-protocol),
[Claude Code permissions](https://code.claude.com/docs/en/permissions).

## 11. Walkthrough: running the 9.2 student-participation demo with an existing OpenAI configuration

This example reuses `config/qwen.local.json`, so the existing key does not need to be entered again, and no Claude login is needed.
You play the student yourself; do not add `--scripted-student`. The remote model acts as the agent, and fixture provides offline mock performance data.
This example goes through the first round, a revised second round, repeat validation, and holdout validation.

### 11.1 Launch

Run from the repository root:

```bash
scripts/start-agent.sh --config config/qwen.local.json --workspace workspaces/student-demo-9-2 --task 9.2 --backend fixture
```

The launcher first runs a small-scale API tool-call check, then enters the classroom. Use a new workspace name for the first run;
using the same name again means resuming the original experiment, and no new experiment is created. If the original experiment is already closed, use a new name for another demo.

Input is needed only when the terminal input prompt `>` or `[y/N]` appears. A question in the agent's output text does not necessarily mean the program is waiting for input;
at other times, wait for it to finish the current stage.

### 11.2 Round one: state a hypothesis

When this prompt appears:

```text
State this round's hypothesis and one alternative explanation (/quit to save and exit).
>
```

Example input:

```text
I think the prefill of long requests is blocking short requests, so chunked prefill should lower short-request latency. Another explanation is that overall concurrency is too high, so lowering concurrency might also improve tail latency. I want to compare these two explanations.
```

Then wait while the agent reads the task, writes the plan and candidates, and runs the configuration check. There is no need to run mlab commands by hand at this point.

### 11.3 Enter predictions and approve

After the check passes, the controller shows the actual candidate configurations, the run budget, and the plan hash, and asks for a prediction for each candidate in turn.
The prediction metric for 9.2 is `ttft_ms.p99`, in milliseconds.

If the plan still uses the following three candidates, these prior guesses can be entered:

| Candidate asked about | Example input |
| --- | ---: |
| baseline | 2400 |
| chunked_256 | 1500 |
| low_concurrency | 2200 |

Enter only one number at a time, for example `2400`, without `ms`. The prediction does not need to be correct; it is used to compare your prior understanding with the experiment results.
The agent may adjust the candidates; go by the actual configuration on screen, and do not copy predictions by name alone.

When the following confirmation prompt appears, check the plan and enter `y`:

```text
Approve the candidates, budget, and predictions above? Plan hash: ... [y/N]
```

Then wait while the agent submits the batch, reads the results, compares them, and writes the analysis. There is no need to open another terminal to run the experiment.

### 11.4 Decide based on the actual results

When this prompt appears:

```text
Which results changed or supported your hypothesis? What do you keep, modify, or reject? Cite run IDs.
>
```

First look at this round's actual results. **Only if the results really show that lowering concurrency improved p99 and the chunked candidate's p99 did not clearly improve**
does the answer below apply:

```text
mock_0003 has a lower p99 than mock_0001, but the p99 of mock_0002 did not clearly drop. So for now I cannot conclude that the main cause is blocking by long prefills; the overall-overload explanation is more worth testing further. I keep the low-concurrency option, and next round I will lower concurrency further as a comparison. This is only mock evidence.
```

If the actual results differ, change this text and the run IDs; do not fabricate grounds just to get through the flow.

Next, a candidate list appears, for example:

```text
Choose a candidate (number or name): 1. baseline, 2. chunked_256, 3. low_concurrency
>
```

To keep the low-concurrency candidate, enter `low_concurrency`, or `3` with the ordering above.
At the prompt "Confirm recording your decision and selecting ...", enter `y`.

### 11.5 Round two: revise and run again

When this prompt appears:

```text
Next: revise, validate, close, or /quit to save and exit.
>
```

Enter `revise`. Then enter the new hypothesis, for example:

```text
The previous round's results better support the overall-overload explanation. This round I want to keep the original baseline as a control, lower the concurrency limit of the low-concurrency candidate from 8 to 4, check whether tail latency keeps improving, and watch whether goodput drops.
```

The 8 and 4 here likewise need to match the first round's actual configuration. The agent creates and modifies the second-round plan, and the first round's evidence is kept.
Then go through the steps again: review the plan → enter predictions → enter `y` to approve → wait for the analysis → explain the decision → choose a candidate → enter `y` to confirm.

If the second round still has the three candidates above, you can predict `1800`, `1800`, and `1200` respectively, or enter your own judgment.
These values are only input examples, not expected correct answers.

### 11.6 Run repeat/holdout validation and close

After the second-round decision is confirmed, enter `validate` at the next-step prompt.
The agent runs a repeat and two holdouts on the selected candidate; wait for the runs to finish.

When this appears:

```text
Close the experiment and compute the conclusion from the actual evidence? [y/N]
```

Enter `y`. After a normal close, the output should be:

```text
[stage] closed
[verified final result] ...
```

With three candidates and one seed per round, two development rounds plus validation make 9 runs in total.
When the mock validation conditions are met, the conclusion is `mock_only`; with insufficient evidence or failed validation it may be `unresolved`.
Do not interpret completed runs or satisfied mock constraints as real GPU performance improvements.

### 11.7 View the report and resume midway

The report for the workspace above is generated at:

```text
workspaces/student-demo-9-2/experiments/exp_001/report.md
workspaces/student-demo-9-2/experiments/exp_001/report.json
```

The Markdown is for reading; the JSON contains the full metrics, the prediction errors for the development stage, and the evidence paths.
The selected candidate and the report path are those in the final result after the code's checks, not just the model's closing free-text summary.

To save and exit midway, enter `/quit` at a student input prompt; Ctrl+C also works.
To resume, rerun exactly the same launch command as in 11.1. The program continues from the authoritative state in the workspace,
and restarting does not automatically rerun experiments that have already completed.

This example uses fixture so that the student interaction loop can be tried first. To have the model also estimate the performance data,
start a new workspace and change `--backend fixture` to `--backend llm`; this adds API calls, and the results are still mock.

## 12. Demo entry points and CLI additions in this version

### 12.1 Full demo without an API key

First run `scripts/setup.sh`, then `source .venv/bin/activate`.
The project pins Claude Code 2.1.317, with the version number in `agent/compatibility.json`; to upgrade, change this version number, rerun `scripts/setup.sh`, then run the toolchain tests.
The local model returns tool calls from a prewritten script, to show the interaction and state management reliably; it does not represent the reasoning ability of the real Qwen.

```bash
# Automatically shows two rounds: 3 development runs → student revision → 3 development runs → 1 repeat → 2 holdouts → report
python evals/local_classroom.py --protocol anthropic --backend fixture

# Play the student yourself; the model is still the local prewritten script, and no key is needed
python evals/local_classroom.py --interactive --workspace workspaces/class-demo-9-2
```

At the end, the automated demo prints `PASS classroom integration` and the workspace path.
The report is at `experiments/exp_001/report.md` in that workspace, and `classroom-verification.json` explicitly marks `real_qwen: false`.
In manual input mode, `/quit` can be entered at a prompt; running the same command again resumes.

Student input can be shown in the following order; the specific candidates and run IDs are as shown in the actual output on screen:

| Prompt | Example input |
| --- | --- |
| Initial hypothesis | I think long prefills are blocking short requests, or concurrency may be too high; lowering concurrency can help tell the two explanations apart. |
| TTFT p99 predictions for the three candidates | Enter 2400, 1500, 2200 in turn, in milliseconds |
| Approve the plan? | After reviewing the candidates, budget, and predictions, enter y |
| First-round decision | mock_0003 has a lower TTFT p99 than mock_0001; mock_0002 did not lower this metric, so for now I keep the overload explanation and do not yet claim that chunking is ineffective. |
| Candidate selection | low_concurrency, then y to confirm |
| Next step | revise |
| Second-round hypothesis | Lower concurrency from 8 to 4, see whether tail latency keeps falling, and check whether goodput drops. |
| Second-round predictions, approval | Enter predictions for the candidates shown, then y |
| Second-round decision | Cite the second round's actual run IDs and state what you keep, what you reject, and the limits of mock evidence. |
| Candidate, next step | Choose low_concurrency, confirm, then enter validate |
| Close after validation | Confirm the repeat and holdout results, then enter y |

This is a demo of the teaching flow. A real model may propose different candidates and explanations; the conclusions in the table must not be copied mechanically.

### 12.2 Demo with your own model API

The main path is still the native Anthropic Messages interface:

```bash
scripts/start-agent.sh --config config/agent.local.json --task 9.2 --backend fixture --workspace workspaces/live-demo-9-2
```

When only an OpenAI-format API is available, start the translator with the existing local configuration:

```bash
scripts/start-agent.sh --config config/qwen.local.json --task 9.2 --backend fixture --workspace workspaces/live-openai-demo-9-2
```

Here the student input is the same as in the table above; the model is a real remote model, and the experiment numbers still come from fixture.
Changing `--backend fixture` to `--backend llm` also has the API estimate performance; use a new workspace for this.
Qwen3.8-27B is a candidate model name; the actual API configuration must use the model ID provided by the provider, and the underlying model cannot be confirmed from an alias alone.

### 12.3 New workloads, holdouts, and timeline

```bash
# Generate a new draft workload with a seed, without overwriting the original task; the same seed gives the same output
mlab generate-workload --task 9.3 --seed 42 --out workspaces/dag-42.requests.jsonl
# Each of the three tasks can run on its own, without a model API
mlab demo --task 9.3 --out workspaces/dag-demo
mlab --workspace workspaces/dag-demo run show mock_0001 --timeline --page 1
```

The 9.1 holdout resamples from the original prompt-length distribution; the 9.2 holdouts raise the share of short and of long prompts respectively, keeping the open-loop arrival times.
The first 9.3 holdout keeps the DAG unchanged and only halves the concurrency limit (when it was 1, it becomes 2); the second resamples by each model's length distribution and rewires dependencies within the same depth, keeping the graph acyclic and the model mix unchanged.
These are all synthetic drafts, not real traces. The generation metadata, seed, and hash are frozen with the task; the run spec and the JSON report record the specific configuration changes.
Existing workspaces keep the old task snapshot; the new holdout protocol applies only to newly created workspaces.
`--timeline` shows, per page, the ready, first-token, and completion times of 4 requests along with the critical-path duration; it is not to be read as real per-device utilization.

### 12.4 Regression entry points

```bash
scripts/verify-cli.sh
# Optional: add the 4 protocol/backend combinations of real Claude + local fake API
scripts/verify-cli.sh --with-claude
```

The tests cover the mock loop for all three tasks, the approval/freeze gates, error recovery, the backend interface, synthetic workloads, and the 9.3-specific holdouts.
New recovery cases force-kill the process writing the ledger and check rollback before commit and retention after commit separately; another case verifies that a worker interruption is classified as lost.
The long-task test injects a 1200-second delay and checks end_turn immediately, without actually waiting 20 minutes.
These tests do not replace a real student pilot, multi-model 10/30-run evaluations, or power-loss fault verification.
