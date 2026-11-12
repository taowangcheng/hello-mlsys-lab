# Demo steps: Ch 9.2, two flywheels

The student writes the hypothesis and falsification condition, comments on the plan, chooses hardware points and makes the decisions; to find out why simulation and measurement disagree, the student asks the agent to help analyze the data. The agent plans, runs and drafts the records. The fast flywheel is the simulator, with no limit on runs; the slow flywheel is the hardware stand-in, with a budget of 8 runs. The flow is two simulation rounds and two measurements, about 10 minutes with Claude Haiku and about 7 confirmation prompts. The model is accessed by logging in with a Claude Code account. All numbers are placeholder numbers, and the hardware stand-in is not real hardware.

## Setup

```bash
scripts/setup.sh                # first time only: installs the Python environment and the pinned Claude Code version
source .venv/bin/activate
mlab doctor                     # claude_version_matches_pin and claude_logged_in should both be true
mlab init --task 9.2 --simulator fixture --provider claude-haiku workspaces/demo2
cd workspaces/demo2 && ./claude
```

On first launch, Claude Code asks whether to trust the workspace; No is selected by default, so choose Yes. If not logged in, run `./claude` once first to log in.

## Steps

Type the lines below in order. In steps with an entry under "Confirmation", Claude Code asks for confirmation in the same window; read the command, then choose Yes.

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

## Course page demo

The same story, shown on the local course page. The unit of work on the page is a session: a session does one task from the start and has its own records and agent. The student's words can go into the forms in the middle column or into the chat in the right column.

```bash
mlab web workspaces/web-demo --simulator fixture --provider claude-haiku
```

The terminal prints the page address, and the browser opens it automatically; when connected to a remote server, open the address given by the editor's port forwarding (in VS Code, the "Ports" panel). If port 8765 is taken, add `--port` with a free port. In the left column, click "New session" and choose "9.2 Single-node serving under an SLA". The steps below use forms, chat, quick replies and confirmation cards at least once each; any step can instead be done by typing the student's line from the table in the previous section into the chat, and the agent writes the words into the files and brings up a confirmation card when confirmation is needed.

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
mlab init --task 9.2 --simulator llm --provider claude-haiku workspaces/demo2-llm
cd workspaces/demo2-llm && ./claude
```

Haiku estimates the simulation results, taking 10–20 seconds per batch; the steps and inputs stay the same. The simulated numbers vary slightly from run to run, and an occasional verdict may differ; the hardware stand-in's numbers do not change. When the model API fails, `mlab next` enters `retry_backend`, and resubmitting reruns only the failed runs.

## Interrupting and starting over

- **Resume:** after exiting, run `./claude` again in the same workspace; the SessionStart hook injects the current stage, and completed runs are not rerun.
- **Start over:** run `mlab init` again with a new workspace name.

## Fallbacks

| Situation | Command | Notes |
| --- | --- | --- |
| Stage-by-stage questions are preferred | `scripts/start-agent.sh --workspace workspaces/classroom` | Real model; the student answers the prompts; fast flywheel only for now |
| No model available for the course page | `mlab web workspaces/web-offline --agent fake --simulator fixture` | Offline fake agent; the student writes through the forms; steps that need confirmation still get confirmation cards |
| No network, or the model is unavailable | `python evals/local_classroom.py --protocol anthropic --backend fixture` | Real Claude Code with a pre-scripted fake model; runs two simulation rounds in about 30 seconds; tell the audience this |
| Only the experiment tools | `mlab demo --task 9.2 --out workspaces/offline` | No agent; a script goes through one simulation round, validation on the simulator and one measurement, and compares simulation with measurement |
