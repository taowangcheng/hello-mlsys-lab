#!/usr/bin/env python3
"""Verify the simulator's public Agent HTTP API without importing its engine.

Protocol variants use different declared models/hardware from course baselines.
Passing this probe establishes data exchange, not course baseline acceptance or
registration of a simulator backend in the lab application.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import runpy
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "integrations/mlsys-simulator"
CHECK = runpy.run_path(str(DIRECTORY / "contract.py"))["check_response"]


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exchange(base, path, raw=None):
    """One HTTP attempt; preserve body bytes and never retry or follow redirects."""
    started = time.monotonic()
    request = Request(base + path, data=raw, headers={"Content-Type": "application/json"} if raw else {})
    try:
        response = build_opener(NoRedirect).open(request, timeout=60)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        return {"status": response.status, "body": body,
                "elapsed_s": round(time.monotonic() - started, 4),
                "headers": {key.lower(): value for key, value in response.headers.items()
                            if key.lower() in {"content-type", "retry-after"}}}


def diagnostic(payload, name, rows=None):
    """Make an explicitly separate boundary case, leaving all task files intact."""
    result = deepcopy(payload)
    result.update(client_request_id=name, candidate_id=name)
    if rows is not None:
        source = trace_bytes(rows)
        result["workload"].update(requests=rows, source_sha256=digest(source))
    return result


def trace_bytes(rows):
    return b"".join(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n" for row in rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8877")
    parser.add_argument("--out", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--service-commit", help="Operator-verified service revision; not verified by HTTP")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    url = urlsplit(base)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        parser.error("Use an HTTP(S) service root without credentials, query or fragment")
    if args.service_commit and not re.fullmatch(r"[0-9a-f]{40}", args.service_commit):
        parser.error("The service commit must be a full lowercase Git SHA")
    args.out.mkdir(parents=True, exist_ok=False)
    cases = []

    def save(name, raw=None, *, path="/v1/simulate", expected_status=200, expected_code=None,
             expected_counts=None, expected_run_status=None, expected_failure_class=None, received=None):
        packet = received if received is not None else exchange(base, path, raw)
        request_path = f"requests/{name}.json" if raw is not None else None
        response_path = f"responses/{name}.json"
        if request_path:
            destination = args.out / request_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        destination = args.out / response_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(packet["body"])
        entry = {"name": name, "method": "POST" if raw is not None else "GET", "path": path,
                 "request": request_path, "response": response_path,
                 "request_sha256": digest(raw) if raw is not None else None,
                 "response_sha256": digest(packet["body"]),
                 "http_status": packet["status"], "headers": packet["headers"], "elapsed_s": packet["elapsed_s"]}
        if raw is not None:
            workload = json.loads(raw)["workload"]
            source = trace_bytes(workload["requests"])
            if digest(source) == workload["source_sha256"]:
                entry["source"] = f"sources/{name}.jsonl"
                destination = args.out / entry["source"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source)
        checks = []
        try:
            payload = json.loads(packet["body"])
            if packet["status"] != expected_status:
                checks.append(f"Expected HTTP {expected_status}, received {packet['status']}")
            if expected_code and payload.get("error", {}).get("code") != expected_code:
                checks.append(f"Expected error code {expected_code}")
            if packet["status"] == 200 and raw is not None:
                validated = CHECK(raw, payload)
                entry.update(run_status=validated.status, evidence_level=validated.evidence_level,
                             outcomes=dict(Counter(row.status for row in validated.requests)),
                             backend=validated.backend.model_dump(), failure_class=validated.failure_class)
                if validated.evidence_level != "simulated":
                    checks.append("Real simulator replies must be labeled simulated")
                if expected_counts is not None and entry["outcomes"] != expected_counts:
                    checks.append(f"Expected outcomes {expected_counts}, received {entry['outcomes']}")
                if expected_run_status and validated.status != expected_run_status:
                    checks.append(f"Expected run status {expected_run_status}")
                if expected_failure_class and validated.failure_class != expected_failure_class:
                    checks.append(f"Expected failure class {expected_failure_class}")
            elif packet["status"] >= 400:
                entry["error"] = payload.get("error")
        except (ValueError, TypeError, AttributeError) as error:
            payload = None
            checks.append(str(error))
        entry.update(passed=not checks, failures=checks)
        cases.append(entry)
        write(args.out / "cases" / f"{name}.json", entry)
        print(json.dumps({key: entry[key] for key in ("name", "http_status", "passed", "failures")}), flush=True)
        return payload, entry

    catalog, catalog_case = save("catalog", path="/v1/catalog")
    if not catalog_case["passed"] or not isinstance(catalog, dict) or not catalog.get("catalog_version"):
        raise SystemExit("No valid service catalog; inspect the saved response.")
    samples = DIRECTORY / "examples/protocol-v1.20"
    variants = {task: (samples / f"task-{task}.protocol.request.json").read_bytes() for task in ("9.1", "9.2", "9.3")}
    protocol_results = {}
    for task, count in (("9.2", 12), ("9.1", 6), ("9.3", 10)):
        raw = (DIRECTORY / "examples" / f"task-{task}.request.json").read_bytes()
        save(f"task-{task}-original", raw, expected_status=422, expected_code="CATALOG_VERSION_MISMATCH")
        acknowledged = json.loads(raw)
        acknowledged["catalog_version"] = catalog["catalog_version"]
        save(f"task-{task}-original-with-catalog", encode(acknowledged), expected_status=422)
        protocol_results[task], _ = save(f"task-{task}-protocol", variants[task], expected_counts={"completed": count},
                                         expected_run_status="succeeded")

    repeated, repeat_case = save("task-9.2-repeat", variants["9.2"], expected_counts={"completed": 12})
    deterministic = repeated is not None and repeated == protocol_results["9.2"]
    if not deterministic:
        repeat_case["passed"] = False
        repeat_case["failures"].append("Same input bytes produced a different response")
        write(args.out / "cases/task-9.2-repeat.json", repeat_case)

    sla = json.loads(variants["9.2"])
    for name, field, value, error in (
        ("unsupported-model", "model", "llm-3b", "UNKNOWN_MODEL_ID"),
        ("unsupported-fp8", "weight_precision", "FP8", "UNSUPPORTED_PRECISION"),
    ):
        message = diagnostic(sla, name)
        message["strategy"]["deployments"][0][field] = value
        save(name, encode(message), expected_status=422, expected_code=error)
    message = diagnostic(sla, "unsupported-hardware")
    message["infra"]["nodes"][0]["devices"][0]["hardware"] = "w7900d"
    save("unsupported-hardware", encode(message), expected_status=422, expected_code="UNKNOWN_HARDWARE_ID")
    message = diagnostic(sla, "unsupported-tp")
    message["infra"]["nodes"][0]["devices"].append({"id": "gpu1", "hardware": "rx9070xt"})
    message["strategy"]["deployments"][0].update(devices=["gpu0", "gpu1"], parallel={"kind": "tp", "degree": 2})
    save("unsupported-tp", encode(message), expected_status=422)

    for task, counts in (("9.1", {"timed_out": 1, "not_started": 5}),
                         ("9.2", {"timed_out": 1, "not_started": 11}),
                         ("9.3", {"timed_out": 1, "blocked": 9})):
        name = f"task-{task}-simulation-cutoff"
        message = diagnostic(json.loads(variants[task]), name)
        message["execution"]["max_simulated_ms"] = 0.001
        save(name, encode(message), expected_run_status="timed_out", expected_counts=counts)

    message = diagnostic(sla, "deployment-oom")
    message["strategy"]["serving"]["kv_budget_gib"] = 16
    save("deployment-oom", encode(message), expected_run_status="failed", expected_failure_class="modeled_oom",
         expected_counts={"not_started": 12})

    sequential = diagnostic(json.loads(variants["9.1"]), "sequential-request-failure", [
        {"request_id": "too-large", "input_tokens": 4096, "output_tokens_target": 8},
        {"request_id": "small-after-failure", "input_tokens": 16, "output_tokens_target": 2},
    ])
    sequential["strategy"]["serving"]["kv_budget_gib"] = 0.01
    save("sequential-request-failure", encode(sequential), expected_counts={"failed": 1, "completed": 1}, expected_run_status="failed")

    dag = json.loads(variants["9.3"])
    parent_model, child_model = [item["model"] for item in dag["strategy"]["deployments"]]
    dag = diagnostic(dag, "dag-request-failure", [
        {"request_id": "parent", "input_tokens": 4096, "output_tokens_target": 8, "target_model": parent_model},
        {"request_id": "child", "input_tokens": 16, "output_tokens_target": 2, "target_model": child_model,
         "depends_on": ["parent"], "gap_after_deps_ms": 100},
        {"request_id": "independent", "input_tokens": 16, "output_tokens_target": 2, "target_model": child_model},
    ])
    dag["strategy"]["serving"]["kv_budget_gib"] = 0.01
    save("dag-request-failure", encode(dag), expected_counts={"failed": 1, "blocked": 1, "completed": 1}, expected_run_status="failed")

    message = diagnostic(sla, "wall-timeout")
    message["evaluation"]["timeout_s"] = 0.001
    save("wall-timeout", encode(message), expected_status=504, expected_code="AGENT_TIMEOUT")

    barrier = threading.Barrier(2)
    concurrent = [encode(diagnostic(json.loads(variants["9.3"]), f"concurrent-{index}")) for index in (1, 2)]

    def simultaneous(raw):
        barrier.wait(timeout=5)
        return exchange(base, "/v1/simulate", raw)

    with ThreadPoolExecutor(max_workers=2) as pool:
        packets = list(pool.map(simultaneous, concurrent))
    for index, (raw, packet) in enumerate(zip(concurrent, packets), 1):
        busy = packet["status"] == 429
        save(f"concurrent-{index}", raw, received=packet, expected_status=429 if busy else 200,
             expected_code="AGENT_BUSY" if busy else None, expected_counts=None if busy else {"completed": 10})
    concurrency_verified = (sorted(packet["status"] for packet in packets) == [200, 429]
                            and all(packet["headers"].get("retry-after") == "1" for packet in packets if packet["status"] == 429))
    report = {
        "purpose": "Independent lab HTTP verification of the upstream Agent protocol; no original baseline acceptance",
        "sample_source_commit": json.loads((samples / "sources.json").read_text())["commit"],
        "service_commit": args.service_commit,
        "service_commit_note": "Operator-supplied revision; HTTP replies identify a version but not a source commit",
        "lab_contract_sha256": digest((DIRECTORY / "contract.py").read_bytes()),
        "probe_sha256": digest(Path(__file__).read_bytes()),
        "base_url": base, "catalog_version": catalog["catalog_version"],
        "runtime_backend_registered": False, "original_course_baselines_accepted": False,
        "protocol_request_counts": {task: len(payload["requests"]) if payload else None for task, payload in protocol_results.items()},
        "deterministic_repeat": deterministic, "single_slot_with_retry_after_verified": concurrency_verified,
        "passed": all(case["passed"] for case in cases) and concurrency_verified,
        "cases": cases,
    }
    write(args.out / "report.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
