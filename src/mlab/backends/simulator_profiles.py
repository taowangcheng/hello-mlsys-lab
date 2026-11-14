"""Explicit public configurations for isolated simulator integration exercises."""
from copy import deepcopy
import json

from ..contracts import Candidate
from ..core.io import ROOT, atomic, dump, hashed, load, require

PROFILE = "protocol-v1.20"
ALIASES = {"llm-0.6b": "qwen3-0.6b", "llm-1.7b": "qwen3-1.7b", "llm-4b": "qwen3-4b"}
NOTICE = ("Integration exercise: Qwen3 BF16 models on RX9070XT. These configurations differ from the original "
          "course baselines. Simulation is uncalibrated; sandbox measurements use demonstration data.")


def service_settings(base_url=None):
    from .simulator_http import SimulatorHTTPClient
    client = SimulatorHTTPClient(base_url or "http://127.0.0.1:8877")
    return {"base_url": client.base_url, "catalog_version": "mlsys-agent-catalog-v1", "version": "1.20.0",
            "model_aliases": dict(ALIASES)}


def prepare(root, task, catalog, profile):
    """Create a disclosed session-local task variant; never edit source tasks."""
    require(profile == PROFILE, "E_SCHEMA", "Unknown simulator exercise profile", 2)
    task_id = task["id"]
    original = deepcopy(task["frozen"])
    models = ["llm-4b", "llm-0.6b"] if task_id == "9.3" else ["llm-0.6b", "llm-1.7b", "llm-4b"]
    infra_id = "rx9070xt_x4" if task_id == "9.3" else "rx9070xt_x1"
    catalog["models"].update({"llm-0.6b": 0.6, "llm-1.7b": 1.7, "llm-4b": 4.0})
    catalog["infra"][infra_id] = {"id": infra_id, "nodes": [{"id": "node0", "devices": [
        {"id": f"gpu{i}", "hardware": "rx9070xt"} for i in range(4 if task_id == "9.3" else 1)]}]}
    task.update(status="integration_exercise", source=task["source"] + "; explicit " + PROFILE,
                infra_options=[infra_id])
    enum = lambda values: {"type": "enum", "values": values}
    span = lambda low, high: {"type": "range", "min": low, "max": high}
    task["decision_space"] = {
        "deployments.model": enum(models), "deployments.weight_precision": enum(["BF16"]),
        "deployments.kv_precision": enum(["BF16"]), "deployments.context_tokens": span(512, 40960),
        "deployments.replicas": span(1, 3 if task_id == "9.3" else 1),
        "deployments.parallel.kind": enum(["none"]), "deployments.parallel.degree": enum([1]),
        "serving.max_concurrency": span(1, 1 if task_id == "9.1" else 64),
        "serving.gpu_memory_utilization": span(0.5, 0.95), "serving.kv_fraction": span(0.1, 0.9),
        "serving.kv_budget_gib": span(0.001, 48), "serving.chunked_prefill_tokens": span(64, 4096),
        "serving.continuous_batching": enum([False] if task_id == "9.1" else [False, True]),
        "cache.prefix_cache": enum([False]), "scheduling.priority": enum(["fifo"]),
        "scheduling.preemption": enum(["none"]), "speculative": enum([False]),
    }
    path = root / "task" / task["frozen"]["workload_requests"]["path"]
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if task_id == "9.3":
        mapping = {"llm-32b": "llm-4b", "llm-7b": "llm-0.6b"}
        for row in rows:
            row["target_model"] = mapping[row["target_model"]]
        atomic(path, "".join(json.dumps(row) + "\n" for row in rows), text=True)
        task["frozen"]["workload_requests"]["sha256"] = hashed(path)
    meta = root / "task" / task["frozen"]["workload_meta"]["path"]
    atomic(meta, {"schema": "mlab.v1", "task": task_id, "profile": profile, "notice": NOTICE,
                  "original_source": original, "requests": len(rows), "mode": task["workload_mode"]})
    task["frozen"]["workload_meta"]["sha256"] = hashed(meta)
    atomic(root / "task/profile.json", {"id": profile, "notice": NOTICE, "original_source": original,
                                        "model_aliases": {model: ALIASES[model] for model in models}})
    atomic(root / "task/quality_table.yaml", {"schema": "mlab.v1", "task": task_id, "status": "not_evaluated",
        "source": "No measured quality evidence for this integration exercise", "entries": []})
    atomic(root / "task/GUIDE.md", "# Integration exercise\n\n" + NOTICE + "\n\n"
        "Keep request IDs, lengths, arrivals and dependency gaps unchanged. Choose configurations from this session's examples. "
        "Explore the original task question, compare runs and explain the evidence limits. "
        "Quality is not evaluated. Repeatability of a deterministic simulation does not establish hardware stability.\n", text=True)
    baseline = load(root / "task/baselines/baseline.yaml")
    baseline.update(id="baseline", infra=infra_id, rationale="Explicit Qwen3/BF16 simulator integration configuration")
    strategy = baseline["strategy"]
    for deployment in strategy["deployments"]:
        deployment.update(model={"llm-32b": "llm-4b", "llm-7b": "llm-0.6b"}.get(deployment["model"], "llm-1.7b"),
                          weight_precision="BF16", kv_precision="BF16")
    baseline = dump(Candidate.model_validate(baseline))
    atomic(root / "task/baselines/baseline.yaml", baseline)
    first, second = deepcopy(baseline), deepcopy(baseline)
    if task_id == "9.1":
        first.update(id="bf16_0_6b", rationale="Smaller model with the same BF16 precision")
        first["strategy"]["deployments"][0]["model"] = "llm-0.6b"
        second.update(id="bf16_1_7b", rationale="Larger model with the same BF16 precision")
    elif task_id == "9.2":
        second.update(id="c16", rationale="Concurrency 16 with all other choices unchanged")
        second["strategy"]["serving"]["max_concurrency"] = 16
    else:
        first.update(id="replica_1", rationale="One worker replica with the same available four-device infrastructure")
        first["strategy"]["deployments"][1].update(replicas=1, devices=["gpu1"])
        second.update(id="replica_3", rationale="Three worker replicas for independent branches")
    candidates = [first, second]
    if task_id == "9.3":
        third = deepcopy(second)
        third.update(id="concurrency_1", rationale="One active request per replica")
        third["strategy"]["serving"]["max_concurrency"] = 1
        candidates.append(third)
    for candidate in candidates:
        atomic(root / "task/examples/candidates" / (candidate["id"] + ".yaml"), candidate)
    task_name = {"9.1": "9.1-edge", "9.2": "9.2-sla", "9.3": "9.3-dag"}[task_id]
    plan = load(ROOT / "examples" / task_name / "plan.yaml")
    plan.update(simulator="mlsys", candidates=["candidates/" + item["id"] + ".yaml" for item in candidates])
    atomic(root / "task/examples/plan.yaml", plan)
    return rows
