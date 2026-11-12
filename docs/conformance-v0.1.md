# Conformance check of the initial CLI version against the Plan and contract-v0.1

Current runtime update (2026-11-12): the default agent is now isolated Codex / GPT-6 Luna. See [Codex integration](codex.md) for the active configuration and entry points. Claude-specific descriptions below document the earlier implementation.

## 1. Scope and conclusion

Basis of the check: `docs/plan.md` and `docs/contract-v0.1.md` in this repository, with the source documents at baseline commit f3e900d.
This record covers the initial CLI implementation and does not count the later Web, real backends, or the student pilot as completed.

**Conclusion**: the initial version covers the main CLI/mock teaching path and the core evidence rules; it cannot claim that the whole Plan or all later backend requirements have been accepted.
The tables below distinguish what is implemented in code, what has been verified, and the remaining gaps; the existence of a function is not taken as a substitute for real model/student trials.

## 2. Item-by-item check against the Plan

| Basis | Requirement | Code/evidence | Conclusion |
| --- | --- | --- | --- |
| §1, §4 | Hypothesis → plan → run → analysis → revision → validation | classroom.py, workspace.py; a two-round, 9-run Claude toolchain demo | Implemented; the current CLI regression and the local fake API demo pass |
| §3 | Frozen trace/SLA, decision space, approval invalidation | task_manifest, specifications, approved | Implemented, with tests |
| §3 | Two repair attempts on errors, lock on the third | note_error, unlock, student confirmation in the terminal | Implemented; whether the model's fixes are semantically wrong needs evaluation |
| §4 | Student states the hypothesis first; research decisions in natural language | Student input, hypothesis/decision files | Implemented; learning effect awaits human assessment |
| §5.1 | Short JSON, paginated logs, files for large output | CLI emit, run show --logs | Implemented |
| §5.1 | Asynchronous, idempotent, no idle spinning on long tasks | worker, Backend.submit, wait/end_turn | Passes with a 1200-second injection and immediate end_turn; did not actually wait 20 minutes |
| §5.1 | Rejection of non-comparable data, small-sample p99 warning | compare defaults to development runs of the same round and checks workload/evaluation/split/version | Implemented |
| §5.2–5.3 | Backend protocol, five artifacts, separation of the three fields | contracts, backends, metrics, manifest hashes | Implemented, with shared interface tests added |
| §5.5 | backend_error, prediction_metrics, duration_s | Contract, task configuration, failure budget logic | Implemented |
| §5.5 | TTFT-only goodput for 9.2 while TPOT is pending | evaluation.goodput_policy, coverage note | Explicit exception; not extended to other tasks |
| §6.2 | LLM estimates, caching, failure classification, mock ceiling | backends/llm.py, worker, conclude | Implemented; the direction of real estimates awaits evaluation |
| §6.3 | Connect the real simulator/AMD by changing configuration only | Plan.backend currently restricted to fixture/llm; no real adapters | Later phase; not done |
| §7 | Three tasks, quality floor, protection against metric gaming | tasks, quality, completion_rate, DAG observations | Basic implementation; the full 9.3 semantics are still simplified |
| §7 | Student predictions kept separate from backend estimates | prediction.json, per-request estimates, report.json | Implemented; holdouts are not miscounted as development prediction error |
| §7 | Dedicated workload generator, two kinds of 9.3 holdout | core/workloads.py, seed/metadata/hash; single-dimension concurrency change + new DAG from the same distribution | Synthetic-draft implementation and loop verification for all three tasks completed; not real traces |
| §8 | AGENTS, skills, SessionStart, permissions | template and the classroom runtime | Implemented; ask confirmation verified in a real terminal |
| §8 | Pinned framework version and upgrade regression | compatibility.json records the version; setup.sh verifies and installs it into .venv/bin; the launcher and doctor check the version | Implemented |
| §9 | mlab ui, local workbench | No ui command at present | Deferred under the CLI scope |
| §10 | Atomic writes and recovery | SQLite WAL/FULL, atomic files, rebuilding derived state | Verified transaction consistency before and after a forced process kill, and recovery from a lost worker; power loss and all file-write windows are not covered |
| §12 | E1–E11, multi-model thresholds | evals/run.py, direction_check.py | Entry points implemented; 10/30 runs and manual review not done |
| §1, §11 | 3–5 students, 45 minutes, 30 minutes on a clean machine | No corresponding pilot records | Not accepted |

## 3. Item-by-item check against the backend contract

| Contract | Handling in this version | Status |
| --- | --- | --- |
| §1 Backend.submit(spec,key) can accept a new spec directly | No longer requires pre-registration by the workflow; registers within the initialized workflow context, starts, and returns the handle idempotently | Fixed; shared interface cases pass |
| §1 poll/cancel/fetch | Status queries, immutable terminal states, cancellation, hash checks of the five artifacts | Implemented; recovery/timeouts are mainly maintained by workspace.status |
| §2 Six-tuple and frozen inputs | RunSpec has the six fields; evaluation includes the declared duration, timeout, and goodput policy | Implemented; inner dictionaries remain extensible |
| §2 No request_id-based strategies or scheduling on future output lengths | Extra Strategy fields are rejected; fixture priorities use only known inputs/topology | Implemented; physical scheduling accuracy is not guaranteed |
| §3 Five files plus spec | Unified artifacts are written on both success and failure; manifest and result hashes are anchored in the ledger | Implemented |
| §3 status/verdict/evidence_level | Independent fields; OOM is judged separately from other failures | Implemented |
| §3 Unknown quality cannot be judged as passing | draft quality tables; unknown is not_evaluated | Implemented |
| §3 Shared metrics, failures kept in the denominator, linear quantiles | core/metrics.py and hand-calculated tests | Implemented |
| §3 goodput=null when a threshold is undefined | Dual thresholds by default; only 9.2 adopts the explicit exception of Plan 5.5 and writes coverage | Extension per the Plan, not a silent deviation |
| §4 Unsupported mechanisms must be rejected | Capability enumeration and check_capabilities; fixture no longer falsely reports KV budget/preemption/cache | Fixed |
| §4 Metrics null with a reason | Required/unavailable metrics explicitly null, with reasons in unsupported_metrics | Fixed |
| §4 Calibration domain | The run definition carries no calibration, and including one is rejected by contract validation; calibration is the simulator side's responsibility | Local backends have no applicable range; E_OUT_OF_CALIBRATION is defined together with the real simulator |
| §5 Error classification | FailureClass includes backend_error from the Plan's W2 extension; cancellation is not misreported as lost | Implemented/fixed |
| §6 tightest_constraint/per_device | 9.1 produces the numeric constraint with the highest utilization; per_device is explicitly null | Partial metric support; missing data is not fabricated |
| §7 One shared set of backend interface tests | tests/test_backends.py covers both fixture and the LLM with a local fake API | Dedicated tests pass; not a certification of all real backends |

## 4. Main implementation differences

1. The classroom uses a terminal controller to handle student confirmations, and the agent is forbidden from performing student actions on the student's behalf in headless turns.
   Native interactive Claude still uses the template's ask rules; acceptance evidence for the two paths is not mixed.
2. The CLI classroom defaults to fixture for stable demos, and `--backend llm` switches explicitly; mlab init keeps LLM as its default.
3. Storing the key in a Git-ignored local JSON is supported, going beyond the Plan's initial suggestion of environment variables only; generated configurations and committed content must not contain a real key.
4. The current setup is a repository checkout/editable install; there is no promise that a standalone wheel already includes the course data or front-end resources.
5. Tasks, thresholds, and quality tables are still draft; currently only mock_only/unresolved is produced, never simulation_validated/hardware_validated.
6. The runtime environment is Linux: launch scripts use bash, and CI runs on ubuntu; the workspace records the directory containing mlab, so the hooks and the agent's Bash tool can find it from any terminal.
7. The Claude Code version is pinned by the project and installed into .venv/bin; students do not need to install it separately, and other installations on the machine are unaffected.
8. `./claude` loads only the project configuration and turns off skills, plugins, and connectors synced from claude.ai; the built-in skills and built-in plugins shipped with Claude Code are kept.
9. The LLM backend supports account login: without an API key, it calls the pinned version's `claude -p`.

## 5. Verification boundaries of this version

On Linux, 75 CLI tests pass in total: 43 for workflow/classroom/metrics/new CLI acceptance, 21 for the backend and API interface, and 11 for the runtime environment; with `--with-claude`, 79 in total.
Real Claude Code 2.1.317 with Haiku completed two rounds and 10 runs in `./claude`, ending with a mock_only report; this does not represent the effectiveness of other models.
`scripts/verify-cli.sh` reruns the CLI tests, and `--with-claude` adds the toolchain combinations with the local fake API.
CI can be triggered manually or by a PR.

Before committing, the branch, the file list, and the exclusion of secrets are still checked; workspaces, real API configurations, run logs, model caches, and the Python environment are not committed.
Pending acceptance: multi-model 10/30-run pass rates, installation on a clean machine, and the student pilot. The Web, the real simulator, and AMD proceed later as in the original Plan.
Demo launch and step-by-step input are in [Demo steps](demo.md).
