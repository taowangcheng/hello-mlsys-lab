#!/usr/bin/env python3
"""Execute one explicit simulator request and save raw and normalized artifacts.

This entry point exercises the lab HTTP client and shared metrics. It does not
register the service in a student session or replace the course task catalog.
"""
import argparse
import hashlib
import json
from pathlib import Path

from mlab.backends.simulator_http import (
    SimulatorAPIError, SimulatorHTTPClient, SimulatorProtocolError, SimulatorTransportError,
)
from mlab.contracts.simulator import RunRequest


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8877")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New artifact directory")
    args = parser.parse_args(argv)
    raw = args.request.read_bytes()
    request = RunRequest.model_validate_json(raw, strict=True)
    client = SimulatorHTTPClient(args.base_url, timeout_s=request.evaluation.timeout_s + 30)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "request.json").write_bytes(raw)
    try:
        exchange = client.simulate(raw)
    except SimulatorAPIError as error:
        (args.out / "response.json").write_bytes(error.raw)
        write(args.out / "error.json", {"layer": "http_service", "http_status": error.status,
                                       "error": error.error, "headers": error.headers})
        print(str(error))
        return 4
    except SimulatorProtocolError as error:
        (args.out / "response.json").write_bytes(error.raw)
        write(args.out / "error.json", {"layer": "response_validation", "message": str(error)})
        print(str(error))
        return 4
    except SimulatorTransportError as error:
        write(args.out / "error.json", {"layer": "transport", "execution_state": "unknown", "message": str(error)})
        print(str(error))
        return 4
    (args.out / "response.json").write_bytes(exchange["response_bytes"])
    result = exchange["result"]
    rows = result.pop("observations")
    (args.out / "requests.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
    write(args.out / "summary.json", result)
    write(args.out / "manifest.json", {"kind": "simulator_api_adapter_check", "runtime_backend_registered": False,
        "base_url": args.base_url, "backend": result["backend"], "headers": exchange["headers"],
        "sha256": {name: "sha256:" + hashlib.sha256((args.out / name).read_bytes()).hexdigest()
                   for name in ("request.json", "response.json", "requests.jsonl", "summary.json")}})
    print(json.dumps({"status": result["status"], "requests": result["requests"], "evidence_level": result["evidence_level"],
        "ttft_ms": result["metrics"]["ttft_ms"], "makespan_s": result["metrics"]["makespan_s"],
        "critical_path_s": result["metrics"]["critical_path_s"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
