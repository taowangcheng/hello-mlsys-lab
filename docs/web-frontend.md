# Conversation and experiment workspace

The default page at `/` uses three persistent panes: tasks and sessions on the left, the selected task or evidence in the center, and student–assistant conversation on the right. Students can ask about the task, explore an explanation, plan an experiment, request execution, or investigate evidence without leaving the current result. Questions need no selected result or prepared experiment. There is no required hypothesis/review/decision sequence.

## Interaction model

- **Conversation** supports learning throughout the workspace. The assistant answers task questions, helps reason about configurations, carries out requested simulations and prepares sandbox measurements for confirmation. Students can change direction in ordinary conversation while the center pane keeps their evidence visible.
- **New conversation** creates a clean session in the same task. Its conversation, experiments, runs, attachments, comparison selection, notes and report start empty. Earlier sessions remain available from the session list. Only the task's hardware budget is shared; starting a session does not restore spent runs.
- **Evidence references** can be added with **Add from history** or `@`, without first selecting rows in a table. Search covers the current session's experiments, configurations and runs. Sent messages retain their references, and draft messages and attachments survive navigation and refresh within that session. To inspect an older session's evidence, reopen that session explicitly.
- **Task overview** explains the objective, constraints and available methods. **Discuss the task** focuses the existing conversation without replacing its draft or attachments.
- **Manual setup** is a secondary menu for configuring a run or reopening saved experiment plans. Plans group configurations, workloads and seeds for reuse; they are not a required navigation step. Names, questions and expectations sit in an optional section. New setups receive a name automatically, and local edits survive navigation and refresh. A revision conflict preserves those edits and offers a separate copy.
- **Run history** is the main result entry and collects simulations and measurements in the current session. Search, filters, sort order and comparison selections survive navigation. Execution status and constraint verdict are separate. A successful process can still violate the SLA.
- **Run details** expose the saved configuration, task constraints, matching simulation, request timings and observations. **View configuration** opens the recorded settings. **Modify and run again** starts an editable copy containing only that run's configuration, workload and seed, with a link back to the original. It does not inherit the whole batch. **Measure this configuration** preselects that configuration and workload for review. Executed inputs never change.
- **Compare** accepts 2–12 runs from the current session, including different experiments. Students choose the baseline. The page shows metrics and changed settings, and calls out different workloads, evaluation rules, evidence types or execution backends.
- **Configuration evidence** brings together every run of a configuration in the session, with development, repeat and holdout validation. **Measurement records** group predictions and observations, show verdict changes and intervals, and let students inspect matched per-request ratios.
- **Thinking and reflection** preserves an experiment's hypothesis, alternative explanation, falsification condition, review, analysis and decision when present. Students can add reflections without completing a form sequence or unlocking another action.
- **Notes and conclusions** keeps the student's observations linked to evidence. Submitting a conclusion does not end exploration. An incomplete validation remains an incomplete conclusion.
- **Report** is available at any time in the center pane. It contains the current evidence, original reasoning and linked records, and makes unfinished validation visible. Saving a report does not require submitting a conclusion.
- **Conversation beside a result** keeps questions and evidence together while students inspect configurations, charts or request timings. The assistant can also find relevant history without explicit attachments. Numerical claims should cite the records used.

Expandable conversation records describe actions such as preparing a plan or submitting a simulation. Submitting work is distinct from completing a run; tool-call counts are not presented as experiment steps.

Students can ask the assistant to run a specific simulation or use the experiment controls themselves. Creating a draft alone does not start it. The assistant can wait for requested work, discuss its results in the same turn, or cancel a run when asked. Observations and submitted conclusions remain the student's own actions.

A saved run's configuration and seed remain inspectable before completion and after cancellation or failure; the server verifies the saved specification before returning them.

Sandbox measurements require confirmation of the selected configurations, workloads, repetitions and budget. In conversation, the assistant prepares the request and displays a measurement card; confirming it resumes the same conversation turn. Cancelling it spends no measurement budget. A student can also arrange a measurement directly from a draft. Both paths prepare a matching simulation if one is missing. These preparatory simulations do not spend hardware budget. Closing the direct measurement dialog leaves its pending request available for later review.

The current fixture/LLM simulator and hardware stand-in produce demonstration data. The UI labels this limitation. A different model provider does not make those numbers real GPU measurements.

## Design and implementation

The visual system uses warm neutral surfaces, a forest-green accent, restrained borders, consistent spacing and clear type hierarchy. Simulation and measurement have distinct marks. Light and dark themes and narrow layouts share the same information architecture. Charts respect reduced-motion preferences and offer keyboard-operable points.

The task navigation and hierarchy draw on [Linear's design refresh](https://linear.app/now/behind-the-latest-design-refresh). The run table and comparison workflow draw on [Aim's run explorer](https://aimstack.readthedocs.io/en/latest/ui/pages/run_management.html). Connecting discussion to evidence is informed by [Hex Threads](https://learn.hex.tech/docs/explore-data/threads). These are interaction references; this application does not embed those services or copy a full product template.

| Layer | Implementation |
| --- | --- |
| Application | React and TypeScript in `frontend/src` |
| Dialogs and focus management | Radix Dialog |
| Run table | TanStack Table v8 |
| Icons and Markdown | Lucide and react-markdown |
| Charts | Local SVG trade-off plots and request timing bars |
| Build | Vite; exact dependencies in `package-lock.json` |
| Server | Existing Starlette server and shared `Lab` validation |
| Storage | Existing experiment files and SQLite ledger; no second database |

The interface itself needs no CDN, external fonts, analytics, remote UI service or account. A live assistant requires the selected model provider's credentials. Markdown is rendered without raw HTML. The production output ships in `src/mlab/web/static/workbench/`, so students do not need Node. The earlier page remains at `/classic` for compatibility; the current workspace renders configuration evidence, measurements, reflections and full reports itself. Switching a conversation to the workbench selects its exploration policy; use a separate session for a legacy guided demonstration.

## Build and verify

```bash
scripts/build-web.sh
```

This installs Node 24.22.3 under `.venv/web-toolchain`, uses a project-local npm cache, installs the lockfile and builds the assets. It does not replace the machine's Node or Codex installation. Codex remains pinned independently to 0.167.2.

For frontend development after the build:

```bash
cd frontend
npm run dev
```

The Vite proxy expects the Python server on port 8765. Open the Vite address under `/static/workbench/`. The generated production assets, not the development server, are used by `mlab web`.

Run the API/regression suite and browser checks:

```bash
.venv/bin/python -m pytest -q
cd frontend
PATH="$PWD/node_modules/.bin:$PATH" PLAYWRIGHT_BROWSERS_PATH=../.venv/playwright playwright install chromium --only-shell
npm run test:browser
npm run format:check
```

The general browser checks create a disposable course under `.test-runs/`, serve it on loopback port 8877, and use an isolated browser profile. They exercise student editing, draft recovery, simulation, measurement confirmation, evidence navigation, notes, comparisons, contextual discussion, Chinese, dark mode and mobile width. Screenshots and failure traces go to `workspaces/ui-refactor-check/`. No live model is called by these checks.

`frontend/tests/conversation.spec.ts` checks that all three tasks show the three-pane workspace and accept a question without selected evidence. It also covers session isolation, references, keeping the conversation while inspecting a result, and requesting simulation and confirming or cancelling sandbox measurement in chat. Its scripted offline assistant tests the interaction mechanics, not the quality of model explanations. Screenshots go to `workspaces/conversation-review/`; run these scenarios from `frontend` with `npm run test:browser -- conversation.spec.ts`.

`frontend/tests/records.spec.ts` checks configuration validation, grouped measurement analysis, request ratios, saved reflections and full reports without a submitted conclusion. It also checks that a fresh session has no earlier evidence while retaining the task's budget usage. It runs its own disposable course and saves screenshots under `workspaces/records-review/`.

`frontend/tests/all-tasks.spec.ts` additionally completes a full investigation in each of tasks 9.1, 9.2 and 9.3: edit two configurations, simulate every provided workload with repetitions, inspect requests, compare results, measure the selected configuration twice on development and once on every holdout, write an observation, submit a conclusion, open its report, and reuse an earlier configuration in a new draft. Each task exports ten numbered screenshots and `evidence.json` under `workspaces/all-tasks-review/offline/task-9.x/`. Its separate disposable course and generated reports remain under `workspaces/all-tasks-review/offline/course-*`; the test server chooses an available loopback port. Run just these scenarios from `frontend` with `npm run test:browser -- all-tasks.spec.ts`.

These offline scenarios use fixture simulations and hardware stand-in results. Real SoC conversations and recordings are generated separately; follow [the conversation recording procedure](demo.md#record-the-conversation-experience). Its current gallery is `workspaces/studio-review/index.html`, with per-task exports under `workspaces/studio-review/live/`. The earlier full-workflow gallery has [its own recorder](demo.md#record-all-three-tasks-with-a-real-assistant). Record into a fresh course: a new conversation does not reset the task-wide measurement budget.

## Consistency and compatibility

Workbench mutations name the experiment or measurement explicitly. A workspace lock serializes edits; revision checks reject stale writes; request IDs replay the original result. Shared task admission locks prevent simultaneous submissions from overspending hardware budget across sessions. Execution keeps the existing immutable specification and result checks.

Historical guided experiments remain readable. Cloning one produces a new exploratory draft. The terminal and earlier guided API retain their existing approval rules. Opening the workbench switches its conversation to the exploration tool policy. Requested tools can return results to the ongoing conversation; completion of an unrelated run does not start a new conversation turn automatically.

The workbench currently polls complete session summaries. This is suitable for classroom-sized histories; a long-running deployment with thousands of runs should add pagination and incremental result loading. Live model availability and real GPU backend validation remain separate from browser and protocol tests.
