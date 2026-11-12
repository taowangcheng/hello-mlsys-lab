# Student-led experiment workbench

The default page at `/` is a task workspace. Students can keep several experiment drafts, edit configurations, schedule simulations or measurements, inspect any earlier run, and compare evidence across experiments. The assistant is an optional discussion panel. There is no required hypothesis/review/decision sequence in this interface.

## Interaction model

- **Task overview** explains the objective, constraints and available methods.
- **Experiments** holds editable drafts and executed configurations. A draft can contain one or several configurations, workloads and repetition seeds. Expectations are optional. Local edits survive navigation and refresh. A revision conflict preserves those edits and offers a new experiment instead of overwriting someone else's saved version.
- **Runs** collects simulations and measurements across all sessions of the task. Search, filters, sort order and comparison selections survive navigation. Execution status and constraint verdict are separate. A successful process can still violate the SLA.
- **Run details** expose the saved configuration, task constraints, matching simulation, request timings and observations. Duplicate an earlier configuration to continue exploring; executed inputs never change.
- **Compare** accepts 2–12 runs, including runs from earlier sessions. Students choose the baseline. The page shows metrics and changed settings, and calls out different workloads, evaluation rules, evidence types or execution backends.
- **Notes and conclusions** keeps the student's observations linked to evidence. Submitting a conclusion does not end exploration. An incomplete validation remains an incomplete conclusion.
- **Discussion** carries explicitly selected evidence. The assistant can answer questions and propose a new editable draft. It cannot execute work, spend measurement budget, cancel runs, or submit the student's conclusion.

Simulation and measurement are separate student actions. A student can arrange a measurement directly from a draft. The preview prepares a matching simulation if one is missing, then shows the hardware count and remaining budget for explicit confirmation. Closing the dialog leaves the pending measurement available for later review. Preparatory simulations do not spend hardware budget.

The current fixture/LLM simulator and hardware stand-in produce demonstration data. The UI labels this limitation. A different model provider does not make those numbers real GPU measurements.

## Design and implementation

The visual system uses warm neutral surfaces, a forest-green accent, restrained borders, consistent spacing and clear type hierarchy. Simulation and measurement have distinct marks. Light and dark themes and narrow layouts share the same information architecture. Charts respect reduced-motion preferences and offer keyboard-operable points.

The task navigation and hierarchy draw on [Linear's design refresh](https://linear.app/now/behind-the-latest-design-refresh). The run table and comparison workflow draw on [Aim's run explorer](https://aimstack.readthedocs.io/en/latest/ui/pages/run_management.html). The optional evidence discussion is informed by [Hex Threads](https://learn.hex.tech/docs/explore-data/threads). These are interaction references; this application does not embed those services or copy a full product template.

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

The interface itself needs no CDN, external fonts, analytics, remote UI service or account. A live assistant requires the selected model provider's credentials. Markdown is rendered without raw HTML. The production output ships in `src/mlab/web/static/workbench/`, so students do not need Node. The earlier page remains at `/classic` for older guided records and reports. Switching a conversation to the workbench selects its exploration policy; use a separate session for a legacy guided demonstration.

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

Browser checks create a disposable course under `.test-runs/`, serve it on loopback port 8877, and use an isolated browser profile. They exercise student editing, draft recovery, simulation, measurement confirmation, evidence navigation, notes, comparisons, contextual discussion, Chinese, dark mode and mobile width. Screenshots and failure traces go to `workspaces/ui-refactor-check/`. No model is called by those checks.

## Consistency and compatibility

Workbench mutations name the experiment or measurement explicitly. A workspace lock serializes edits; revision checks reject stale writes; request IDs replay the original result. Shared task admission locks prevent simultaneous submissions from overspending hardware budget across sessions. Execution keeps the existing immutable specification and result checks.

Historical guided experiments remain readable. Cloning one produces a new exploratory draft. The terminal and earlier guided API retain their existing approval rules. Opening the workbench switches its conversation to the exploration tool policy, and automatic guided continuation is disabled there.

The workbench currently polls complete task summaries. This is suitable for classroom-sized histories; a long-running deployment with thousands of runs should add pagination and incremental result loading. Live model availability and real GPU backend validation remain separate from browser and protocol tests.
