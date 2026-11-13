#!/usr/bin/env python3
"""Probe the upstream simulator's existing HTTP API without importing its code.

This is a compatibility probe, not a lab performance backend. Successful form
examples must never be reported as successful replays of the course workloads.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def request(base, path, payload=None):
    started = time.monotonic()
    body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
    req = Request(base + path, data=body, headers={"Content-Type": "application/json"} if body else {})
    try:
        response = urlopen(req, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        try:
            result = json.loads(raw)
        except ValueError:
            result = {"non_json_body": raw.decode(errors="replace")}
        return {"status": response.code, "elapsed_s": round(time.monotonic() - started, 4),
                "headers": {key: value for key, value in response.headers.items()
                            if key.lower() in {"content-type", "retry-after"}},
                "body": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8877")
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("Use an HTTP(S) API root without credentials, query or fragment")
    args.out.mkdir(parents=True, exist_ok=False)
    cases = []

    def save(name, path, payload=None, response=None):
        value = response if response is not None else request(base, path, payload)
        write(args.out / "requests" / (name + ".json"), {"method": "POST" if payload is not None else "GET",
              "path": path, "body": payload})
        write(args.out / "responses" / (name + ".json"), value)
        body = value["body"]
        cases.append({"name": name, "status": value["status"], "elapsed_s": value["elapsed_s"],
                      "error": body.get("error"), "response": "responses/" + name + ".json"})
        print(json.dumps(cases[-1]), flush=True)
        return value

    presets = save("presets", "/api/presets")
    smoke = {"model_id": "qwen3-1.7b", "workload": {"requests": 8, "input_tokens": 128,
             "output_tokens": 16, "arrival": "periodic", "rate_rps": 2, "seed": 42}}
    first = save("analytical-smoke", "/api/simulate", smoke)
    second = save("analytical-repeat", "/api/simulate", smoke)
    save("capabilities-endpoint", "/v1/capabilities")
    save("runs-endpoint", "/v1/runs")
    save("sequential-arrival", "/api/simulate", {**smoke, "workload": {**smoke["workload"], "arrival": "sequential"}})
    save("exact-request-list", "/api/simulate", {**smoke, "workload": [
        {"request_id": "a", "arrival_ms": 0, "input_tokens": 128, "output_tokens": 16},
        {"request_id": "b", "arrival_ms": 100, "input_tokens": 512, "output_tokens": 32}]})
    save("model-id-with-precision", "/api/simulate", {**smoke, "strategy": {"weight_precision": "FP8"}})
    save("multi-replica", "/api/simulate", {**smoke, "strategy": {"replicas": 2}})
    save("dag-nodes", "/api/simulate", {**smoke, "nodes": [
        {"node_id": "a", "model_id": "qwen3-1.7b", "input_tokens": 128, "output_tokens": 16},
        {"node_id": "b", "model_id": "qwen3-1.7b", "input_tokens": 512, "output_tokens": 32,
         "dependencies": ["a"], "gap_after_deps_ms": 100}]})
    examples = Path(__file__).resolve().parents[1] / "integrations/mlsys-simulator/examples"
    task_responses = {}
    for task in ("9.1", "9.2", "9.3"):
        payload = json.loads((examples / f"task-{task}.request.json").read_text())
        task_responses[task] = save("task-" + task + "-contract", "/api/simulate", payload)

    # Two ordinary HTTP clients test the existing one-slot policy. No injected
    # sleeps, patched handlers, private engine calls or synthetic server replies.
    barrier = threading.Barrier(2)
    concurrent = {"model_id": "qwen3-1.7b", "workload": {"requests": 64, "input_tokens": 128,
                  "output_tokens": 128, "arrival": "burst", "rate_rps": 2, "seed": 42}}
    def simultaneous():
        barrier.wait(timeout=5)
        return request(base, "/api/simulate", concurrent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(simultaneous) for _ in range(2)]
        concurrent_results = [future.result() for future in futures]
    for i, response in enumerate(concurrent_results):
        save(f"concurrent-{i + 1}", "/api/simulate", concurrent, response)

    first_body, second_body = first["body"], second["body"]
    smoke_ok = (first["status"] == 200 and first_body.get("ok") is True
                and first_body.get("result", {}).get("status") == "COMPLETED"
                and first_body.get("result", {}).get("counts", {}).get("completed") == 8
                and len(first_body.get("requests", [])) == 8)
    deterministic = (smoke_ok and second_body.get("ok") is True
                     and first_body.get("requests") == second_body.get("requests")
                     and first_body.get("result") == second_body.get("result"))
    report = {"purpose": "HTTP protocol compatibility probe; no course task execution is claimed",
              "upstream": json.loads((examples.parent / "upstream.json").read_text()),
              "version_verified_over_http": False,
              "version_note": "The declared checkout is recorded separately; the current HTTP API does not expose a software version.",
              "base_url": base, "transport_connected": presets["status"] == 200 and smoke_ok,
              "analytical_smoke_completed": smoke_ok,
              "deterministic_repeat": deterministic,
              "native_result_sha256": digest(first_body.get("result")),
              "native_requests_sha256": digest(first_body.get("requests")),
              "concurrent_http_statuses": sorted(item["status"] for item in concurrent_results),
              "task_integration": {task: {"status": "blocked" if response["status"] >= 400 else "needs_semantic_validation",
                  "http_status": response["status"], "error": response["body"].get("error")}
                                   for task, response in task_responses.items()},
              "cases": cases}
    write(args.out / "report.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))
    return 0 if report["transport_connected"] and deterministic else 1


if __name__ == "__main__":
    raise SystemExit(main())
