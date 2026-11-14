import statistics
from collections import deque
from .io import require


def percentile(values, q):
    if not values:
        return None
    a = sorted(values)
    index = (len(a) - 1) * q
    low = int(index)
    return a[low] + (a[min(low + 1, len(a) - 1)] - a[low]) * (index - low)


def distribution(values):
    return {"p50": percentile(values, .5), "p95": percentile(values, .95), "p99": percentile(values, .99)}


def get_metric(metrics, path):
    value = metrics
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def calculate(rows, evaluation):
    require(rows, "E_MOCK_INVALID", "backend returned no request observations", 4)
    completed = [r for r in rows if r["status"] == "completed" and r["output_tokens"] >= r["output_tokens_target"]]
    ttft = [r["first_token_ms"] - r["ready_ms"] for r in rows if r["first_token_ms"] is not None and r["ready_ms"] is not None]
    tpots = [(r["finished_ms"] - r["first_token_ms"]) / (r["output_tokens"] - 1) for r in completed if r["output_tokens"] >= 2]
    threshold = {c["metric"].split(".")[0]: c["value"] for c in evaluation["constraints"]
                 if c["value"] is not None and c["status"] != "tbd" and c["metric"].startswith(("ttft_ms.", "tpot_ms."))}
    good = []
    for row in completed:
        delay = row["first_token_ms"] - row["ready_ms"]
        tpot = (row["finished_ms"] - row["first_token_ms"]) / (row["output_tokens"] - 1) if row["output_tokens"] >= 2 else None
        if delay <= threshold.get("ttft_ms", float("inf")) and (tpot is None or tpot <= threshold.get("tpot_ms", float("inf"))):
            good.append(row)
    duration = evaluation["duration_s"]
    goodput_available = "ttft_ms" in threshold and ("tpot_ms" in threshold or evaluation.get("goodput_policy") == "ttft_only_if_tpot_tbd")
    # ready_ms carries arrival or dependencies; critical path excludes actual queueing.
    by_id = {row["request_id"]: row for row in rows}
    require(len(by_id) == len(rows), "E_MOCK_INVALID", "duplicate observation IDs", 4)
    degree, children = {}, {name: [] for name in by_id}
    for row in rows:
        parents = row["depends_on"]
        require(len(set(parents)) == len(parents) and all(p in by_id for p in parents),
                "E_MOCK_INVALID", "observation dependencies must name distinct existing IDs", 4)
        degree[row["request_id"]] = len(parents)
        for parent in parents:
            children[parent].append(row["request_id"])
    pending = deque(name for name, count in degree.items() if count == 0)
    critical = {}
    while pending:
        name = pending.popleft()
        row = by_id[name]
        parents = [critical[parent] for parent in row["depends_on"]]
        critical[name] = (max(parents, default=0) + row["gap_after_deps_ms"] + row["service_ms"]
                          if row["service_ms"] is not None and all(value is not None for value in parents) else None)
        for child in children[name]:
            degree[child] -= 1
            if degree[child] == 0:
                pending.append(child)
    require(len(critical) == len(rows), "E_MOCK_INVALID", "observation dependencies contain a cycle", 4)
    service_complete = all(value is not None for value in critical.values())
    unknown = sum(r["status"] == "unknown" for r in rows)
    metrics = {"ttft_ms": distribution(ttft), "tpot_ms": distribution(tpots),
               "e2e_ms": distribution([r["finished_ms"] - r["ready_ms"] for r in completed]),
               "completion_rate": len(completed) / len(rows),
               "throughput_tok_s": sum(r["output_tokens"] for r in completed) / duration,
               "goodput_tok_s": sum(r["output_tokens"] for r in good) / duration if goodput_available else None,
               "decode_tok_s": statistics.median([1000 / v for v in tpots if v > 0]) if any(v > 0 for v in tpots) else None,
               "makespan_s": (max(r["finished_ms"] for r in completed) - min(r["ready_ms"] for r in rows)) / 1000 if len(completed) == len(rows) else None,
               "critical_path_s": max(critical.values()) / 1000 if service_complete else None}
    assumptions = []
    if not goodput_available:
        assumptions.append("Goodput is null: required TTFT/TPOT thresholds are not declared.")
    elif "tpot_ms" not in threshold:
        assumptions.append("Plan W2 9.2 exception: TPOT threshold is tbd; goodput applies only the declared TTFT threshold.")
    if not service_complete:
        assumptions.append("Critical path is null: independent service-time observations are unavailable.")
    if unknown:
        # A lost observation may contain additional completed output or latency samples.
        # Known rows remain available, but whole-workload aggregates are not inferred.
        for metric in metrics:
            metrics[metric] = distribution([]) if metric in {"ttft_ms", "tpot_ms", "e2e_ms"} else None
        assumptions.append("Workload metrics are unavailable because some request observations are unknown.")
    counts = {"total": len(rows), "completed": len(completed), "failed": len(rows) - len(completed) - unknown}
    if unknown:
        counts["unknown"] = unknown
    return metrics, counts, assumptions


def verdict(metrics, task, quality, status, failure):
    constraints = []
    for constraint in task["constraints"]:
        observed = get_metric(metrics, constraint["metric"])
        value = constraint["value"]
        met = None
        if value is not None and constraint["status"] != "tbd" and observed is not None:
            met = {"<": lambda: observed < value, "<=": lambda: observed <= value,
                   ">": lambda: observed > value, ">=": lambda: observed >= value,
                   "==": lambda: observed == value}[constraint["op"]]()
        constraints.append({**constraint, "observed": observed, "met": met})
    if failure == "oom":
        return "constraints_violated", constraints
    if status != "succeeded":
        return "not_evaluated", constraints
    if any(c["met"] is False for c in constraints) or quality.get("passes_floor") is False:
        return "constraints_violated", constraints
    pending = any(c["met"] is None for c in constraints if c["value"] is not None and c["status"] != "tbd")
    return ("not_evaluated" if pending or quality.get("passes_floor") is not True else "constraints_met"), constraints
