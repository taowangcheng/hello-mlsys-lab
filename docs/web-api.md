# Course page API (`mlab web`)

The page and `evals/web_story.py` use only the endpoints listed here. Requests and responses are JSON. When a business-logic check fails, the response is HTTP 200 with `ok:false`, in the same format as the CLI; an unknown session returns 404.

The unit of work on the page is a session: a session does one task from the start and has its own workspace under the course directory (`9.2-1/`, `9.2-2/`, …) and one agent. A session ID is made of the task and a sequence number. Sessions of the same task share only the hardware budget.

## Starting and access

```bash
mlab web [<course-dir>] [--provider <profile>] [--simulator llm|fixture] [--agent codex|sdk|fake] [--port 8765] [--no-browser] [--no-opening] [--auto-continue]
```

- The course directory defaults to `mlsys-course`. The first run creates the directory and `course.json`, and the student opens sessions on the page; later runs keep the simulator, hardware backend and model profile chosen at creation.
- `--agent fake` uses the offline fake agent: it takes the agent's steps by `mlab next` and still sends confirmation cards for commands that need confirmation, but it calls no model and does not write the student's own words on the student's behalf.
- `--no-opening` turns off the agent speaking first when a session opens; `--auto-continue` lets the agent continue on its own after runs finish (off by default).
- If the port is taken, the command says why and exits; pass a different `--port`.

The server listens only on 127.0.0.1, and the page has no login. When connected to a remote server, open the address given by port forwarding (`ssh -L` or VS Code Remote); any host name and origin are accepted. Opened from an external browser through a VS Code tunnel (a `*.devtunnels.ms` address), the page stalls after being idle for a while or after a few refreshes in a row, and so does a small static page served by Python's built-in `http.server`; in that case use the editor's built-in browser or `ssh -L` instead. The `Content-Type` of a non-GET request must be `application/json`, otherwise the server returns 415; this keeps ordinary cross-site forms from posting to it.

Every response carries a CSP (same-origin resources only, no inline scripts), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`. Data responses carry `Cache-Control: no-store`; page files carry `Cache-Control: no-cache` and an ETag, and the server answers 304 when a file is unchanged. Responses larger than 1 KB are gzip-compressed when the browser supports it. Every endpoint is a short request that returns at once; there are no streams that stay open.

## Response format

```json
{"ok": true, "status": "ok", "...": "data"}
{"ok": false, "status": "rejected", "error": {"code": "E_SCHEMA", "message": "…", "field": null}, "next_actions": [{"actor": "student", "do": "review", "say": "…"}]}
```

While the agent is working or a card is waiting, a form returns `E_AGENT_BUSY`; resend it with `"interrupt": true`, and the server stops the agent first, then submits.

## Language

The server and `mlab` write English. The page offers Chinese or English; the choice is kept in the browser, and the first visit follows the browser's language. The page translates its own text, the step labels, cards and notices with `static/locales/zh.json`, whose keys are the English text; a sentence with no entry is shown as it is. Every request carries the header `X-Mlab-Lang: en|zh`. The server uses it for two things: `GET …/records/report` renders the report in that language (`report.md` stays English), and the session's agent is told to reply in it, with a `[Page note]` appended to the next message whenever the language is new to the agent. Records keep the words of whoever wrote them.

## Read

| Endpoint | Returns |
| --- | --- |
| `GET /api/course` | `tasks`: the tasks a session can be opened for, with their titles; `sessions`: each session's ID, task, sequence number, creation time, badge, stage, current selection, latest submitted conclusion, hardware budget and agent status; the course's simulator, hardware backend, model profile and agent type |
| `GET /api/sessions/{s}/state` | All fields of `mlab next`, plus `step`, `step_label`, `form` (the form for this step), `hint`, `quick_replies`, `badge`, `runs_in_flight`, `ledger_seq`, and `agent` (status, waiting cards, number of queued messages, session, usage) |
| `GET /api/sessions/{s}/overview` | The task's objective, constraints, decision space, holdout workloads, budget, `GUIDE.md` and mode |
| `GET /api/sessions/{s}/notebook` | The session's timeline (rounds, measurements and submitted conclusions, with their lineage), the configurations tried, the current selection, the latest submitted conclusion, the validation matrix (with the conclusion a submission now would get), research questions, events and budget |
| `GET /api/sessions/{s}/records/{id}` | One record. `id` can be `overview` (same as the notebook), `report`, a round `r002`, a measurement `m001`, a configuration `r002/c16` (all of its runs across simulation rounds and measurements, the gaps against the frozen simulator predictions, the validation matrix), or a run `sim_0004` or `hw_0001`. A measurement record includes the simulation–measurement pairs: the gap, whether it is beyond noise, whether the verdict flips, and differences in effective configuration |
| `GET /api/sessions/{s}/runs/{run}/requests` | Each request's ready, first-token and finish times and its group; the critical path when there are dependencies |
| `GET /api/sessions/{s}/measurements/{m}/pairs?run=hw_0001` | Per-request TTFT of one measurement and of its simulator prediction, with their ratio, sorted from slowest to fastest, with the slowest quarter marked |
| `GET /api/sessions/{s}/preview/{kind}?…` | The summary for a confirmation card, computed without writing anything: `kind` is one of the kinds in the "Confirmation cards" section below, and the parameters have the same names as the command's (`plan`, `candidate`) |
| `GET /api/sessions/{s}/chat?limit=200&before=<id>` | The chat shown on the page and the agent's status |
| `POST /api/sessions/{s}/compare` `{"runs": [...]}` | Comparability check, comparable groups, each run's strategy differences from the first run, and chart data. Computes only, writes nothing |

In record data, every piece of text has a clear source: the student's own words (hypothesis, comments, decision, rationales for hardware points), drafted by the agent (research question, candidates, analysis, changes and responses), or computed by mlab (everything else). All views are built with the read-only `Lab` and change no file or ledger entry in the workspace.

## Write

### New session `POST /api/sessions`

`{"task": "9.2"}`: creates a new session that does the task from the start and returns `session` (for example `9.2-2`). Earlier sessions are all kept and can be resumed at any time.

### Forms `POST /api/sessions/{s}/forms/{kind}`

Common fields: `then` is `notify` (write, then tell the agent; the default), `confirm` (write, then confirm directly) or `save` (write only); `interrupt` is described above. On success, `agent` in the response is `sent` or `queued`, which shows that the notice has gone to the agent.

| `kind` | Content | What `confirm` does |
| --- | --- | --- |
| `hypothesis` | `claim`, `alternative`, `falsifier`; creates a round first if there is no draft round | |
| `review` | `entries: [{candidate, verdict: keep/change/drop, reason}]`, `general` | Approves the current plan; refused if any candidate is to be changed or dropped |
| `decision` | `text` (must cite a run from this round), `candidate` | Confirms the decision and selects this candidate |
| `next_step` | `choice: round/measure/validate/conclude`, `detail`; `round` can take `source`, `validate` can take `candidate` | Records the student's choice; `round` and `measure` create the new round or measurement right away |
| `points` | `points: [{candidate, workflow?, workload, runs, rationale}]`; creates a measurement first if there is no draft measurement or the last one has finished measuring | |
| `approve_points` | None | Approves the checked hardware points |
| `unlock` | `target: workflow/measurement` | Clears the repeated-error count |
| `conclude` | `candidate` (optional, for example `r002/c16`; defaults to the current selection) | Submits a conclusion and generates the report; can be submitted more than once, and the latest submission counts |
| `report` | None | Regenerates the report |
| `new_round` | `source`: an approved round | Starts a new round with its candidates |

What the student writes goes into the record verbatim, changing only the student's section; a line with source `web-form` is also added to `student.jsonl`. The checks are the same as in the CLI; confirmations triggered by a form do not count toward the agent's repeated-error count.

### Chat `POST /api/sessions/{s}/chat`

`{"text": "…", "refs": ["r002"], "mode": "queue"}`. An `@ID` in `text` is recognized automatically; IDs can also be given in `refs`. The student's words are recorded in `student.jsonl` (source `web-chat`); when sending them to the agent, the page appends a one-line fact about each referenced record, marked `[Page note]`. `mode` is `queue` (queued while the agent is working and sent together on its next turn) or `interrupt_then_send` (stops the current turn first). When a card is waiting, the message is the answer to the card: a confirmation card is treated as not approved, with the message as the reason; a question card takes the message as the student's own answer. The returned `status` is `sent`, `queued` or `answered_card`.

### Agent `POST /api/sessions/{s}/agent/{action}`

| `action` | Effect |
| --- | --- |
| `open` | Connects the agent; the first time a session is opened within 10 minutes, has the agent sum up the progress in a sentence or two |
| `interrupt` | Stops the current turn, voids waiting cards and drops queued messages |
| `restart` | Disconnects and reconnects, continuing this session's earlier conversation |
| `continue` | `{"owner": "m001"}`: tells the agent these runs have finished, so it continues by `mlab next` |

### Cards

| Endpoint | Body |
| --- | --- |
| `POST /api/sessions/{s}/approvals/{card}` | `{"decision": "allow"}` or `{"decision": "deny", "reason": "…"}`; the reason is passed to the agent verbatim and recorded in `student.jsonl` (source `web-card`) |
| `POST /api/sessions/{s}/questions/{card}` | `{"answers": {"question": "an option or the student's own answer"}}` or `{"cancel": true}`; answers are recorded in `student.jsonl` (source `web-question`) |

## Confirmation cards

When the Codex agent calls a command below through its dynamic Bash tool, the host waits for the page confirmation card before executing it. The optional legacy SDK uses `can_use_tool` for the same flow. The card's summary is computed by the read-only `Lab`: `title`, `facts` (what is to be approved), `effects` (the effects of approving), `will_fail` (why `mlab` would refuse, empty if it would not) and `record`.

| Kind | Command | The card lists |
| --- | --- | --- |
| `workflow_approve` | `mlab workflow approve --plan <hash>` | Research question, hypothesis and falsification condition, candidates, number of simulation runs, the student's comments |
| `workflow_decide` | `mlab workflow decide [--candidate <id>]` | The decision text as written, the cited runs, the selected candidate |
| `measure_approve` | `mlab measure approve --plan <hash>` | Each point's simulated value and margin; the budget to be used and what remains |
| `conclude` | `mlab conclude [--candidate <round>/<candidate>]` | The chosen configuration, the conclusion it would get and the basis for it; if a measurement has not finished running, that the command will be refused |
| `workflow_unlock`, `measure_unlock` | `mlab workflow unlock`, `mlab measure unlock` | The error record |

These commands must be run on their own, in the standard form. When one is hidden in a compound command, a variant form such as `--workspace`, `$(…)`, backticks or `bash -c`, the `PreToolUse` hook refuses it and gives the reason, and the chat shows a "Blocked" entry. Other commands and edits outside what is allowed are refused as well. After the server restarts, cards left unhandled before the restart are marked stale.

## Events `GET /api/events?last=N`

The page fetches new events with a short request every one to a few seconds: once a second while something is happening, every 2.5 seconds when quiet, and every 8 seconds while the page is in the background. Without `last`, only the current event number `last` is returned, to fetch from; with `last=N`, the events numbered above N (at most 500 per request) and the new `last` are returned at once. If the number is too old for the buffer to fill the gap, the response is `"resync": true`, and the page reloads all data. Each event is `{"id", "type", "session", "data"}`.

| Event | Data |
| --- | --- |
| `task.state` | `state`: the same fields as `GET /state` |
| `agent.status` | `status` (`offline`, `connecting`, `idle`, `running`, `waiting_approval`, `waiting_answer`, `error`), `label`, `detail`, `badge`, `queued` |
| `agent.usage` | `usage`: cumulative turns, tokens, cost and `cost_estimated`; `turn`: this turn's usage |
| `records.changed` | `seq`, `workflow`, `measurement`; the ledger or the files of the current round or measurement changed |
| `runs.progress` | `runs`: runs still in progress and their phases |
| `chat.append`, `chat.update` | `entry`: a chat entry that was added or updated |
| `chat.delta` | `text`: the text the agent is producing |
| `approval.request`, `question.request` | `card`: the card |
| `approval.resolved`, `question.resolved` | `id`, `state` (`approved`, `denied`, `cancelled`, `answered`) |
| `notify` | `text`, `owner`: all runs of a round or a measurement have finished |
| `course.changed` | `created`: a new session was opened |

## Files

| Location | Content |
| --- | --- |
| `<course-dir>/course.json` | The simulator, hardware backend and model profile chosen at creation |
| `<course-dir>/9.2-1/` | One session's workspace, the same as one created by `mlab init` |
| `<workspace>/.mlab/student.jsonl` | The student's own words; each line has `at`, `source` (`web-form`, `web-chat`, `web-question`, `web-card`, `cli`), `kind`, `workflow`, `measurement` (the current round and measurement when it was said), `refs`, `text` |
| `<workspace>/.mlab/web/chat.jsonl` | The chat on the page. `role` is `student`, `agent`, `tool`, `card`, `notice` or `error`; later changes (a card's outcome, a tool's output) are appended as `{"update": <id>, …}` |
| `<workspace>/.mlab/codex/` | Isolated Codex state, sessions and caches; credentials remain in memory |
| `<workspace>/.mlab/web/codex.jsonl` | Prompts, visible replies, lab tool calls/results and usage; excludes authentication and reasoning |
| `<workspace>/.mlab/web/sdk.jsonl` | Legacy only: raw messages the SDK received, with long fields truncated, for auditing |
| `<workspace>/.mlab/web/agent.json` | Backend, model, conversation ID, turns and token counts; Codex sets `cost_available:false` because the protocol does not report a USD cost |
| `<workspace>/.mlab/web/claude-config/` | Legacy only: this session's Claude Code configuration directory, used with an API key |
