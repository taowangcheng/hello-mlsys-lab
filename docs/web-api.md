# Course page API (`mlab web`)

The page and `evals/web_story.py` use only the endpoints listed here. Requests and responses are JSON. When a business-logic check fails, the response is HTTP 200 with `ok:false`, in the same format as the CLI; an unknown session returns 404.

The default workbench has persistent navigation, central evidence and conversation panes. A session is the storage and conversation boundary under the course directory (`9.2-1/`, `9.2-2/`, …). Its experiments, runs, references, notes and report are isolated from other sessions; students reopen an older session to continue its work. Sessions of one task share only the hardware budget. The earlier guided API and `/classic` page remain available for compatibility.

## Starting and access

```bash
mlab web [<course-dir>] [--provider <profile>] [--simulator llm|fixture] [--agent codex|sdk|fake] [--port 8765] [--no-browser] [--no-opening] [--auto-continue]
```

- The course directory defaults to `mlsys-course`. The first run creates the directory and `course.json`, and opening a task creates its first session if needed; later runs keep the simulator, hardware backend and model profile chosen at creation.
- In the workbench, `--agent fake` leaves experiment controls fully usable and supports scripted simulation and measurement requests for offline interaction checks. It does not provide model explanations. In the guided interface, it uses the offline fake agent: it takes the agent's steps by `mlab next` and still sends confirmation cards for commands that need confirmation, but it calls no model and does not write the student's own words on the student's behalf.
- `--no-opening` turns off the agent speaking first when a session opens; `--auto-continue` applies to guided sessions only. In the workbench, a requested tool returns its result to the ongoing conversation, but completion of an unrelated run does not start a new turn automatically.
- If the port is taken, the command says why and exits; pass a different `--port`.

The server listens only on 127.0.0.1, and the page has no login. When connected to a remote server, open the address given by port forwarding (`ssh -L` or VS Code Remote); any host name and origin are accepted. Opened from an external browser through a VS Code tunnel (a `*.devtunnels.ms` address), the page stalls after being idle for a while or after a few refreshes in a row, and so does a small static page served by Python's built-in `http.server`; in that case use the editor's built-in browser or `ssh -L` instead. The `Content-Type` of a non-GET request must be `application/json`, otherwise the server returns 415; this keeps ordinary cross-site forms from posting to it.

Every response carries a CSP (same-origin resources only, no inline scripts), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`. Data responses carry `Cache-Control: no-store`; static assets carry `Cache-Control: no-cache` and an ETag, and the server answers 304 when a file is unchanged. Responses larger than 1 KB are gzip-compressed when the browser supports it. The page uses request/response calls rather than persistent streams. Validation and admission finish before mutation responses return; accepted runs continue in worker processes.

## Response format

```json
{"ok": true, "status": "ok", "...": "data"}
{"ok": false, "status": "rejected", "error": {"code": "E_SCHEMA", "message": "…", "field": null}, "next_actions": [{"actor": "student", "do": "review", "say": "…"}]}
```

Workbench controls remain usable alongside conversation. Legacy guided forms retain their busy check: while the agent is working or a card is waiting, a form returns `E_AGENT_BUSY`; resend it with `"interrupt": true`, and the server stops the agent first, then submits.

## Language

The server and `mlab` write English. The page offers Chinese or English; the choice is kept in the browser, and the first visit follows the browser's language. The page translates its own text, the step labels, cards and notices with `static/locales/zh.json`, whose keys are the English text; a sentence with no entry is shown as it is. Every request carries the header `X-Mlab-Lang: en|zh`. The server uses it for two things: `GET …/records/report` renders the report in that language (`report.md` stays English), and the session's agent is told to reply in it, with a `[Page note]` appended to the next message whenever the language is new to the agent. Records keep the words of whoever wrote them.

## Task workbench

All mutation endpoints below use the same validation and execution core as the CLI. Updates to an existing experiment or measurement name its object ID. Creation calls return the new object and its revision for later actions.

| Endpoint | Request and behavior |
| --- | --- |
| `POST /api/sessions` | `task`. Creates a clean session for **New conversation**. Earlier conversations, drafts and runs remain in their original sessions; the task's measurement budget is unchanged. |
| `POST /api/workbench/tasks/{task}/open` | Opens the latest session, creating one if needed, and selects the exploration conversation policy. Does not call a model. |
| `GET /api/workbench/sessions/{s}` | Current session's task overview, templates, parameter rules, experiments, runs, measurements, notes and conclusions, plus the shared task hardware budget. The current UI uses this endpoint. References are qualified as `9.2-1/r001` or `9.2-1/sim_0001`. |
| `GET /api/workbench/tasks/{task}` | Compatibility endpoint aggregating the task's sessions. The current UI does not use this endpoint to populate session evidence. |
| `POST /api/workbench/compare` | `runs`: 2–12 distinct qualified run references from the same task. Returns metrics, configuration differences and comparability checks. This compatibility endpoint can span sessions; the current UI selects only the active session's runs. |
| `POST /api/workbench/sessions/{s}/create` | `title`, optional `question`/`expectation`, `candidates`, `seeds`, `workloads`, optional `source`. Creates one editable experiment. |
| `POST /api/workbench/sessions/{s}/save` | Experiment fields plus `workflow` and `revision`. Invalid input or stale revision cannot replace the saved draft. |
| `POST /api/workbench/sessions/{s}/simulate` | `workflow`, `revision`, `confirm: true`. Validates and records the configuration, then admits the selected simulation batch. |
| `POST /api/workbench/sessions/{s}/prepare_measurement` | `points` with workflow, candidate, workload, repetitions (`runs`) and rationale. For a draft, also `workflow`, `revision`, `confirm_config: true`. Validates the complete selection, records the configuration and prepares missing matching simulations. No hardware runs are spent. |
| `POST /api/workbench/sessions/{s}/check_measurement` | `measurement`. Refreshes the preview after predictions complete, or reopens an approved measurement whose submission was rejected. Returns `plan_hash`, points, planned hardware count and the current budget. |
| `POST /api/workbench/sessions/{s}/measure` | `measurement`, `revision` equal to the preview's `plan_hash`, `confirm: true`. Rechecks the shared budget and submits hardware work. |
| `POST /api/workbench/sessions/{s}/cancel` | `run`. Cancels an active run; completed evidence is unchanged. |
| `POST /api/workbench/sessions/{s}/note` | Student `text` and qualified `refs`. Appends an observation. |
| `POST /api/workbench/sessions/{s}/conclude` | `candidate` such as `r002/c16`, `confirm: true`. Submits the evidence-based conclusion and updates the report. |
| `POST /api/workbench/sessions/{s}/report` | Saves the current report without requiring a submitted conclusion. Read `GET /api/sessions/{s}/records/report` for the full localized Markdown and whether a report was saved. |
| `POST /api/workbench/sessions/{s}/chat` | Student `text`, optional `refs`, and optional `mode` (`queue` or `interrupt_then_send`). Includes task context and resolves attached or typed `@` references from the same session. Returns canonical `refs`, conversation status and agent snapshot. The assistant can answer questions, inspect records and carry out requested work. |
| `POST /api/sessions/{s}/execution/{card}` | `allow`: boolean, optional `reason`. Resolves a pending sandbox measurement card and resumes its waiting model tool. Confirmation permits the exact checked plan; rejection leaves the measurement unsubmitted. A missing or already handled card returns `E_SCHEMA`. |

Direct workbench action requests may include `request_id`. Repeating an identical request returns its original result; reusing the key for different content returns `E_CONFLICT`. This replay mechanism does not apply to chat or card responses. Draft revisions also return `E_CONFLICT` when stale. Workspace edit locks and a task-wide process lock serialize admission, including submissions from different sessions. Hardware confirmation remains mandatory even when the model is unavailable or busy.

Conversation references may name experiments, configurations, measurements, runs, overview or report records. The server accepts at most 12 distinct attached or typed references. Bare IDs such as `@sim_0001` resolve within the current session; qualified IDs such as `@9.2-1/sim_0001` must name that same session. Missing and other-session references are rejected before the message is recorded. The student's visible message preserves its wording and canonical references; task facts and compact record context are added separately for the model.

The exploration tool can start an explicitly requested simulation without a separate confirmation card. A requested sandbox measurement prepares any missing prediction, then exposes an `execution` card in the chat history with its configuration, points, seed, plan hash and budget summary. Answering the card continues the same model turn. An ordinary follow-up message declines the pending measurement and is returned to the assistant as the student's response. Stopping the turn also cancels its pending card. Notes and conclusions remain student actions.

The production HTML includes a fresh style nonce for the dialog library's scroll lock. Inline scripts remain blocked. Static JavaScript, CSS and fonts are local; the browser never receives model credentials.

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

Records distinguish authored content from computed results. Guided records retain the original student/assistant sections; workbench drafts record whether the student or assistant created them, and students can edit either kind before execution. Notes preserve the student's words and evidence references. Read views use a read-only `Lab` and change no file or ledger entry.

## Legacy guided writes

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

## Guided confirmation cards

In a guided conversation, when the Codex agent calls a command below through its dynamic Bash tool, the host waits for the page confirmation card before executing it. The optional legacy SDK uses `can_use_tool` for the same flow. The card's summary is computed by the read-only `Lab`: `title`, `facts` (what is to be approved), `effects` (the effects of approving), `will_fail` (why `mlab` would refuse, empty if it would not) and `record`.

| Kind | Command | The card lists |
| --- | --- | --- |
| `workflow_approve` | `mlab workflow approve --plan <hash>` | Research question, hypothesis and falsification condition, candidates, number of simulation runs, the student's comments |
| `workflow_decide` | `mlab workflow decide [--candidate <id>]` | The decision text as written, the cited runs, the selected candidate |
| `measure_approve` | `mlab measure approve --plan <hash>` | Each point's simulated value and margin; the budget to be used and what remains |
| `conclude` | `mlab conclude [--candidate <round>/<candidate>]` | The chosen configuration, the conclusion it would get and the basis for it; if a measurement has not finished running, that the command will be refused |
| `workflow_unlock`, `measure_unlock` | `mlab workflow unlock`, `mlab measure unlock` | The error record |

These commands must be run on their own, in the standard form. When one is hidden in a compound command, a variant form such as `--workspace`, `$(…)`, backticks or `bash -c`, the host tool checks (or the legacy SDK's `PreToolUse` hook) refuse it and give the reason, and the chat shows a "Blocked" entry. Other commands and edits outside what is allowed are refused as well. After the server restarts, cards left unhandled before the restart are marked stale.

## Events `GET /api/events?last=N`

The legacy page fetches new events with a short request every one to a few seconds: once a second while something is happening, every 2.5 seconds when quiet, and every 8 seconds while the page is in the background. Without `last`, only the current event number `last` is returned, to fetch from; with `last=N`, the events numbered above N (at most 500 per request) and the new `last` are returned at once. If the number is too old for the buffer to fill the gap, the response is `"resync": true`, and the page reloads all data. Each event is `{"id", "type", "session", "data"}`.

The workbench instead refreshes its task summary approximately every 2.2 seconds while visible and the open discussion approximately every 1.4 seconds. It does not consume this legacy event feed.

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
| `<workspace>/.mlab/student.jsonl` | The student's words and action inputs; each line has `at`, `source` (`web-action`, `web-form`, `web-chat`, `web-question`, `web-card`, `cli`), `kind`, `workflow`, `measurement` (the current round and measurement when it was said), `refs`, `text` |
| `<workspace>/.mlab/ledger.sqlite3` | Authoritative experiment, run, measurement, observation, conclusion and replay-key records |
| `<workspace>/.mlab/web/chat.jsonl` | The chat on the page. `role` is `student`, `agent`, `tool`, `card`, `notice` or `error`; later changes (a card's outcome, a tool's output) are appended as `{"update": <id>, …}` |
| `<workspace>/.mlab/codex/` | Isolated Codex state, sessions and caches; credentials remain in memory |
| `<workspace>/.mlab/web/codex.jsonl` | Prompts, visible replies, lab tool calls/results and usage; excludes authentication and reasoning |
| `<workspace>/.mlab/web/sdk.jsonl` | Legacy only: raw messages the SDK received, with long fields truncated, for auditing |
| `<workspace>/.mlab/web/agent.json` | Backend, model, interaction policy, conversation ID, turns and token counts; Codex sets `cost_available:false` because the protocol does not report a USD cost |
| `<workspace>/.mlab/web/claude-config/` | Legacy only: this session's Claude Code configuration directory, used with an API key |
