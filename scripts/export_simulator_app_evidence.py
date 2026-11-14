#!/usr/bin/env python3
"""Export public HTTP receipts and lab observations after verifying all hashes."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from mlab.contracts.simulator import check_response
from mlab.core.io import hashed, load
from mlab.core.workspace import Lab


def export(workspaces, destination, simulator_source):
    if set(workspaces) != {"9.1", "9.2", "9.3"}:
        raise ValueError("Provide one workspace for each of the three tasks")
    commit = subprocess.check_output(
        ["git", "-C", str(simulator_source), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(
            ["git", "-C", str(simulator_source), "status", "--porcelain"], text=True).strip():
        raise ValueError("The simulator checkout must be unmodified")
    report = {"simulator_version": "1.20.0", "profile": "protocol-v1.20", "simulator_source_commit": commit,
              "source_commit_observation": "Local checkout, not a value verified by HTTP", "tasks": []}
    for task in ("9.1", "9.2", "9.3"):
        lab = Lab(Path(workspaces[task]), readonly=True)
        try:
            if lab.task["id"] != task:
                raise ValueError("Workspace task differs from the requested task")
            item = {"task": task, "session": lab.root.name, "profile": load(lab.root / "task/profile.json"), "runs": []}
            for run in lab.store.all("run"):
                result, spec = lab.result(run["id"])
                if result["status"] != "succeeded" and run["role"] != "simulator":
                    raise ValueError("A sandbox recording did not complete")
                folder = lab.root / run["folder"]
                target = destination / ("task-" + task) / run["id"]
                target.mkdir(parents=True, exist_ok=True)
                manifest = load(folder / "manifest.json")
                for name in ["manifest.json", *manifest["sha256"]]:
                    if "/" in name or not name.endswith((".json", ".jsonl", ".txt")):
                        raise ValueError("Unexpected public artifact name")
                    shutil.copy2(folder / name, target / name)
                trace = lab.root / "task" / spec["workload"]["requests"]["path"]
                shutil.copy2(trace, target / "workload.jsonl")
                if hashed(target / "workload.jsonl") != spec["workload"]["requests"]["sha256"]:
                    raise ValueError("Source workload changed")
                if run["role"] == "simulator":
                    check_response((target / "simulator-request.json").read_bytes(), load(target / "simulator-response.json"))
                    if (result["evidence_level"] != "simulated" or result["backend"]["version"] != "1.20.0"
                            or result["coverage"]["in_domain"] is not None or result["metrics"]["critical_path_s"] is not None):
                        raise ValueError("Unexpected simulator evidence or calibration claim")
                elif result["evidence_level"] != "mock":
                    raise ValueError("Sandbox evidence must remain explicitly synthetic")
                item["runs"].append({
                    "id": run["id"], "role": run["role"], "candidate": run["candidate"],
                    "workload": run.get("holdout") or "development", "evidence_level": result["evidence_level"],
                    "status": result["status"], "failure_class": result["failure_class"], "requests": result["requests"],
                    "manifest": str((target / "manifest.json").relative_to(destination)),
                    "manifest_sha256": hashed(target / "manifest.json"),
                })
            item["conclusions"] = lab.store.all("conclusion")
            report["tasks"].append(item)
        finally:
            lab.close()
    (destination / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({item["task"]: len(item["runs"]) for item in report["tasks"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export verified public run artifacts, excluding databases and agent logs")
    parser.add_argument("--workspace", action="append", required=True, help="TASK=WORKSPACE, once per task")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--simulator-source", type=Path, required=True)
    args = parser.parse_args()
    export(dict(value.split("=", 1) for value in args.workspace), args.out, args.simulator_source)
