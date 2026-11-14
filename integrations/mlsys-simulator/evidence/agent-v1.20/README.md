# Independent HTTP and result-adapter evidence

Captured on 2026-11-14 against the unmodified upstream service at commit
`feda6677b90a6f50f35ac78c1d5eabff986ed5b7`, reporting simulator version 1.20.0.
The source revision was checked on the operator's local checkout; it is not
advertised by the HTTP endpoint.

- [report.json](report.json): 24 HTTP cases with expected outcomes, exact-body
  hashes and relative paths to the complete requests and responses.
- [requests](requests), [responses](responses): actual sent/received bytes.
  Do not reformat these files without regenerating their hashes.
- [sources](sources): source JSONL for the two separate failure diagnostics.
- [client](client): three additional executions through the lab's HTTP client
  and shared metric calculation, with raw and normalized artifacts.
- [verification.json](verification.json): validation commands and scope.

The contract used for the initial independent probe is the lab contract from
commit `d28137bfb2d87e5ee247305325f34f1b7824c639`; its source hash is in the report.
The same wire models now live in `src/mlab/contracts/simulator.py`, re-exported
by the handoff entry point, with unchanged request/response JSON Schemas.

Successful protocol configurations have simulated, uncalibrated evidence.
Expected 422/429/504 cases passing does not mean their requests completed.
Original course baselines and student-session integration are not claimed.
See [findings and ownership](../../REVIEW-v1.20.zh-CN.md).
