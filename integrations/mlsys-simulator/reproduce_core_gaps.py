"""Developer-only reproductions using the documented upstream Python API.

Run in a simulator checkout, with that checkout on PYTHONPATH. This script is
not imported by the lab, agent or HTTP probe. Toy synthetic timings isolate
schema/time semantics; they are not task executions or performance evidence.
"""
from copy import deepcopy
import json

from mlsys_sim import Experiment, SimulationError, __version__, predict_closed_loop, simulate_dag


def main():
    plan = {
        "schema_version": 1,
        "trace_source": "protocol_reproduction_not_a_course_trace",
        "nodes": [
            {"node_id": "parent", "model_id": "toy", "input_tokens": 16, "output_tokens": 1},
            {"node_id": "child", "model_id": "toy", "input_tokens": 16, "output_tokens": 1,
             "dependencies": ["parent"], "release_ms": 100},
        ],
        "pools": [{"model_id": "toy", "experiment": {
            "calibration": {"backend": "synthetic", "prefill_ms": 1000, "decode_ms": 1}}}],
    }
    native = simulate_dag(plan)
    parent, child = native["nodes"]
    output = {"version": __version__, "purpose": "Public core API schema reproduction only",
              "absolute_release_control": {"parent_completion_ms": parent["completion_ms"],
                  "child_release_ms": child["release_ms"], "child_ready_ms": child["ready_ms"],
                  "relative_gap_requirement_ready_ms": parent["completion_ms"] + 100},
              "native_critical_path_definition": native["critical_path"]["definition"],
              "native_request_has_service_ms": "service_ms" in child}
    with_gap = deepcopy(plan)
    with_gap["nodes"][1]["gap_after_deps_ms"] = 100
    try:
        simulate_dag(with_gap)
    except SimulationError as error:
        output["relative_gap_field"] = {"code": error.code, "message": str(error)}
    else:
        output["relative_gap_field"] = {"accepted": True, "semantic_validation_still_required": True}
    fixed_stream = {"schema_version": 1, "study_id": "protocol", "cohort": "fixed",
        "concurrency_grid": [1], "requests_per_run": 2, "replications": 1, "seed": 42,
        "think_ms": 0, "input_lengths": [16, 32], "output_tokens": 2, "timeout_ms": 10000,
        "max_run_ms": 10000, "requests": [
            {"request_id": "a", "input_tokens": 16, "output_tokens": 2},
            {"request_id": "b", "input_tokens": 32, "output_tokens": 3}]}
    try:
        predict_closed_loop(Experiment(), fixed_stream)
    except SimulationError as error:
        output["fixed_sequential_stream"] = {"code": error.code, "message": str(error)}
    else:
        output["fixed_sequential_stream"] = {"accepted": True, "semantic_validation_still_required": True}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
