import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from ..contracts import ENVIRONMENT_FAILURES, Capabilities, RequestObservation, RunResult, RunSpec
from ..core.io import ROOT, TERMINAL, LabError, atomic, dump, hashed, inside, load, require
from ..core.metrics import calculate, verdict
from ..core.store import Store, now
from . import llm

BACKEND_VERSIONS = {"fixture": "0.2.0-fixture.1", "llm": "0.2.0-llm.1", "standin": "0.1.0-standin.1"}
ROLES = {"fixture": "simulator", "llm": "simulator", "standin": "hardware"}
PREFIX = {"simulator": "sim", "hardware": "hw"}

# The simulators' shared time model: a fixed cost per request, prefill time per input token,
# and queueing above 8 concurrent requests per replica. The simulators own these numbers.
DEFAULT_PARAMS = {"base_ms": 120.0, "per_token_ms": .15, "contention_ms": 45.0}

# Hardware stand-in. It follows the same formula with four designed discrepancies, matching
# the four discrepancy types in the implementation plan. The numbers are invented for teaching.
STANDIN = {
    "base_ms": 200.0, "per_token_ms": .165, "contention_ms": 45.0,  # parameter error against the simulators' numbers
    "preempt_from": 16, "preempt_ms": 40.0,  # missing mechanism: long requests are preempted above this concurrency per replica
    "run_noise": .02, "request_noise": .02,  # run-to-run and per-request measurement variation
    "min_chunk": 512, "chunk_short_factor": .7,  # implementation difference: small prefill chunks are raised to 512
    "memory_overhead_gib": .6,  # runtime memory the simulator does not count
}


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A worker that exited but was not yet reaped is a zombie and still answers signal 0.
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def allocate(store, root, folder, role, taken=()):
    """Next run ID for this role: sim_0001, hw_0001, ..."""
    prefix = PREFIX[role] + "_"
    used = [int(r["id"][len(prefix):]) for r in store.all("run") if r["id"].startswith(prefix) and r["id"][len(prefix):].isdigit()]
    used += [int(i[len(prefix):]) for i in taken if i.startswith(prefix)]
    number = max(used, default=0) + 1
    while (Path(root) / folder / "runs" / f"{prefix}{number:04}").exists():
        number += 1
    return f"{prefix}{number:04}"


class LocalBackend:
    def __init__(self, workspace, name):
        self.workspace, self.name = Path(workspace), name
        require(name in BACKEND_VERSIONS, "E_SCHEMA", "unknown backend", 2)
        self.role = ROLES[name]

    def capabilities(self, task):
        mechanisms = ["continuous_batching", "chunked_prefill", "multi_replica", "tensor_parallel", "dag_arrival", "priority_scheduling"]
        if self.name == "llm":
            mechanisms += ["kv_budget", "preemption", "prefix_cache"]
        notes = (["Hardware stand-in: invented numbers with designed discrepancies; not a real GPU."] if self.name == "standin" else
                 ["Synthetic/LLM mock only; mechanisms are approximate, not validated system simulation."])
        return Capabilities(backend=self.name, version=BACKEND_VERSIONS[self.name], role=self.role, evidence_level="mock",
                            tasks=["9.1", "9.2", "9.3"], mechanisms=mechanisms,
                            metrics=["ttft_ms", "tpot_ms", "e2e_ms", "completion_rate", "throughput_tok_s", "goodput_tok_s", "decode_tok_s", "vram_peak_gib", "kv_peak_gib", "makespan_s", "critical_path_s"],
                            notes=notes)

    def submit(self, spec, key):
        from .capabilities import check_capabilities
        spec = dump(RunSpec.model_validate(spec))
        check_capabilities(self.capabilities(None), spec["strategy"], spec["workload"]["mode"])
        store = Store(self.workspace)
        try:
            store.begin()
            existing = [r for r in store.all("run") if r["key"] == key]
            if existing:
                run = existing[-1]
                require(run["backend"] == self.name, "E_SCHEMA", "idempotency key belongs to another backend", 2)
                require(run["spec_hash"] == hashed(spec), "E_FROZEN_CHANGED", "idempotency key reused for different spec")
            else:
                # Direct contract use: the run joins the task's current round.
                position = store.get("settings", "task")
                require(position and position.get("workflow"), "E_SCHEMA", "backend requires an initialized workflow context", 2)
                workflow = store.get("workflow", position["workflow"])
                identifier = allocate(store, self.workspace, workflow["folder"], self.role)
                folder = self.workspace / workflow["folder"] / "runs" / identifier
                run = {"id": identifier, "key": key, "spec_hash": hashed(spec), "workflow": workflow["id"],
                       "measurement": None, "role": self.role, "candidate": spec["candidate_id"], "split": spec["execution"]["split"],
                       "holdout": spec["execution"].get("holdout"), "config_hash": config_hash(spec),
                       "backend": self.name, "status": "queued",
                       "failure_class": None, "billable": self.role == "hardware",
                       "backend_identity": {"backend": self.name, "version": BACKEND_VERSIONS[self.name]},
                       "folder": str(folder.relative_to(self.workspace)), "created_at": now(), "progress": {"phase": "queued"}, "pid": None}
                atomic(folder / "spec.json", spec)
                store.put("run", identifier, run)
                workflow["runs"] = list(dict.fromkeys(workflow["runs"] + [identifier]))
                store.put("workflow", workflow["id"], workflow)
                store.event("run_submitted", {"run_id": identifier, "key": key, "source": "backend.submit"})
            should_launch = run["status"] == "queued" and not run.get("launch_reserved")
            if should_launch:
                run["launch_reserved"] = True
                store.put("run", run["id"], run)
            store.commit()
            if should_launch:
                try:
                    pid = launch(self.workspace, run["id"])
                    store.begin()
                    current = store.get("run", run["id"])
                    current["pid"] = pid
                    store.put("run", run["id"], current)
                    store.commit()
                except OSError:
                    finalize(self.workspace, run["id"], "failed", "backend_error", [], {}, ["E_BACKEND_UNAVAILABLE: worker launch failed"], {})
            return {"run_id": run["id"], "backend": self.name}
        except BaseException:
            store.rollback()
            raise
        finally:
            store.close()

    def poll(self, handle):
        store = Store(self.workspace)
        try:
            run = store.get("run", handle["run_id"])
            require(run is not None, "E_SCHEMA", "unknown run", 2)
            return run
        finally:
            store.close()

    def cancel(self, handle):
        finalize(self.workspace, handle["run_id"], "cancelled", None, [], {}, ["Cancelled by user."], {})

    def fetch(self, handle, dest):
        run = self.poll(handle)
        require(run["status"] in TERMINAL, "E_NEEDS_APPROVAL", "run has not reached a terminal state")
        origin = self.workspace / run["folder"]
        manifest = load(origin / "manifest.json")
        if run.get("manifest_hash"):
            require(hashed(manifest) == run["manifest_hash"], "E_FROZEN_CHANGED", "manifest changed")
        require(hashed(load(origin / "result.json")) == run["result_hash"], "E_FROZEN_CHANGED", "result changed")
        for name, digest in manifest["sha256"].items():
            require(hashed(inside(origin, name)) == digest, "E_FROZEN_CHANGED", "artifact changed: " + name)
        artifacts = {}
        for name in ["manifest.json", "result.json", "requests.jsonl", "events.jsonl", "logs.txt"]:
            path = origin / name
            if Path(dest).resolve() != origin.resolve():
                Path(dest).mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, Path(dest) / name)
            artifacts[name] = hashed(path)
        return artifacts


def config_hash(spec, strategy=None):
    """The system configuration under test, independent of its label and workload split."""
    evaluation = {key: value for key, value in spec["evaluation"].items() if key != "timeout_s"}
    return hashed({"infra": spec["infra"], "strategy": strategy or spec["strategy"], "evaluation": evaluation})


def launch(root, run_id):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    args = [sys.executable, "-m", "mlab.worker", "--workspace", str(root), "--run", run_id]
    with open(os.devnull, "wb") as sink:
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, start_new_session=True)
    return process.pid


def memory(spec, hardware, models, overhead=1.5):
    device_mem = {}
    weights = {"BF16": 2, "FP8": 1, "INT8": 1, "INT4": .5}
    for deployment in spec["strategy"]["deployments"]:
        amount = models[deployment["model"]] * 1e9 * weights[deployment["weight_precision"]] / 2**30
        degree = deployment["parallel"]["degree"]
        for device in deployment["devices"]:
            device_mem[device] = device_mem.get(device, 0) + amount / degree + overhead
    capacity = hardware["vram_gib"] * spec["strategy"]["serving"]["gpu_memory_utilization"]
    return max(device_mem.values()), any(value > capacity for value in device_mem.values())


def shape(spec, request):
    """Scale and multiplicative factors shared by the simulators and the stand-in."""
    deployment = next(d for d in spec["strategy"]["deployments"] if not request.get("target_model") or d["model"] == request["target_model"])
    size = float(deployment["model"].removeprefix("llm-").removesuffix("b"))
    degree = deployment["parallel"]["degree"]
    scale = (size / 3) ** .5 / (1 + .7 * (degree - 1))
    per_replica = spec["strategy"]["serving"]["max_concurrency"] / deployment["replicas"]
    return deployment, scale, per_replica


def fixture(spec, requests, peak):
    serving = spec["strategy"]["serving"]
    params = DEFAULT_PARAMS
    randomizer = random.Random(spec["execution"]["seed"] + spec["execution"]["repeat"] * 1009)
    rows = []
    for request in requests:
        deployment, scale, per_replica = shape(spec, request)
        contention = max(0, per_replica - 8) * params["contention_ms"]
        chunked = serving["chunked_prefill_tokens"] is not None
        ttft = (params["base_ms"] + request["input_tokens"] * params["per_token_ms"] + contention) * scale * (1 + randomizer.uniform(-.04, .04))
        if chunked and request.get("category") == "short":
            ttft *= .55
        tpot = (8 + (6 if deployment["weight_precision"] == "INT4" else 0) + serving["max_concurrency"] * .15) * scale
        if not serving["continuous_batching"]:
            ttft *= 1.1
            tpot *= 1.1
        rows.append({"request_id": request["request_id"], "ttft_ms": ttft, "tpot_ms": tpot, "status": "completed"})
    return {"requests": rows, "vram_peak_gib": peak, "kv_peak_gib": .75}


def standin(spec, requests, peak, hardware_name="GPU"):
    """Hardware stand-in: what a measurement returns, with the four designed discrepancies."""
    serving = spec["strategy"]["serving"]
    identity = json.dumps({"strategy": spec["strategy"], "workload": spec["workload"], "seed": spec["execution"]["seed"],
                           "repeat": spec["execution"]["repeat"]}, sort_keys=True)
    run_factor = 1 + random.Random("run:" + identity).uniform(-STANDIN["run_noise"], STANDIN["run_noise"])
    randomizer = random.Random("request:" + identity)
    effective = dict(serving)
    if serving["chunked_prefill_tokens"] is not None:
        effective["chunked_prefill_tokens"] = max(serving["chunked_prefill_tokens"], STANDIN["min_chunk"])
    rows = []
    for request in requests:
        deployment, scale, per_replica = shape(spec, request)
        contention = max(0, per_replica - 8) * STANDIN["contention_ms"]
        long = request["input_tokens"] > 1024
        preempt = STANDIN["preempt_ms"] * max(0, per_replica - STANDIN["preempt_from"]) ** 1.5 if long else 0
        ttft = (STANDIN["base_ms"] + request["input_tokens"] * STANDIN["per_token_ms"] + contention + preempt) * scale
        if effective["chunked_prefill_tokens"] is not None and request.get("category") == "short":
            ttft *= STANDIN["chunk_short_factor"]
        tpot = (9 + (7 if deployment["weight_precision"] == "INT4" else 0) + serving["max_concurrency"] * .17) * scale
        if not serving["continuous_batching"]:
            ttft *= 1.1
            tpot *= 1.1
        ttft *= run_factor * (1 + randomizer.uniform(-STANDIN["request_noise"], STANDIN["request_noise"]))
        rows.append({"request_id": request["request_id"], "ttft_ms": ttft, "tpot_ms": tpot * run_factor, "status": "completed"})
    observed = {"effective_config": {"serving": effective, "deployments": spec["strategy"]["deployments"]},
                "environment": {"kind": "hardware stand-in, not real hardware", "gpu": hardware_name, "driver": "stand-in", "rocm": "stand-in",
                                "framework": "stand-in", "image": "stand-in"}}
    return {"requests": rows, "vram_peak_gib": peak, "kv_peak_gib": .9}, observed


def observations(data, requests, spec, schedule_resources=False):
    pending = list(requests)
    estimates = {r["request_id"]: r for r in data["requests"]}
    output, completed, parent_status = [], {}, {}
    mode = spec["workload"]["mode"]
    slots = {}
    def depth(identifier, trail=()):
        require(identifier not in trail, "E_MOCK_INVALID", "cyclic DAG", 4)
        children = [r["request_id"] for r in requests if identifier in r.get("depends_on", [])]
        return 1 + max((depth(c, (*trail, identifier)) for c in children), default=0)
    while pending:
        eligible = [r for r in pending if all(parent in completed for parent in r.get("depends_on", []))]
        require(eligible, "E_MOCK_INVALID", "cyclic or missing DAG dependencies", 4)
        if schedule_resources and mode == "dag":
            priority = spec["strategy"]["scheduling"].get("priority", "fifo")
            def ordering(r):
                ready = max((completed[p] for p in r.get("depends_on", [])), default=r.get("arrival_ms", 0)) + r.get("gap_after_deps_ms", 0)
                rank = r["input_tokens"] if priority == "short_first" else -depth(r["request_id"]) if priority == "critical_path" else 0
                return ready, rank
            eligible.sort(key=ordering)
        request = eligible[0]
        pending.remove(request)
        row = estimates[request["request_id"]]
        parents = request.get("depends_on", [])
        gap = request.get("gap_after_deps_ms", 0)
        ready = (max((completed[p] for p in parents), default=request.get("arrival_ms", 0)) + gap if mode == "dag"
                 else max(completed.values(), default=0) if mode == "sequential" else request["arrival_ms"])
        start = ready
        slot = None
        if schedule_resources and mode == "dag":
            deployment = next(d for d in spec["strategy"]["deployments"] if not request.get("target_model") or d["model"] == request["target_model"])
            pool = slots.setdefault(deployment["model"], [0.] * (deployment["replicas"] * spec["strategy"]["serving"]["max_concurrency"]))
            slot = min(range(len(pool)), key=pool.__getitem__)
            start = max(ready, pool[slot])
        first = start + row["ttft_ms"]
        finish = first + (request["output_tokens_target"] - 1) * row["tpot_ms"]
        status = row["status"]
        if any(parent_status[p] != "completed" for p in parents):
            status = "failed"
        count = request["output_tokens_target"] if status == "completed" else 0
        if finish > spec["evaluation"]["duration_s"] * 1000:
            finish = max(ready, spec["evaluation"]["duration_s"] * 1000)
            first = first if first <= finish else None
            count = min(request["output_tokens_target"], max(0, 1 + int((finish - first) / row["tpot_ms"]))) if first is not None else 0
            status = "truncated" if count else "timed_out"
        if status != "completed" and count == 0:
            first = None
        observation = RequestObservation(request_id=request["request_id"], ready_ms=ready, first_token_ms=first,
                    finished_ms=finish, output_tokens=count, output_tokens_target=request["output_tokens_target"], status=status,
                    service_ms=row["ttft_ms"] + (request["output_tokens_target"] - 1) * row["tpot_ms"], depends_on=parents,
                    gap_after_deps_ms=gap)
        output.append(observation.model_dump())
        completed[request["request_id"]] = finish
        if slot is not None:
            pool[slot] = finish
        parent_status[request["request_id"]] = status
    return output


def quality(root, spec):
    table = load(root / "task/quality_table.yaml")
    values = []
    for deployment in spec["strategy"]["deployments"]:
        match = next((e for e in table["entries"] if e["model"] == deployment["model"] and e["precision"] == deployment["weight_precision"]), None)
        values.append(match["passes_floor"] if match else None)
    passes = False if False in values else None if None in values else True
    return {"status": "from_table" if None not in values else "not_evaluated", "passes_floor": passes,
            "source": "quality_table:" + spec["task"], "table_status": table["status"], "note": "hand-written draft table, not measured quality"}


def coverage(run, spec, requests):
    if run["role"] == "hardware":
        return {"in_domain": True, "extrapolated": [], "assumptions": [
            "Hardware stand-in: the numbers are made up, not measured on real hardware.",
            "The stand-in adds run noise, parameter error, a missing mechanism and configuration differences by fixed rules."]}
    return {"in_domain": True, "extrapolated": [], "assumptions": [
        "Placeholder numbers: not real system performance.", "Mock seed/repeat differences are synthetic, not independent hardware measurements."]}


def finalize(root, run_id, status, failure, rows, extra, logs, provenance):
    root = Path(root)
    store = Store(root)
    try:
        store.begin()
        run = store.get("run", run_id)
        if run["status"] in TERMINAL:
            store.rollback()
            return
        folder = root / run["folder"]
        spec = load(folder / "spec.json")
        task = load(root / "task/task.yaml")
        requests = load_requests(root, spec)
        metrics, counts, assumptions = calculate(rows, spec["evaluation"]) if rows else ({}, {"total": len(requests), "completed": 0, "failed": len(requests)}, [])
        metrics.update(extra)
        q = quality(root, spec)
        judgment, constraints = verdict(metrics, task, q, status, failure)
        numeric = []
        for constraint in constraints:
            observed, threshold = constraint["observed"], constraint["value"]
            if type(observed) in {int, float} and threshold and observed > 0:
                ratio = observed / threshold if constraint["op"] in {"<", "<="} else threshold / observed
                numeric.append({"metric": constraint["metric"], "utilization": ratio, "met": constraint["met"]})
        if spec["task"] == "9.1":
            metrics["tightest_constraint"] = max(numeric, key=lambda item: item["utilization"]) if numeric else None
        unavailable = set(task["required_metrics"]) | {metric for metric, value in metrics.items() if value is None}
        unsupported = {metric: "No value: unsupported model, missing threshold or unsuccessful run; see status/coverage." for metric in unavailable if metrics.get(metric) is None}
        for metric in unsupported:
            metrics[metric] = None
        provenance = dict(provenance)
        observed = {key: provenance.pop(key) for key in ("environment", "effective_config") if key in provenance}
        span = coverage(run, spec, requests)
        span["assumptions"] += assumptions
        source = {"llm": "llm", "fixture": "fixture", "standin": "standin"}[run["backend"]]
        result = dump(RunResult(run_id=run_id, task=spec["task"], candidate_id=spec["candidate_id"], spec_hash=run["spec_hash"],
                  backend={"name": run["backend"], "version": BACKEND_VERSIONS[run["backend"]], "service_source": source, "model": str(provenance.get("model", source))},
                  status=status, verdict=judgment, evidence_level="mock", failure_class=failure, constraints=constraints,
                  metrics=metrics, unsupported_metrics=unsupported, quality=q, coverage=span, requests=counts))
        for name, content in (("requests.jsonl", "".join(json.dumps(r) + "\n" for r in rows)),
                              ("events.jsonl", "".join(json.dumps({"request_id": r["request_id"], "ready_ms": r["ready_ms"], "finished_ms": r["finished_ms"]}) + "\n" for r in rows)),
                              ("logs.txt", "\n".join(logs) + "\n")):
            atomic(folder / name, content, text=True)
        atomic(folder / "result.json", result)
        artifacts = {name: hashed(folder / name) for name in ["spec.json", "result.json", "requests.jsonl", "events.jsonl", "logs.txt"]}
        manifest = {"schema": "mlab.v1", "spec": spec, "spec_hash": run["spec_hash"], "seed": spec["execution"]["seed"],
                    "role": run["role"], "backend": result["backend"],
                    "versions": {"mlab": "0.2.0", "metrics": "linear-v1"}, "provenance": provenance, **observed, "sha256": artifacts}
        atomic(folder / "manifest.json", manifest)
        billable = failure not in ENVIRONMENT_FAILURES if run["role"] == "hardware" else False
        run.update(status=status, failure_class=failure, verdict=judgment, ended_at=now(), result_hash=hashed(result),
                   manifest_hash=hashed(load(folder / "manifest.json")), billable=billable, progress={"phase": status})
        store.put("run", run_id, run)
        if failure == "backend_error" or (run["role"] == "hardware" and failure in ENVIRONMENT_FAILURES):
            owner =store.get("workflow", run["workflow"]) if run.get("workflow") else store.get("measurement", run["measurement"]) if run.get("measurement") else None
            if owner:
                owner["errors"]["backend_error"] = owner["errors"].get("backend_error", 0) + 1
                store.put("workflow" if run.get("workflow") else "measurement", owner["id"], owner)
        store.event("run_terminal", {"run_id": run_id, "status": status, "failure_class": failure})
        store.commit()
        store.project()
    except BaseException:
        store.rollback()
        raise
    finally:
        store.close()


def load_requests(root, spec):
    return [json.loads(line) for line in inside(root / "task", spec["workload"]["requests"]["path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
