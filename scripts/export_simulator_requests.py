#!/usr/bin/env python3
"""Export unchanged course inputs as proposed transport messages, without local paths.

Only the simulator service may resolve model geometry, device rates, memory
planning and calibration. The messages contain student choices and task data.
"""
import argparse
import json
from pathlib import Path

from mlab.contracts import Candidate, RunSpec
from mlab.core.io import ROOT, dump, hashed, load
from mlab.core.workspace import TASKS


def export(task_id):
    folder = ROOT / "tasks" / TASKS[task_id]
    task = load(folder / "task.yaml")
    candidate = dump(Candidate.model_validate(load(folder / "baselines/baseline.yaml")))
    reference = task["frozen"]["workload_requests"]
    path = folder / reference["path"]
    assert hashed(path) == reference["sha256"], "Task trace no longer matches its frozen hash"
    requests = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    spec = RunSpec(task=task_id, candidate_id=candidate["id"],
        workload={"mode": task["workload_mode"], "requests": requests, "source_sha256": reference["sha256"]},
        infra=load(ROOT / "catalog/infra" / (candidate["infra"] + ".yaml")), strategy=candidate["strategy"],
        evaluation={"constraints": task["constraints"], "duration_s": task["duration_s"], "timeout_s": 1800,
            "goodput_policy": "ttft_only_if_tpot_tbd" if task_id == "9.2" else "both_thresholds_required"},
        execution={"seed": 42, "repeat": 0, "split": "development", "holdout": None, "initial_state": "empty", "warmup": None,
                   "max_simulated_ms": task["duration_s"] * 1000})
    message = dump(spec)
    message["schema"] = "mlab.simulator.v1"
    message["catalog_version"] = "to-be-agreed"
    message["client_request_id"] = f"protocol-probe-task-{task_id}-baseline"
    return message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "integrations/mlsys-simulator/examples")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for task in TASKS:
        path = args.out / f"task-{task}.request.json"
        path.write_text(json.dumps(export(task), indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
