# Implementation audit — 2026-11-13

Three complete review passes covered the conversation, experiment controls, evidence reading, historical records, clean sessions, measurements, reports, provider isolation, and adverse request timing. Findings were reproduced and fixed, with full regressions after each pass. The starting revision was `5c9d8be`.

| Pass | Main emphasis | Python | Browser |
| --- | --- | --- | --- |
| 1 | Input contracts, evidence integrity and message preservation | 208 passed, 6 skipped | 19 passed |
| 2 | Delayed requests, editing, references, navigation and selection | 218 passed, 6 skipped | 26 passed |
| 3 | Recovery, weak networks, complete records and final consistency review | 225 passed, 6 skipped | 29 passed |

Production builds, TypeScript checks, formatting and whitespace checks passed. The six skipped tests are the explicitly enabled legacy Claude integrations; this audit used Codex and SoC Qwen. Failed reproductions and intermediate regressions remain available beside the successful final logs under `workspaces/three-pass-audit/`.

## Corrected behavior

- Accepted messages retain their complete text. Non-text or oversized messages are rejected before logging or starting a reply. Drafts remain editable when the message limit is exceeded. Plan fields, observation text and measurement names also reject oversized values instead of silently shortening them.
- Acknowledgements cannot clear newer conversation drafts, observations or reflections. Saving a configuration preserves changes made during the request and carries forward the accepted revision. Saved-state comparisons use editable content, avoiding false changes caused by response metadata.
- Workspace reads coordinate with saves, so a temporarily incomplete set of candidate files cannot make a plan disappear. A deterministic test pauses a save between files and verifies that readers wait. Out-of-order responses cannot overwrite newer data, and slow responses remain usable while a newer poll is pending.
- Late operations respect navigation. Simulation completion still opens run history when the editor becomes a completed record. Conclusion previews follow the currently selected configuration. Seed zero remains zero when the repetition count changes.
- Inline references consistently ignore emails, URLs and escaped mentions. Suggestions reopen after editing a dismissed query. Observation citations open the correct record type. Saved reports can be referenced before submitting a conclusion.
- Task context and attached evidence share one 60,000-character budget. Every shortened preview retains a complete readable source. Snapshot hashes are verified on reuse and first read. Long JSON property names fall back to whole-record pagination, oversized pointer metadata is rejected, and output shrinking terminates safely. Explicit tool failures remain failures.
- Pending and failed executions expose logs. The page labels the 2,000-character preview and offers a full log download. Complete logs remain available to the assistant independently of the page preview.
- Replacing an incompatible model thread carries recent visible discussion and a readable complete transcript from the same session. Creating a fresh student session still imports no earlier discussion or experiments.
- Invalid history limits return structured errors, and event cursors recover after a server restart. Disabled or full browser storage no longer prevents the application from opening; drafts remain in the current page with an accurate persistence notice.
- Model context preserves typed parameter rules, distinguishing numeric ranges from enumerated choices. Instructions distinguish the net effect of a configuration change from evidence establishing a runtime mechanism.

## Complete student workflows

Each browser pass exercised task questions, configuration changes, simulation, sandbox confirmation, run inspection, comparison, observations, conclusions, reports and reuse. Conversation checks also cover keyboard references, composition input, clean sessions, returning to earlier sessions, cancellation, streaming, Chinese, dark mode and narrow layouts.

| Task | Simulations in final workflow | Stand-in measurements | Report | Browser errors |
| --- | --- | --- | --- | --- |
| 9.1 | 8 | 3 | Saved | 0 |
| 9.2 | 12 | 4 | Saved | 0 |
| 9.3 | 12 | 4 | Saved | 0 |

These performance results use the fixture simulator and hardware stand-in. They are synthetic data, not GPU measurements.

## Live provider verification

The project required Codex `0.167.2` and used SoC `qwen3.8:27b`, with the existing serial inference admission and workspace-local state. Credentials entered through hidden standard input and ephemeral authentication; no credential file was written into the tested Codex homes.

Live probes exercised conceptual questions on all three tasks. A 99,098-character synthetic analysis contained an unpredictable marker only in its final paragraph. The initial attachment omitted that marker; the model called `Read` with `/analysis`, offset 98,698 and limit 400, received the marker, and reported it correctly. A forced instruction-policy change then created a different thread, and the assistant recovered the earlier comparison label even though it was absent from the new message and task context.

Reviewing live explanations also exposed ambiguous range metadata and overly strong causal wording. Typed ranges and explicit interpretation guidance were added and checked with follow-up questions. Unedited replies are retained for inspection. These probes establish the observed integration behavior; they do not guarantee every future generated explanation or provider availability.

## Local evidence

The workspace audit directory contains per-pass reproduction logs, complete regression logs, final screenshots, task evidence JSON and unedited SoC conversations:

- `workspaces/three-pass-audit/README.md`: detailed pass record and artifact index.
- `workspaces/three-pass-audit/round-3/python-final.log` and `browser-final.log`: final full regressions.
- `workspaces/three-pass-audit/round-3/live-soc-conversation.md` and `live-soc-results.json`: live requests, replies and checked outcomes.
- `workspaces/three-pass-audit/round-3/task-9.1-*`, `task-9.2-*`, `task-9.3-*`: comparison/report screenshots and complete workflow evidence.
- `workspaces/three-pass-audit/round-3/live-reading-and-recovery.png`: saved real conversation in the final interface.

Generated workspaces and provider transcripts remain local and are not committed. Source regressions are in `tests/test_resilience.py`, `tests/test_final_audit.py`, `frontend/tests/resilience.spec.ts` and `frontend/tests/final-audit.spec.ts`, alongside the existing suites.
