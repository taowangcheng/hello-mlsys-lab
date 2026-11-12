# Demo: learning through conversation and experiments

The default page keeps navigation on the left, the task or evidence in the center, and conversation on the right. Students ask questions, work through explanations, decide what to try, request simulations or sandbox measurements, and revisit evidence while talking. The examples below are possible investigations, not a required sequence. Performance results use the synthetic fixture and hardware stand-in.

## Course page demo

From the repository root, after `scripts/setup.sh`:

```bash
source .venv/bin/activate
mlab web workspaces/conversation-demo --provider codex-luna --simulator fixture --no-opening
```

Open the printed address, or its SSH/editor port forward, and choose a task. For SoC Qwen, start a fresh course with `scripts/start-soc.sh workspaces/soc-conversation-demo` instead. See [Codex integration](codex.md) for credentials, isolation and provider limits. An offline preview can use `--agent fake`; its experiment controls and scripted execution requests work, but it does not provide model explanations.

Start with a question that needs no experiment: "What does this task ask me to learn?" or "Why can throughput improve while response time gets worse?" Continue in your own words. The assistant should explain the question, not require a hypothesis form or turn every answer into another run.

| Task | Conversation to try | What to check |
| --- | --- | --- |
| 9.1: on-device assistant | Ask how model size, precision and memory interact. Request a draft comparing `int4_7b` and `fp8_3b`, discuss what that comparison can establish, then ask to run it. | Drafting alone starts nothing. An explicit simulation request runs the selected experiment. The discussion recognizes that both model size and precision changed. |
| 9.2: serving under an SLA | Ask for a comparison of concurrency 16 and 20. After simulating, ask which earlier evidence helps explain the latency margin. Use **Add from history** or `@` to attach a result, then request measurements for one configuration. | References identify the correct session and run. The measurement card shows the chosen workloads, repetitions and budget. Confirming continues the discussion; cancelling spends no measurement budget. |
| 9.3: dependent workload | Ask how a dependency chain changes the interpretation of latency. Bring in runs from different experiments and ask what explains their different completion times. Open a cited run to inspect its request timeline, then return to the question. | The conversation remains available while inspecting evidence. The assistant distinguishes a controlled precision comparison from a broader architecture comparison, and simulation evidence from sandbox measurements. |

These are independent examples. Students may change direction, inspect old work, start another draft, or ask a conceptual follow-up at any point. **New conversation** creates a clean session in the same task, with empty evidence, notes, report and attachments. Earlier drafts, results and conversations remain in their original sessions; reopen one from the session list to continue it. Only the hardware budget is shared. **Add from history** or `@` attaches records from the current session. Typed questions and attachments remain available while navigating within it.

### Direct experiment controls

The same work can be arranged with page controls while continuing the conversation. To try this task 9.2 example, open **Experiments** first:

| Investigation | Page actions | What to inspect |
| --- | --- | --- |
| Compare two concurrency settings | Click **New experiment**, name it "Concurrency near the SLA", and write the question you want to investigate. Rename the first configuration `c16` and set **Maximum concurrency** to 16. Use **Add a variant** to create `c20` with concurrency 20, keeping other settings unchanged. | Both configurations are editable. Writing an expectation is optional. **Save draft** keeps the draft on the server; unsaved edits are retained in this browser. |
| Run the simulation | Keep the development workload and one repetition, click **Run simulations**, review the two configurations and click **Start simulations**. | **Runs** lists both executions, their latency, goodput and constraint verdicts. Finishing successfully does not necessarily mean meeting the SLA. |
| Examine an individual result | Open the `c20` run. Inspect **Configuration** and **Requests**, then write an observation under **Observations**. Return with **All runs**. | The saved configuration and request timings stay attached to this run. Search and filter settings survive returning to the list. |
| Compare the evidence | Select both completed runs and click **Compare results**. Change the baseline if useful. | The table shows the changed concurrency setting alongside the performance metrics; the plot includes the SLA threshold. |
| Choose a measurement | Return to **Runs**, click **Arrange measurement**, choose the experiment and select only `c20`. Write why this result needs checking, then click **Review measurement**. | The preview shows one hardware run and the remaining task budget. It reuses a matching simulation or prepares one without spending hardware budget. |
| Measure and investigate the gap | Click **Confirm and measure**, open the new measurement result and follow **Matching simulation** or **Compare this pair**. | With the fixture and stand-in, `c20` is around 1940 ms in simulation and 2370 ms in measurement: the execution completes, but the measurement violates the 2000 ms SLA. These are demonstration numbers, not GPU measurements. |
| Continue from earlier work | In a run detail, click **Use this configuration**, or use **Duplicate** in the experiment list. Adjust one parameter and give the new experiment a name. | The earlier configuration and its results remain unchanged. Other drafts can remain open while this experiment runs. |
| Discuss or record a conclusion | Select evidence and click **Discuss these results**, or add it from the conversation's history picker. Record your own observations in **Notes & conclusions**. **Submit conclusion** lets you review a configuration's evidence. | The assistant can help plan and run requested experiments. Observations and conclusions are the student's own actions. A conclusion without the required validation is marked as needing more evidence. Submitting does not end exploration. |

Students may also click **Arrange measurement** while editing a draft, without first running a simulation round. The measurement preview records the selected configuration and prepares any missing matching simulation. Hardware work still requires the separate confirmation. There is no need to submit a decision about a previous experiment before creating another one.

Use the center pane's directory to open a configuration's validation matrix and all its runs, or a measurement's predictions, observed differences, intervals and request ratios. **Thinking and reflection** keeps earlier hypotheses and decisions visible and accepts new observations without a required form sequence. **Report** shows the full current report and linked evidence; it can be saved before a conclusion is submitted, with missing validation still visible.

### Workbench checklist

- [ ] Ask a task question with no experiment or selected evidence, then pursue a conceptual follow-up without starting a run.
- [ ] Use **New conversation** within the task and confirm its conversation, drafts, runs, attachments, notes and report start empty. Reopen the earlier session to recover its work. Check that the task's spent hardware budget is unchanged.
- [ ] Request a draft and revise the idea in conversation; request simulation separately and discuss its actual results.
- [ ] Attach an earlier run from the current session using search or `@`, without leaving the result under discussion.
- [ ] Inspect a cited result and return with the typed question and attachments intact; revisit the sent message's original references.
- [ ] Request a sandbox measurement in conversation, inspect its details and budget, then test both cancellation and confirmation. Confirmation continues the same discussion.
- [ ] Create two drafts without running or concluding the first one; switch between them and recover unsaved edits after a refresh.
- [ ] Complete simulation and measurement using only page controls, without sending a chat message.
- [ ] Select runs from different experiments, change the comparison baseline, and inspect the listed comparison conditions.
- [ ] Reopen an earlier run and duplicate its configuration; confirm its original result is unchanged.
- [ ] Write an observation linked to a result and find it again in the session's notes.
- [ ] Open configuration and measurement records in the center pane, including request ratios, without losing the conversation.
- [ ] Save a report before submitting a conclusion and check that unanswered questions and missing validation remain visible.
- [ ] Keep a measurement pending while exploring another experiment, then reopen its review before spending hardware runs.
- [ ] Switch Chinese/English and light/dark themes; inspect the page at a narrow width.
- [ ] Keep all displayed fixture and stand-in numbers clearly identified as demonstration data.

The automated browser checks use a fresh disposable course and no live model. Their setup and artifact locations are in [Frontend verification](web-frontend.md#build-and-verify).

## Record the conversation experience

Start a fresh SoC course from the repository root, with opening greetings disabled:

```bash
scripts/start-soc.sh workspaces/studio-review/live-course --port 8878 --no-browser --no-opening
```

After installing the isolated browser described in [Frontend verification](web-frontend.md#build-and-verify), run in another terminal:

```bash
cd frontend
PLAYWRIGHT_BROWSERS_PATH=../.venv/playwright node_modules/.bin/node scripts/record-conversation.mjs
node_modules/.bin/node scripts/build-review.mjs --zip
```

The recorder uses **New conversation** on the page for each task, checks the new session and unchanged task budget, asks a conceptual question, requests two simulations, attaches a current-session result through the `@` history picker, and requests one sandbox measurement. It checks the proposed configuration, workload, seed and count before confirming the measurement on the page. All changes use the UI; direct API calls only read verification data.

It also records comparison beside the conversation, measurement analysis, saving a report, the mobile conversation, a second clean session and returning to the earlier one. This recording checks those interactions on the development workload; it does not submit a conclusion or complete repeat and holdout validation.

Each task exports 15 numbered screenshots, `conversation.json`, `conversation.md`, `summary.json` and a normal-speed `walkthrough.webm` under `workspaces/studio-review/live/task-9.x/`. Open `workspaces/studio-review/index.html` for the gallery or share `workspaces/studio-review/studio-review.zip`. Extract the ZIP before opening its index. The transcript includes references and action cards, and partial exports are saved after each completed turn and on failure. `summary.json` in the `live` directory collects task results. The recorder accepts optional task IDs and uses `MLAB_REVIEW_URL` and `MLAB_REVIEW_DIR` to override the server and output directory. It consumes one measurement run per task; a new conversation does not restore spent budget.

The gallery is a static snapshot: rerun `build-review.mjs` after new recordings arrive. Use the same `MLAB_REVIEW_DIR` for the recorder and gallery builder; the builder writes the gallery, README and optional ZIP to that directory's parent. Only named public recording artifacts enter the ZIP; course state, raw capture files and logs are excluded. Missing or failed recordings remain visibly incomplete.

A task may also include `quality-review.json` with reviewer notes about unsupported or overstated claims. These notes appear separately from the unchanged conversation and are included in the review bundle. Each note can provide `severity`, an original `messageId`, an `issue` and supporting `evidence`; recording completion is distinct from explanation quality.

## Record all three tasks with a real assistant

The recorder runs a complete example in each task: a real assistant proposes a draft, the student edits and confirms execution, simulations cover the available workloads, measurements include repetitions and holdouts, and the student submits a conclusion. It saves the assistant's actual replies without rewriting them and refreshes the partial transcript after every completed turn, so earlier replies remain available if a later step fails. The performance backends are still the fixture and hardware stand-in.

This script exercises direct experiment controls with real discussion. Its earlier recordings document the preceding workbench layout; they do not verify the newer conversation-started simulations, history attachments or in-chat measurement cards. Use the conversation checklist above when reviewing those interactions.

Task 9.3 also compares the unchanged `tp2_bf16` reference with the initial controlled pair. Its six simulations run as a separate experiment so that both seeds and all workloads fit within the twelve-run batch limit. The task therefore covers three configurations without requiring one fixed sequence or one large batch.

Start a separate local server from the repository root using a fresh course directory. The launcher reads the SoC credential privately; do not put it in a command or recording:

```bash
scripts/start-soc.sh workspaces/all-tasks-review/course --port 8878 --no-browser --no-opening
```

In another terminal, after installing the isolated browser described in [Frontend verification](web-frontend.md#build-and-verify):

```bash
cd frontend
PLAYWRIGHT_BROWSERS_PATH=../.venv/playwright node_modules/.bin/node scripts/record-all-tasks.mjs
node_modules/.bin/node scripts/build-evidence-index.mjs
```

With no arguments, the recorder visits 9.1, 9.2 and 9.3 in order. To record particular tasks, append their IDs, for example `scripts/record-all-tasks.mjs 9.2 9.3`. Each recorded task creates a new session, but all sessions share the task's measurement budget. The recording needs 3 measurement runs for task 9.1 and 4 each for tasks 9.2 and 9.3. Use a fresh course for another full take. A repeated invocation replaces that task's exported screenshots and transcript in the same output directory; use a separate output directory to retain different takes.

`MLAB_REVIEW_URL` selects the running server and defaults to `http://127.0.0.1:8878`. `MLAB_REVIEW_DIR` selects the recording directory; by default, both scripts derive the repository location from their own path and use `workspaces/all-tasks-review/live`, independently of the working directory. Use the same directory for the recorder and index generator. The generator writes `index.html` and `README.md` into that directory's parent and can be rerun while tasks are still recording; missing artifacts appear as pending.

Open `workspaces/all-tasks-review/index.html` for the gallery. Each task links its numbered screenshots, full `conversation.md` and `conversation.json`, public `evidence.json`, and `walkthrough.webm`. If a `walkthrough.mp4` is added beside the WebM, the gallery offers both. Videos use normal recording speed; the gallery applies no speed change. The index reads only these public recording artifacts, not credentials, private reasoning or internal tool logs.

## Legacy guided terminal story

The remaining sections document the earlier guided flow used by terminal demonstrations and the `/classic` page. They are retained to explain existing recordings and records. Their required sequence and confirmation cards do not describe the default workbench.

The student writes the hypothesis and falsification condition, comments on the plan, chooses hardware points and makes the decisions; to find out why simulation and measurement disagree, the student asks the agent to help analyze the data. The agent plans, runs and drafts the records. The fast flywheel is the simulator, with no limit on runs; the slow flywheel is the hardware stand-in, with a budget of 8 runs. The flow is two simulation rounds and two measurements, about 7 confirmation prompts. The current agent is Codex / GPT-6 Luna; duration depends on model response times. All numbers are placeholder numbers, and the hardware stand-in is not real hardware.

## Setup

```bash
scripts/setup.sh                # first time only: installs Python dependencies and checks the supported Codex version
source .venv/bin/activate
mlab doctor                     # codex_version_matches_pin should be true and auth should be present
mlab init --task 9.2 --simulator fixture --provider codex-luna workspaces/demo2
cd workspaces/demo2 && ./codex
```

Log in once with the installed `codex login`, or set `OPENAI_API_KEY`. The lab uses a separate state directory and reads the existing login without modifying it; see [Codex integration](codex.md).

## Steps

Type the lines below in order. In steps with an entry under "Confirmation", the lab asks for confirmation in the same window; read the command, then choose Yes.

| Step | Student types | Confirmation |
| --- | --- | --- |
| 1 State a hypothesis | I think the p99 of short requests gets worse because long prefills block decode, but it could also just be overall overload. If changing only the chunking clearly lowers p99, it's blocking; if only lowering concurrency helps, it's overload. Please start by designing a controlled experiment with the three existing candidates and one seed. | |
| 2 Comment on the plan | I've read the plan: keep all three candidates. baseline is the control, chunked_256 changes only the chunking, and low_concurrency only lowers concurrency, so the variables are properly controlled. No changes. Please record my comments, then approve and run. | Approve |
| 3 Round 1 decision | Chunking improved only the median and p99 didn't change; the SLA was met only after lowering concurrency, so the results support overload and rule out blocking. My decision: keep low_concurrency and reject the chunking-only approach. Please write this into decision.md, confirm the decision, and select low_concurrency. | Decide |
| 4 Revise the hypothesis | Let's do another simulation round. New hypothesis: excessive concurrency is the main cause, but concurrency 8 may be too conservative; if concurrency 24 also meets the SLA, I'm wrong. Please create a new round with three candidates named c16, c20 and c24, with concurrency set to 16, 20 and 24 respectively, all other settings the same as low_concurrency, and one seed. | |
| 5 Comment on the plan | The plan is fine: the three candidates differ only in concurrency. No changes. Please record my comments, then approve and run. | Approve |
| 6 Round 2 decision | Concurrency 20 is the highest concurrency that meets the SLA, but in the simulation it is only 3% below the SLA. I'll choose c20 for now. Please write this into decision.md and confirm the decision. | Decide |
| 7 Choose hardware points | The simulated margin is too small, so I want to go to hardware. Please create a new measurement: measure c20 once, because I want to choose it but in the simulation it is only 3% below the SLA; and measure low_concurrency from round 1 once, as a low-concurrency control. | |
| 8 Approve the measurement | I confirm these two points, using 2 runs of the hardware budget in total. Please approve and submit the measurement. | Approve measurement |
| 9 Compare simulation and measurement | The measurement results are in. Please help me compare the simulation with the measurement: where do they disagree, and what could be the cause? | |
| 10 Points for final validation | c20 violated the SLA on hardware, so I reject it and switch to c16, which has a larger margin. Please create a new measurement for final validation: measure c16 twice on the development workload and once on each of the two holdout workloads. The reason: c16 has to be validated on hardware, and repeating it once shows the noise. | |
| 11 Approve the measurement | I confirm these four runs, using 4 runs of the hardware budget in total. Please approve and submit the measurement. | Approve measurement |
| 12 Submit a conclusion | All four hardware runs meet the constraints. Please submit the conclusion with r002/c16, then generate the report. | Submit conclusion |

The agent's wording differs from run to run; when it asks a question, answer it first, then go on to the next step. When it proposes adding candidates, seeds or hardware points, answer "No, do what I said"; otherwise the expected results below will not match. In step 9 the agent runs `mlab align` to get the data, explains whether the two sides are comparable, whether the gap is noise and whether it would change the decision, and gives likely causes; this step needs no confirmation and writes no record.

## Expected results

The SLA is TTFT p99 below 2000 ms. The tables show TTFT p99 (ms).

**Simulation (fast flywheel)**

| Round | Candidate | Concurrency | Simulation | Constraint |
| --- | --- | ---: | ---: | --- |
| 1 | `baseline` | 32 | 2495 | Violated |
| 1 | `chunked_256` | 32 | 2495 | Violated |
| 1 | `low_concurrency` | 8 | 1386 | Met |
| 2 | `c16` | 16 | 1755 | Met |
| 2 | `c20` | 20 | 1940 | Met, 3% margin |
| 2 | `c24` | 24 | 2125 | Violated |

**First measurement**

| Candidate | Simulation | Measurement | Gap | Beyond noise | SLA verdict |
| --- | ---: | ---: | ---: | --- | --- |
| `low_concurrency` | 1386 | 1562 | +12.7% | Yes, all requests shifted | Unchanged |
| `c20` | 1940 | 2370 | +22.2% | Yes, all requests shifted, the slowest requests the most | Flipped to violated |

**Second measurement (`c16`)**

| Workload | Simulation | Measurement | Gap |
| --- | ---: | ---: | ---: |
| Development, run 1 | 1755 | 1960 | +11.6% |
| Development, repeat | 1755 | 1959 | +11.6% |
| Short-prompt holdout | 1677 | 1902 | +13.4% |
| Long-prompt holdout | 1740 | 1955 | +12.4% |

All four hardware runs are about 12% slower than the simulation, but all meet the SLA: `c16` passes the development, repeat and both holdout workloads on hardware, the conclusion submitted with `r002/c16` is `mock_only`, and 6 runs of the hardware budget are used. The round 2 decision chose `c20`, which violates the SLA on hardware; submitting with it gives `unresolved`. Finally, open `report.md` in the workspace: it contains the submitted conclusion; each round's hypothesis, comments and decision; the comparison of simulation and measurement for both measurements; and the student's own words, recorded verbatim.

## Talking points

- The student does not report numbers. Numeric predictions come from the simulator and are frozen before the measurement; the student contributes the hypothesis and falsification condition, comments on the plan, rationales for hardware points, and decisions.
- When simulation and measurement disagree, the student can ask the agent for an analysis. The agent gets the paired data with `mlab align` and helps the student check three things: whether the two are comparable (effective configuration), whether the gap is noise (the same requests compared one by one), and whether it affects the decision (SLA verdict and ranking).
- On this stand-in, the simulation is optimistic by about 12% overall: every request of the low-concurrency candidate and of `c16` is about that much slower. Concurrency 20 is 22% slower, with the slowest requests shifted the most, which looks like a mechanism that appears only under high concurrency, and the SLA verdict flips. So when relying on the simulation alone, the margin has to be larger than this gap: `c16` has a simulated margin of about 12%, and only about 2% is left on hardware.
- Calibrating the simulator is the simulator side's responsibility and is not part of this flow.
- The hardware stand-in creates four kinds of gap by fixed rules: measurement noise, inaccurate parameters, a missing mechanism, and differences in how a strategy is implemented (for example, `chunked_256` chunks at 512 on the stand-in). All numbers are made up.

## Legacy guided page demo

The same terminal story, shown on the earlier `/classic` page. The unit of work on the page is a session: a session does one task from the start and has its own records and agent. The student's words can go into the forms in the middle column or into the chat in the right column.

```bash
mlab web workspaces/web-demo --simulator fixture --provider codex-luna
```

The terminal prints the page address. Open that address with `/classic` appended for this legacy demonstration; when connected to a remote server, open the address given by the editor's port forwarding (in VS Code, the "Ports" panel). If port 8765 is taken, add `--port` with a free port. In the left column, click "New session" and choose "9.2 Single-node serving under an SLA". The steps below use forms, chat, quick replies and confirmation cards at least once each; any step can instead be done by typing the student's line from the table in the previous section into the chat, and the agent writes the words into the files and brings up a confirmation card when confirmation is needed.

| Step | What to do on the page | Confirmation card |
| --- | --- | --- |
| 1 State a hypothesis | In the middle column's "Hypothesis" form, fill in the hypothesis, the alternative explanation and the falsification condition, then click "Save and notify agent"; then say in the chat "Please start by designing a controlled experiment with the three existing candidates and one seed." | |
| 2 Comment on the plan | On round 1's "Plan" tab, choose "Keep" for all three candidates and write the reasons, then click "Save comments and approve" | |
| 3 Round 1 decision | On the "Analysis & decision" tab, write the decision, cite runs with "Insert run", select `low_concurrency`, and click "Save and confirm" | |
| 4 Revise the hypothesis | In "Next step", choose "Another simulation round"; write the new hypothesis in the new round's hypothesis form; in the chat, say the second half of the step 4 line from the previous section (the names and concurrency of the three candidates) | |
| 5 Comment on the plan | Click the quick reply "Keep all and approve"; the agent records the comments in `review.md`, then runs the approve command | Approve the plan |
| 6 Round 2 decision | In the decision form, write the decision, select `c20`, and click "Save and ask the agent to confirm" | Confirm the decision |
| 7 Choose hardware points | In "Next step", choose "Choose hardware points"; on the measurement page, add `c20` and round 1's `low_concurrency`, write the rationales, and click "Save and ask the agent to check" | |
| 8 Approve the measurement | Click the quick reply "Approve the measurement" | Approve the measurement: uses 2, 6 left after approval |
| 9 Compare simulation and measurement | On the measurement page, look at the comparison of simulation and measurement, and click "Per-request ratios" to compare the two runs; click "Ask the agent to analyze" and send the question it puts in the chat | |
| 10 Points for final validation | In "Next step", choose "Choose hardware points"; add `c16`: 2 runs on the development workload and 1 run on each of the two holdout workloads, and write the rationales | |
| 11 Approve the measurement | Click "Approve measurement (4 runs)" | |
| 12 Submit a conclusion | In "Next step", choose "Submit a conclusion", select the configuration `r002/c16`, review the conclusion preview, then click "Confirm" | |

The expected results are the same as in the terminal version above: `r002/c16`, `mock_only`, 6 runs of the hardware budget used. The report is on the "Report" page and in `report.md` in this session's workspace; expand "Directory" at the top of the middle column, where the configuration `r002/c16` brings together all of its simulations and measurements.

**Offline walkthrough**: `mlab web workspaces/web-offline --agent fake --simulator fixture` uses the offline fake agent and calls no model. It does the agent's steps by `mlab next` and still sends confirmation cards for commands that need confirmation, but it does not write words from the chat into the files, so the student writes everything through the forms. When asked to compare simulation and measurement, it runs `mlab align` and lists the numbers without judging them.

**TA checklist**:

- [ ] Session list: a session shows a step such as "● Plan review needed" when the student's input is needed and "◐ Agent working" while the agent works; a notice appears when another session's runs finish.
- [ ] Open a second 9.2 session: it starts from the beginning with no earlier records and shares the hardware budget with the first session (2 runs left); after switching back, the first session can still be continued.
- [ ] Every passage in the middle column has a source tag; hypotheses, comments, decisions and rationales for hardware points are tagged as the student's own words; the page text, in Chinese or English as set by the switch in the top bar, addresses the student as "you".
- [ ] Every chart carries the "Placeholder numbers" tag, and the "Table" button switches it to a table of the same numbers.
- [ ] A confirmation card lists what is to be approved and the effects of approving; after a denial with a reason, the agent's reply contains that reason.
- [ ] Messages sent while the agent is working are queued and handled after it finishes; "Stop" voids waiting cards.
- [ ] After a page refresh, the chat, unsubmitted form input and the current record are all still there.
- [ ] Compare tray: with two simulation runs from the same round and one hardware run added, the comparability matrix points out that the sources differ.
- [ ] With "Directory" at the top of the middle column expanded, the timeline shows which round each candidate came from and which configurations each measurement measured; a configuration page lists the results of that configuration in every simulation round and every measurement.
- [ ] In `report.md`, the section with the student's own words marks the source (form, chat) and contains no `[Page notice]`; neither does `.mlab/student.jsonl`.
- [ ] On a narrow screen, it is possible to switch between sessions, records and chat.

## Demonstrating the hardware queue

The hardware stand-in returns immediately by default. To demonstrate queueing on the slow flywheel, after `mlab init` set `hardware_delay_s` in the workspace's `.mlab/config.json` to 90: when `mlab measure wait --max 60` gets no result, the agent ends its turn and the student can start a simulation round in the meantime; once the measurement finishes, `mlab next` reports that the results are in.

## Switching to the LLM simulator

```bash
mlab init --task 9.2 --simulator llm --provider codex-luna workspaces/demo2-llm
cd workspaces/demo2-llm && ./codex
```

GPT-6 Luna estimates the simulation results; the steps and inputs stay the same. The simulated numbers vary slightly from run to run, and an occasional verdict may differ; the hardware stand-in's numbers do not change. When the model API fails, `mlab next` enters `retry_backend`, and resubmitting reruns only the failed runs.

## Interrupting and starting over

- **Resume:** after exiting, run `./codex` again in the same workspace; the conversation resumes and the agent reads `mlab next`, and completed runs are not rerun.
- **Start over:** run `mlab init` again with a new workspace name.

## Fallbacks

| Situation | Command | Notes |
| --- | --- | --- |
| Terminal chat is preferred | `scripts/start-agent.sh --workspace workspaces/classroom` | Codex / GPT-6 Luna with explicit confirmations |
| No model available for the course page | `mlab web workspaces/web-offline --agent fake --simulator fixture` | Current workbench controls work offline; discussion identifies itself as an offline preview |
| Only the experiment tools | `mlab demo --task 9.2 --out workspaces/offline` | No agent; a script goes through one simulation round, validation on the simulator and one measurement, and compares simulation with measurement |
