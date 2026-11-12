import json
import copy
import datetime
import os
import random
import re
import shlex
import shutil
import sys
import time
from pathlib import Path
from ..contracts import ENVIRONMENT_FAILURES, Candidate, MeasurementPlan, Plan, RunSpec, TaskDefinition
from .io import ROOT, TERMINAL, LabError, atomic, dump, hashed, inside, load, project_claude, require, tool_path
from .metrics import get_metric, percentile
from .locking import budget_locked
from .store import Store, now

TASKS = {"9.1": "9.1-edge", "9.2": "9.2-sla", "9.3": "9.3-dag"}
MODES = ("guided", "open")
# The task's current round, current measurement and the configuration the student selected last.
BLANK = {"workflow": None, "measurement": None, "selection": None}
RUN_ID = re.compile(r"\b(?:sim|hw|mock)_\d{4}\b")
# Record headings. The hypothesis and the plan comments are the student's own words; the page and mlab find them by these headings.
HYPOTHESIS_TEMPLATE = "## Hypothesis\n\n\n## Alternative explanation\n\n\n## What result would prove me wrong\n\n"
REVIEW_TEMPLATE = "## Comments on the plan\n\n\n## Changes and responses\n\n"
POINTS_TEMPLATE = """# Hardware points, in the student's own words. Fields of each point:
#   candidate  a candidate ID from a decided round
#   workflow   the round of the candidate, e.g. r002; when omitted, the latest round that has it
#   workload   development, or one of the task's holdout workloads: {holdouts}
#   runs       how many times to measure the configuration (1-4); more than 1 shows the noise between runs
#   rationale  why this point, in the student's own words
# Example:
# points:
# - {{candidate: c20, workflow: r002, workload: development, runs: 1, rationale: "my pick, but the simulation is only 3% under the SLA"}}
schema: mlab.v1
measurement: {name}
points: []
"""


def sections(text):
    """Markdown body by heading; text before the first heading is under ''."""
    parts, current = {"": []}, ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            current = line.strip().lstrip("#").strip()
            parts.setdefault(current, [])
        else:
            parts[current].append(line)
    return {key: "\n".join(value).strip() for key, value in parts.items()}


def hypothesis_parts(text):
    """The hypothesis and its falsification condition, found by their headings."""
    parts = {key.lower(): value for key, value in sections(text).items()}
    claim = next((v for k, v in parts.items() if "hypothesis" in k and not any(w in k for w in ("wrong", "falsif", "alternative"))), "") or parts.get("", "")
    falsifier = next((v for k, v in parts.items() if "wrong" in k or "falsif" in k), "")
    return claim, falsifier


def comments(text):
    parts = {key.lower(): value for key, value in sections(text).items()}
    return next((v for k, v in parts.items() if "comment" in k), "") or parts.get("", "")


def explicit_candidate(candidate):
    """A frozen candidate as a candidate file again: only fields set on purpose, so the decision-space check still passes."""
    return {"schema": "mlab.v1", **Candidate.model_validate(candidate).model_dump(mode="json", by_alias=True, exclude_defaults=True)}


def clear_repeated_errors(record):
    """A command that moved the work forward ends any run of repeated mistakes."""
    for code in ("E_SCHEMA", "E_INCOMPARABLE"):
        record["errors"].pop(code, None)


def find_workspace(path=None):
    root = Path(path or Path.cwd()).resolve()
    for item in [root, *root.parents]:
        if (item / ".mlab/config.json").is_file():
            return item
    raise LabError("E_SCHEMA", "Not in an mlab workspace; run mlab init first", 2)


def initialize(destination, task_id, simulator="llm", provider=None, hardware="standin", mode="guided"):
    from ..backends.local import ROLES
    provider = provider or "codex-luna"
    profile = load(inside(ROOT / "agent/profiles", provider + ".json")) if (ROOT / "agent/profiles" / (provider + ".json")).is_file() else None
    require(profile is not None, "E_SCHEMA", "unknown provider profile", 2)
    use_codex = profile.get("backend") == "codex"
    require(task_id in TASKS, "E_SCHEMA", "task must be 9.1, 9.2 or 9.3", 2)
    require(mode in MODES, "E_SCHEMA", "mode must be guided or open", 2)
    require(ROLES.get(simulator) == "simulator" and ROLES.get(hardware) == "hardware", "E_SCHEMA", "unknown simulator or hardware backend", 2)
    destination = Path(destination).resolve()
    require(not destination.exists(), "E_SCHEMA", "destination exists; choose a new directory", 2)
    source = ROOT / "tasks" / TASKS[task_id]
    task = dump(TaskDefinition.model_validate(load(source / "task.yaml")))
    for reference in task["frozen"].values():
        require(hashed(inside(source, reference["path"])) == reference["sha256"], "E_FROZEN_CHANGED", "source task file hash mismatch")
    destination.mkdir(parents=True)
    shutil.copytree(source, destination / "task")
    shutil.copytree(ROOT / "template", destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".claude", "CLAUDE.md") if use_codex else None)
    catalog = {"models": {m["id"]: m["params_b"] for m in load(ROOT / "catalog/models/sizes.yaml")},
               "hardware": {p.stem: load(p) for p in (ROOT / "catalog/hardware").glob("*.yaml")},
               "infra": {p.stem: load(p) for p in (ROOT / "catalog/infra").glob("*.yaml")}}
    atomic(destination / "task/catalog.json", catalog)
    # Package-local synthetic holdouts are explicitly marked draft; never derive new measured evidence.
    base = [json.loads(s) for s in inside(source, task["frozen"]["workload_requests"]["path"]).read_text(encoding="utf-8").splitlines() if s]
    from .workloads import generate, write_workload
    for i, name in enumerate(task["holdouts"]):
        variant = {"short_prompt_distribution": "short_mix", "long_prompt_distribution": "long_mix"}.get(name, name)
        rows = base if name == "one_dimension_change" else generate(task_id, base, 1042 + i, variant)
        path = destination / "task/holdouts" / (name + ".requests.jsonl")
        write_workload(path, rows, task_id, 1042 + i, variant)
        task["frozen"]["holdout_" + name] = {"path": path.relative_to(destination / "task").as_posix(), "sha256": hashed(path)}
    atomic(destination / "task/task.yaml", task)
    manifest = {p.relative_to(destination / "task").as_posix(): hashed(p) for p in (destination / "task").rglob("*") if p.is_file()}
    atomic(destination / ".mlab/task_manifest.json", manifest)
    atomic(destination / ".mlab/config.json", {"schema": "mlab.v1", "task": task_id, "simulator": simulator, "hardware": hardware,
                                              "provider": provider, "mode": mode, "run_timeout_s": 1800, "created_at": now(),
                                              "fixture_delay_s": 0, "hardware_delay_s": 0})
    if use_codex:
        launcher = write_codex_launcher(destination)
    else:
        write_local_settings(destination, provider)
        launcher = write_launcher(destination)
    store = Store(destination)
    store.event("workspace_initialized", {"task": task_id, "simulator": simulator, "hardware": hardware, "mode": mode})
    store.put("settings", "task", dict(BLANK))
    store.commit()
    store.project()
    store.close()
    return {"workspace": str(destination), "task": task_id, "simulator": simulator, "hardware": hardware, "next_actions": [
        {"actor": "student", "do": "hypothesis", "say": f"cd into the workspace, run {launcher}, then state your hypothesis and what result would show it wrong."}]}


def write_local_settings(destination, provider=None):
    # PATH lets the hooks and the agent's Bash tool find mlab from any
    # terminal, with or without an activated environment. Secrets stay out of this file.
    env = {"PATH": tool_path()}
    if provider:
        path = inside(ROOT / "agent/profiles", provider + ".json")
        require(path.exists(), "E_SCHEMA", "unknown provider profile", 2)
        for key, value in load(path)["env"].items():
            for variable in re.findall(r"\$\{([^}]+)\}", value):
                require(os.environ.get(variable), "E_SCHEMA", f"set {variable} before selecting this provider", 2)
                value = value.replace("${" + variable + "}", os.environ[variable])
            env[key] = value
    # Skills, plugins and connectors from a personal claude.ai account stay out of lab sessions.
    atomic(destination / ".claude/settings.local.json", {"env": env, "syncClaudeAiSkills": False, "syncClaudeAiPlugins": False,
                                                        "disableClaudeAiConnectors": True})


def write_codex_launcher(destination):
    """Use the same isolated App Server and confirmation flow in the terminal."""
    launcher = destination / "codex"
    python = sys.executable
    atomic(launcher, '#!/bin/sh\nexec ' + shlex.quote(python) + ' -m mlab.terminal --workspace ' +
           shlex.quote(str(destination)) + ' "$@"\n', text=True)
    launcher.chmod(0o755)
    return "./codex"


def write_launcher(destination):
    """./claude runs the pinned Claude Code with project settings only and the agent's four tools."""
    if not project_claude().exists():
        return "claude"
    launcher = destination / "claude"
    atomic(launcher, f'#!/bin/sh\nexec {shlex.quote(str(project_claude()))} --setting-sources project,local --tools Bash,Read,Edit,Write "$@"\n', text=True)
    launcher.chmod(0o755)
    return "./claude"


def interval(rows, metric):
    """5th-95th percentile of a bootstrapped distribution metric within one run."""
    family, _, stat = metric.partition(".")
    quantile = {"p50": .5, "p95": .95, "p99": .99}.get(stat)
    if family == "ttft_ms":
        values = [r["first_token_ms"] - r["ready_ms"] for r in rows if r["first_token_ms"] is not None]
    elif family == "e2e_ms":
        values = [r["finished_ms"] - r["ready_ms"] for r in rows if r["status"] == "completed"]
    else:
        return None
    if quantile is None or len(values) < 2:
        return None
    randomizer = random.Random(0)
    draws = sorted(percentile([randomizer.choice(values) for _ in values], quantile) for _ in range(400))
    return [round(draws[20], 1), round(draws[379], 1)]


def ttft_by_request(rows):
    return {r["request_id"]: r["first_token_ms"] - r["ready_ms"] for r in rows if r["first_token_ms"] is not None}


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def margin(result, metric):
    """Headroom to the metric's constraint threshold, in percent; negative means violated."""
    for constraint in result["constraints"]:
        value, observed = constraint["value"], constraint["observed"]
        if constraint["metric"] == metric and value and type(observed) in {int, float} and constraint["status"] != "tbd":
            headroom = (value - observed) if constraint["op"] in {"<", "<="} else (observed - value)
            return round(headroom / value * 100, 1)
    return None


def rounded(value):
    return round(value, 1) if type(value) in {int, float} else value


class Lab:
    def __init__(self, root=None, readonly=False, *, workflow=None, measurement=None):
        self.root = find_workspace(root)
        self.readonly = readonly
        self.target_workflow, self.target_measurement = workflow, measurement
        self.store = Store(self.root, readonly)
        self.config = load(self.root / ".mlab/config.json")
        self.task = dump(TaskDefinition.model_validate(load(self.root / "task/task.yaml")))

    @property
    def simulator(self):
        return self.config.get("simulator") or self.config.get("backend", "llm")

    @property
    def hardware(self):
        return self.config.get("hardware", "standin")

    @property
    def mode(self):
        return self.config.get("mode", "guided")

    def close(self):
        self.store.close()

    def frozen(self):
        manifest = load(self.root / ".mlab/task_manifest.json")
        for name, expected in manifest.items():
            require(hashed(inside(self.root / "task", name)) == expected, "E_FROZEN_CHANGED", f"task file changed: {name}")

    def current(self):
        """The task's current round, current measurement and selected configuration."""
        current = {**BLANK, **(self.store.get("settings", "task") or {})}
        if self.target_workflow is not None:
            current["workflow"] = self.target_workflow
        if self.target_measurement is not None:
            current["measurement"] = self.target_measurement
        return current

    def workflow(self):
        current = self.current()
        require(current["workflow"], "E_SCHEMA", "run mlab workflow new", 2)
        return self.store.get("workflow", current["workflow"])

    def folder(self, workflow=None):
        return self.root / (workflow or self.workflow())["folder"]

    def measurement(self, required=True):
        current = self.current()
        record = self.store.get("measurement", current["measurement"]) if current["measurement"] else None
        require(record or not required, "E_SCHEMA", "run mlab measure new", 2)
        return record

    def mfolder(self, measurement=None):
        return self.root / (measurement or self.measurement())["folder"]

    def records(self, ids):
        return [self.store.get("run", identifier) for identifier in ids]

    def new_workflow(self, example=False, source=None, exploratory=False):
        current = self.current()
        if current["workflow"] and not exploratory:
            require(self.workflow()["status"] == "decided", "E_NEEDS_APPROVAL", "record and confirm the previous workflow decision first")
        index = len(self.store.all("workflow")) + 1
        identifier = f"r{index:03}"
        origin = None
        if source:
            # Validate before creating anything, so a bad source leaves no half-made round behind.
            origin = self.store.get("workflow", source)
            require(origin, "E_SCHEMA", f"unknown round {source}", 2)
            require((self.root / origin["folder"] / "frozen_plan.json").exists(), "E_SCHEMA", f"{source} was never approved; copy from an approved round", 2)
        folder = self.root / "rounds" / identifier
        folder.mkdir(parents=True)
        examples = ROOT / "examples" / TASKS[self.task["id"]]
        plan = load(examples / "plan.yaml")
        if origin:
            # Reuse the approved candidates of an earlier round; they are simulated again.
            (folder / "candidates").mkdir(parents=True)
            frozen = load(self.root / origin["folder"] / "frozen_plan.json")
            for candidate in frozen["candidates"]:
                atomic(folder / "candidates" / f"{candidate['id']}.yaml", explicit_candidate(candidate))
            plan["candidates"] = [f"candidates/{c['id']}.yaml" for c in frozen["candidates"]]
        else:
            shutil.copytree(examples / "candidates", folder / "candidates")
        plan.update(iteration=index, seeds=[42], simulator=self.simulator)
        if not example or origin:
            plan.update(question="", derived_requirements=[])
        atomic(folder / "plan.yaml", plan)
        atomic(folder / "hypothesis.md", HYPOTHESIS_TEMPLATE, text=True)
        atomic(folder / "review.md", REVIEW_TEMPLATE, text=True)
        for name in ("analysis.md", "decision.md"):
            atomic(folder / name, "", text=True)
        data = {"id": identifier, "iteration": index, "folder": str(folder.relative_to(self.root)),
                "status": "draft", "errors": {}, "locked": False, "runs": [], "created_at": now(), "source": source,
                "exploratory": exploratory}
        self.store.begin()
        self.store.put("workflow", identifier, data)
        current["workflow"] = identifier
        self.store.put("settings", "task", current)
        self.store.event("workflow_new", {"workflow": identifier, "source": source})
        self.store.commit()
        return {"workflow": identifier, "plan_path": str(folder / "plan.yaml"), "hypothesis_path": str(folder / "hypothesis.md"),
                "review_path": str(folder / "review.md")}

    def note_error(self, code, scope="workflow"):
        current = self.current()
        measurement = self.measurement(required=False)
        if scope == "measurement" and measurement and self.measure_stage(measurement) not in {None, "measured"}:
            kind, key = "measurement", measurement["id"]
        elif current["workflow"]:
            kind, key = "workflow", current["workflow"]
        else:
            return
        self.store.begin()
        record = self.store.get(kind, key)
        record["errors"][code] = record["errors"].get(code, 0) + 1
        if record["errors"][code] >= 3:
            record["locked"] = True
        self.store.put(kind, key, record)
        self.store.event(kind + "_error", {kind: key, "code": code, "count": record["errors"][code]})
        self.store.commit()

    def specifications(self):
        self.frozen()
        w = self.workflow()
        require(not w["locked"], "E_REPEAT_LIMIT", "same error occurred three times; student must inspect attempts and unlock")
        folder = self.folder(w)
        plan = dump(Plan.model_validate(load(folder / "plan.yaml")))
        require(plan["task"] == self.task["id"] and plan["iteration"] == w["iteration"], "E_SCHEMA", "plan identity differs from the current round", 2)
        require(plan["question"].strip() and plan["derived_requirements"] and all(d.get("name") and d.get("value") and d.get("derivation") for d in plan["derived_requirements"]),
                "E_SCHEMA", "question and derived_requirements(name,value,derivation) are mandatory", 2)
        hypothesis = inside(folder, plan["hypothesis_ref"])
        claim, falsifier = hypothesis_parts(hypothesis.read_text(encoding="utf-8") if hypothesis.is_file() else "")
        if not w.get("exploratory") and (not claim or not falsifier):
            raise LabError("E_SCHEMA", "hypothesis.md needs the student's hypothesis and, under '## What result would prove me wrong', what result would show it wrong", 2, actions=[
                {"actor": "agent", "do": "ask", "say": "Ask the student and record their own words in hypothesis.md. Never write the hypothesis or the falsification condition yourself."},
                {"actor": "student", "do": "hypothesis", "say": "State the hypothesis and what result would show it wrong."}])
        require(len(plan["candidates"]) >= (1 if w.get("exploratory") else self.task["min_candidates"]), "E_SCHEMA", "too few candidates", 2)
        catalog = load(self.root / "task/catalog.json")
        candidates = []
        seen = set()
        for relative in plan["candidates"]:
            candidate = dump(Candidate.model_validate(load(inside(folder, relative))))
            require(candidate["id"] not in seen, "E_SCHEMA", "duplicate candidate IDs", 2)
            seen.add(candidate["id"])
            require(candidate["infra"] in self.task["infra_options"], "E_OUT_OF_SPACE", "infra not in task decision space")
            strategy = candidate["strategy"]
            from ..backends.local import LocalBackend
            from ..backends.capabilities import check_capabilities
            check_capabilities(LocalBackend(self.root, self.simulator).capabilities(self.task), strategy, self.task["workload_mode"])
            for path, rule in self.task["decision_space"].items():
                if path == "speculative" and not strategy["speculative"]:
                    continue
                head, *tail = path.split(".")
                objects = strategy[head] if head == "deployments" else [strategy.get(head, {})]
                for obj in objects:
                    value = obj
                    for key in tail:
                        value = value.get(key) if isinstance(value, dict) else None
                    if value is None:
                        continue
                    valid = (value in rule["values"] and any(type(value) is type(v) and value == v for v in rule["values"])) if rule["type"] == "enum" else type(value) in {int, float} and rule["min"] <= value <= rule["max"]
                    require(valid, "E_OUT_OF_SPACE", f"{path}={value} outside decision space", field=path)
            # Reject nondefault parameters even if their field exists in another task's schema.
            explicit = load(inside(folder, relative))["strategy"]
            def leaves(obj, prefix=""):
                if isinstance(obj, list):
                    return [item for element in obj for item in leaves(element, prefix)]
                if isinstance(obj, dict):
                    return [item for key, value in obj.items() for item in leaves(value, prefix + "." + key if prefix else key)]
                return [prefix]
            permitted = set(self.task["decision_space"]) | {"deployments.devices"}
            for leaf in leaves(explicit):
                require(leaf in permitted, "E_OUT_OF_SPACE", f"{leaf} is not configurable in this task")
            infra = catalog["infra"][candidate["infra"]]
            devices = {d["id"] for n in infra["nodes"] for d in n["devices"]}
            for deployment in strategy["deployments"]:
                require(set(deployment["devices"]) <= devices, "E_OUT_OF_SPACE", "unknown device")
                require(len(set(deployment["devices"])) == len(deployment["devices"]), "E_SCHEMA", "duplicate device placement", 2)
                degree = deployment["parallel"]["degree"]
                require((deployment["parallel"]["kind"] != "none" or degree == 1) and len(deployment["devices"]) == degree * deployment["replicas"], "E_SCHEMA", "placement must match parallel degree times replicas", 2)
            candidates.append(candidate)
        count = len(candidates) * len(plan["seeds"])
        require(count <= self.task["budget"]["simulator"]["runs_per_batch"], "E_BUDGET", "plan exceeds the simulator's per-batch limit")
        digest = hashed({"plan": plan, "candidates": candidates, "hypothesis": hashed(hypothesis), "task": hashed(self.task)})
        return plan, candidates, digest

    def check(self):
        plan, candidates, digest = self.specifications()
        w = self.workflow()
        w["checked_hash"] = digest
        clear_repeated_errors(w)
        self.store.put("workflow", w["id"], w)
        self.store.commit()
        return {"plan_hash": digest, "candidates": [c["id"] for c in candidates], "planned_runs": len(candidates) * len(plan["seeds"])}

    def approve(self, prefix):
        plan, candidates, digest = self.specifications()
        require(len(prefix.removeprefix("sha256:")) >= 8 and digest.startswith(prefix if prefix.startswith("sha256:") else "sha256:" + prefix), "E_APPROVAL_STALE", "plan hash does not match; check and review current plan")
        w = self.workflow()
        require(w["status"] == "draft", "E_NEEDS_APPROVAL", "workflow already approved; create a new workflow for changed candidates")
        folder = self.folder(w)
        review = (folder / "review.md").read_text(encoding="utf-8") if (folder / "review.md").exists() else ""
        if not comments(review) and not w.get("exploratory"):
            raise LabError("E_NEEDS_APPROVAL", "review.md has no student comments on the plan", 3, actions=[
                {"actor": "agent", "do": "ask", "say": "Show the plan and ask the student to comment on each candidate: keep, change or drop it, and why. Record their words in review.md, then approve."},
                {"actor": "student", "do": "review", "say": "Comment on the plan, or say that you agree with it as it is."}])
        approval = {"schema": "mlab.v1", "plan_hash": digest, "at": now(), "review": review, "identity_verified": False}
        self.store.begin()
        atomic(folder / "approval.json", approval)
        atomic(folder / "frozen_plan.json", {"plan": plan, "candidates": candidates, "hash": digest})
        w.update(status="approved", approved_hash=digest, approval_hash=hashed(approval), frozen_hash=hashed(load(folder / "frozen_plan.json")))
        clear_repeated_errors(w)
        self.store.put("workflow", w["id"], w)
        self.store.event("workflow_approved", {"workflow": w["id"], "plan_hash": digest})
        self.store.commit()
        return {"workflow": w["id"], "status": "approved", "review_path": str(folder / "review.md")}

    def approved(self):
        plan, candidates, digest = self.specifications()
        w = self.workflow()
        require(w.get("approved_hash"), "E_NEEDS_APPROVAL", "the student must review and approve the plan first")
        require(w["approved_hash"] == digest, "E_APPROVAL_STALE", "plan/candidate/hypothesis changed after approval")
        for filename, key in [("approval.json", "approval_hash"), ("frozen_plan.json", "frozen_hash")]:
            require(hashed(load(self.folder(w) / filename)) == w[key], "E_FROZEN_CHANGED", filename + " changed")
        return plan, candidates, w

    def make_spec(self, candidate, seed, repeat, split, holdout=None):
        reference = self.task["frozen"]["holdout_" + holdout if holdout else "workload_requests"]
        catalog = load(self.root / "task/catalog.json")
        requests = [json.loads(line) for line in inside(self.root / "task", reference["path"]).read_text(encoding="utf-8").splitlines() if line]
        for request in requests:
            deployed = [d for d in candidate["strategy"]["deployments"] if not request.get("target_model") or d["model"] == request["target_model"]]
            require(deployed, "E_OUT_OF_SPACE", "candidate does not deploy a requested model")
            require(any(d["context_tokens"] >= request["input_tokens"] + request["output_tokens_target"] for d in deployed),
                    "E_OUT_OF_SPACE", "context cannot hold full input plus forced output")
        strategy = copy.deepcopy(candidate["strategy"])
        change = None
        metadata = inside(self.root / "task", reference["path"]).with_suffix(".meta.json")
        if self.task["id"] == "9.3" and holdout == "one_dimension_change" and metadata.exists():
            before = strategy["serving"]["max_concurrency"]
            after = max(1, before // 2) if before > 1 else 2
            strategy["serving"]["max_concurrency"] = after
            change = {"field": "serving.max_concurrency", "before": before, "after": after,
                      "source": "frozen task holdout protocol: halve concurrency (1 becomes 2)"}
        return dump(RunSpec(task=self.task["id"], candidate_id=candidate["id"],
                workload={"mode": self.task["workload_mode"], "requests": reference}, infra=catalog["infra"][candidate["infra"]],
                strategy=strategy,
                evaluation={"constraints": self.task["constraints"], "duration_s": self.task["duration_s"], "timeout_s": self.config["run_timeout_s"],
                    "goodput_policy": "ttft_only_if_tpot_tbd" if self.task["id"] == "9.2" else "both_thresholds_required"},
                execution={"seed": seed, "repeat": repeat, "split": split, "holdout": holdout,
                    **({"holdout_change": change} if change else {}), "initial_state": "empty", "warmup": None}))

    def identity(self, backend):
        from ..backends.local import BACKEND_VERSIONS
        fingerprint = {"backend": backend, "version": BACKEND_VERSIONS[backend]}
        if backend == "llm":
            from ..backends.llm import configuration
            config = configuration(self.config.get("provider"))
            fingerprint.update(model=config["model"], protocol=config["protocol"],
                               endpoint=os.environ.get("MLAB_BACKEND_IDENTITY") or config["base"])
        return fingerprint

    def run_key(self, spec, backend):
        return hashed({"spec": spec, "backend": self.identity(backend)})

    def budget_peers(self):
        """Other workspaces whose hardware runs count against this one's budget: in a course directory, every other
        session of the same task. Sessions are independent attempts; the hardware budget is the one thing they share."""
        if not (self.root.parent / "course.json").is_file():
            return []
        peers = []
        for path in sorted(self.root.parent.iterdir()):
            config = path / ".mlab/config.json"
            if path != self.root and config.is_file() and load(config).get("task") == self.task["id"]:
                peers.append(path)
        return peers

    def hardware_budget(self):
        """Hardware runs used by this task, in-flight runs included; in a course, all its sessions count. The sandbox's own count prevails."""
        def billed(store):
            return sum(1 for r in store.all("run") if r.get("role") == "hardware" and r.get("billable"))
        used = billed(self.store)
        for peer in self.budget_peers():
            other = Store(peer, readonly=True)
            try:
                used += billed(other)
            finally:
                other.close()
        total = self.task["budget"]["hardware"]["runs_total"]
        return {"used": used, "remaining": total - used, "total": total}

    @budget_locked
    def launch(self, jobs, kind, owner, folder):
        """Create runs for jobs that have no usable result yet, save the owner, then start the workers."""
        from ..backends.local import LocalBackend, ROLES, allocate, config_hash
        self.store.begin()
        try:
            existing = self.store.all("run")
            new, selected = [], []
            for job in jobs:
                spec, backend = job["spec"], job["backend"]
                role = ROLES[backend]
                key = self.run_key(spec, backend)
                matches = [r for r in existing + new if r["key"] == key and r.get(kind) == owner["id"]]
                retry = ENVIRONMENT_FAILURES if role == "hardware" else {"backend_error"}
                reusable = [r for r in matches if r["failure_class"] not in retry]
                if reusable:
                    selected.append(reusable[-1]["id"])
                    continue
                since_unlock = [r for r in matches if r["created_at"] >= owner.get("unlocked_at", "")]
                if len(since_unlock) >= 3:
                    raise LabError("E_REPEAT_LIMIT", "backend failed three times for this slot; inspect logs and unlock")
                run_id = allocate(self.store, self.root, folder.relative_to(self.root), role, [r["id"] for r in new])
                new.append({"id": run_id, "key": key, "spec_hash": hashed(spec),
                            "workflow": owner["id"] if kind == "workflow" else None,
                            "measurement": owner["id"] if kind == "measurement" else None,
                            "role": role, "point": job.get("point"), "_spec": spec,
                            "config_hash": config_hash(spec, job.get("strategy")),
                            "candidate": spec["candidate_id"], "split": spec["execution"]["split"], "holdout": spec["execution"]["holdout"],
                            "backend": backend, "backend_identity": self.identity(backend),
                            "status": "queued", "failure_class": None, "billable": role == "hardware",
                            "folder": str((folder / "runs" / run_id).relative_to(self.root)),
                            "created_at": now(), "progress": {"phase": "queued"}, "pid": None})
                selected.append(run_id)
            hardware = [r for r in new if r["role"] == "hardware"]
            if hardware:
                budget = self.hardware_budget()
                require(len(hardware) <= budget["remaining"], "E_BUDGET",
                        f"hardware budget: {budget['remaining']} of {budget['total']} runs left, {len(hardware)} needed")
            for record in new:
                path = self.root / record["folder"]
                path.mkdir(parents=True)
                atomic(path / "spec.json", record.pop("_spec"))
                self.store.put("run", record["id"], record)
                self.store.event("run_submitted", {"run_id": record["id"], "key": record["key"], "role": record["role"], "split": record["split"]})
            owner["runs"] = list(dict.fromkeys(owner["runs"] + selected))
            self.store.put(kind, owner["id"], owner)
            self.store.commit()
        except BaseException:
            self.store.rollback()
            raise
        for record in new:
            LocalBackend(self.root, record["backend"]).submit(load(self.root / record["folder"] / "spec.json"), record["key"])
        return selected, new

    def submit(self, split="development", candidate_id=None):
        plan, candidates, w = self.approved()
        if split != "development":
            require(w["status"] == "decided" or w.get("exploratory"), "E_NEEDS_APPROVAL", "validation requires a confirmed decision")
            candidates = [c for c in candidates if c["id"] == candidate_id]
            require(candidates, "E_SCHEMA", "unknown candidate for validation", 2)
        else:
            require(w["status"] != "decided", "E_NEEDS_APPROVAL", "decided workflow only accepts validation; create another workflow for development")
        require(plan["simulator"] == self.simulator, "E_APPROVAL_STALE", "simulator changed; use a new workflow with the current simulator")
        jobs = []
        for candidate in candidates:
            for seed in plan["seeds"]:
                for holdout in (self.task["holdouts"] if split == "holdout" else [None]):
                    spec = self.make_spec(candidate, seed, 1 if split == "repeat" else 0, split, holdout)
                    jobs.append({"spec": spec, "backend": self.simulator, "strategy": candidate["strategy"]})
        require(len(jobs) <= self.task["budget"]["simulator"]["runs_per_batch"], "E_BUDGET", "this batch exceeds the simulator's per-batch limit")
        if split == "development":
            w["status"] = "submitted"
        clear_repeated_errors(w)
        selected, new = self.launch(jobs, "workflow", w, self.folder(w))
        return {"run_ids": selected, "created": len(new), "status": "submitted"}

    def refresh(self, records):
        from ..backends.local import finalize, alive
        if self.readonly:
            return  # Views show runs as they are; only writable sessions finish stale ones.
        for run in records:
            if run["status"] in TERMINAL:
                continue
            created = datetime.datetime.fromisoformat(run.get("started_at", run["created_at"]))
            elapsed = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
            if elapsed > self.config["run_timeout_s"]:
                finalize(self.root, run["id"], "timed_out", "timeout", [], {}, ["Worker deadline exceeded; late outputs will be ignored."], {})
            elif (run.get("pid") and not alive(run["pid"])) or (not run.get("pid") and elapsed > 10):
                finalize(self.root, run["id"], "failed", "lost", [], {}, ["Worker exited before terminal artifacts; recovered from ledger."], {})

    def summarize(self, ids):
        self.refresh(self.records(ids))
        runs = self.records(ids)
        pending = [r for r in runs if r["status"] not in TERMINAL]
        delays = {"fixture": self.config.get("fixture_delay_s", 0), "standin": self.config.get("hardware_delay_s", 0)}
        eta = 0 if not pending else None
        if pending and all(r["backend"] in delays for r in pending):
            now_time = datetime.datetime.now(datetime.timezone.utc)
            eta = max(0, max(delays[r["backend"]] - (now_time - datetime.datetime.fromisoformat(r.get("started_at", r["created_at"]))).total_seconds() for r in pending))
        return {"done": bool(runs) and not pending, "eta_s": eta,
                "counts": {status: sum(r["status"] == status for r in runs) for status in sorted({r["status"] for r in runs})}, "run_ids": ids}

    def status(self):
        w = self.workflow()
        summary = self.summarize(w["runs"])
        return {"workflow": w["id"], "status": "completed" if summary["done"] else w["status"], "eta_s": summary["eta_s"],
                "counts": summary["counts"], "run_ids": summary["run_ids"]}

    def wait(self, maximum):
        deadline = time.monotonic() + maximum
        while True:
            state = self.status()
            if state["status"] == "completed" or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        if state["status"] != "completed":
            state["next_actions"] = [{"actor": "agent", "do": "end_turn", "say": "Still running. End this turn; recover later with mlab next."}]
        return state

    def result(self, identifier):
        run = self.store.get("run", identifier)
        require(run is not None, "E_SCHEMA", "unknown run ID", 2)
        require(run["status"] in TERMINAL, "E_NEEDS_APPROVAL", "run is not terminal")
        folder = self.root / run["folder"]
        result = load(folder / "result.json")
        require(hashed(result) == run["result_hash"] and hashed(load(folder / "spec.json")) == run["spec_hash"], "E_FROZEN_CHANGED", "run result or spec changed")
        manifest = load(folder / "manifest.json")
        if run.get("manifest_hash"):
            require(hashed(manifest) == run["manifest_hash"], "E_FROZEN_CHANGED", "manifest changed")
        for name, expected in manifest["sha256"].items():
            require(hashed(inside(folder, name)) == expected, "E_FROZEN_CHANGED", "artifact changed: " + name)
        return result, load(folder / "spec.json")

    def comparison(self, identifiers):
        """Rows and comparability of runs, without writing anything; usable on any runs of this task."""
        ids = list(identifiers)
        require(len(ids) >= 2, "E_SCHEMA", "compare requires at least two runs", 2)
        values = [self.result(i) for i in ids]
        records = [self.store.get("run", i) for i in ids]
        roles = {record.get("role", "simulator") for record in records}
        base_result, base_spec = values[0]
        checks = []
        def check(field, getter):
            seen = [getter(result, spec) for result, spec in values]
            checks.append({"field": field, "values": seen, "comparable": all(v == seen[0] for v in seen)})
        check("workload", lambda r, s: s["workload"]["requests"]["path"])
        check("evaluation", lambda r, s: hashed(s["evaluation"]))
        check("split", lambda r, s: s["execution"]["split"])
        check("evidence_level", lambda r, s: r["evidence_level"])
        check("backend", lambda r, s: r["backend"]["name"] + " " + r["backend"]["version"])
        checks.append({"field": "role", "values": [record.get("role", "simulator") for record in records], "comparable": len(roles) == 1})
        differences = []
        for result, spec in values[1:]:
            for key in ("workload", "evaluation"):
                if spec[key] != base_spec[key]:
                    differences.append(key)
            if spec["execution"]["split"] != base_spec["execution"]["split"]:
                differences.append("split")
            if result["evidence_level"] != base_result["evidence_level"] and "mock" in {result["evidence_level"], base_result["evidence_level"]}:
                differences.append("evidence_level")
            if result["evidence_level"] == base_result["evidence_level"] and result["backend"] != base_result["backend"]:
                differences.append("backend/version")
        if len(roles) > 1:
            differences.append("role")
        metric = self.task["key_metrics"][0]
        rows = [{"run_id": result["run_id"], "candidate": result["candidate_id"],
                 "owner": record.get("workflow") or record.get("measurement"), "role": record.get("role", "simulator"),
                 "split": record["split"], "holdout": record["holdout"], "status": result["status"], "verdict": result["verdict"],
                 "key_metric_value": rounded(get_metric(result["metrics"], metric)),
                 "objective_value": rounded(get_metric(result["metrics"], self.task["objective"].get("metric", ""))),
                 "goodput_tok_s": rounded(result["metrics"].get("goodput_tok_s")), "metrics": result["metrics"],
                 "evidence": str(self.root / record["folder"] / "result.json")}
                for (result, spec), record in zip(values, records)]
        return {"rows": rows, "differences": sorted(set(differences)), "checks": checks, "key_metric": metric}

    def compare(self, identifiers):
        # Default comparisons contain only this iteration's development runs.
        # Repeat and holdout use different inputs and must not be mixed in.
        # Backend failures say nothing about a design, so they stay out by default.
        ids = identifiers or [r["id"] for r in self.store.all("run") if r.get("workflow") == self.workflow()["id"]
                              and r["split"] == "development" and r["failure_class"] != "backend_error"]
        found = self.comparison(ids)
        require("role" not in found["differences"], "E_INCOMPARABLE", "simulator and hardware runs are paired with mlab align, not ranked together", 2)
        require(not found["differences"], "E_INCOMPARABLE", "incomparable fields: " + ", ".join(found["differences"]), 2)
        path = self.root / "comparisons" / (str(time.time_ns()) + ".json")
        atomic(path, {"schema": "mlab.v1", "rows": [{key: row[key] for key in ("run_id", "candidate", "status", "verdict", "metrics", "evidence")}
                                                   for row in found["rows"]],
                      "warning": "p99 is unreliable for these small draft workloads; all numbers are mock."})
        self.store.event("comparison", {"runs": ids, "path": str(path.relative_to(self.root))})
        self.store.commit()
        return {"rows": [{key: row[key] for key in ("run_id", "candidate", "verdict", "key_metric_value", "objective_value", "goodput_tok_s")}
                         for row in found["rows"]],
                "key_metric": found["key_metric"],
                "result_path": str(path), "warning": "small-sample p99; mock only"}

    def backend_failures(self, workflow):
        """Runs whose latest attempt failed for backend reasons; they say nothing about the design."""
        latest = {r["spec_hash"]: r for r in self.store.all("run") if r.get("workflow") == workflow["id"]}
        return [r for r in latest.values() if r["failure_class"] == "backend_error"]

    def stage(self, w):
        if not w:
            return "new_workflow"
        if w["locked"]:
            return "locked"
        if w["status"] == "draft":
            path = self.folder(w) / "hypothesis.md"
            claim, falsifier = hypothesis_parts(path.read_text(encoding="utf-8") if path.exists() else "")
            return "plan" if claim and falsifier else "hypothesis"
        if w["status"] == "submitted":
            if not self.summarize(w["runs"])["done"]:
                return "running"
            return "retry_backend" if self.backend_failures(w) else "analysis_and_decision"
        return w["status"]

    def next_action(self, stage, w):
        """Who acts next, the exact command, and a step code the page uses to pick a form."""
        if stage == "hypothesis":
            path = self.folder(w) / "hypothesis.md"
            claim, _ = hypothesis_parts(path.read_text(encoding="utf-8") if path.exists() else "")
            if claim:
                return "student", "Say what result would show the hypothesis wrong; the agent records your words under 'What result would prove me wrong' in hypothesis.md.", "falsifier"
            return "student", "State the hypothesis, an alternative explanation, and what result would show it wrong; the agent records your words in hypothesis.md.", "hypothesis"
        if stage == "plan" and w.get("checked_hash"):
            return "student", ("Review the plan: for each candidate, keep, change or drop it, and why. The agent records the comments in review.md, "
                               f"revises and re-checks if anything changed, then runs mlab workflow approve --plan {w['checked_hash']}"), "plan_review"
        if stage == "analysis_and_decision":
            path = self.folder(w) / "analysis.md"
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return "student", ("Comment on the analysis and decide what to keep, revise or reject, and why. The agent records it in decision.md, "
                                   "citing run IDs, and runs mlab workflow decide --candidate ID."), "decision"
            return "agent", "mlab compare, then write analysis.md citing run IDs; say whether the results meet the hypothesis's falsification condition.", "analysis_draft"
        if stage == "decided":
            failed = self.backend_failures(w)
            if failed:
                retries = sorted({f"mlab validate --split {r['split']} --candidate {r['candidate']}" for r in failed})
                return "agent", "Validation hit a backend error. Rerun: " + "; ".join(retries), "validation_retry"
            return "student", ("Choose: another simulator round (mlab workflow new), hardware points (mlab measure new), "
                               "simulator validation (mlab validate --split repeat|holdout --candidate ID), or, once the selected "
                               "configuration is validated, submit the conclusion (mlab conclude)."), "next_step"
        return {"new_workflow": ("agent", "mlab workflow new", "new_round"),
                "plan": ("agent", "Fill question and derived_requirements in plan.yaml, write candidates that test the hypothesis, then mlab workflow check.", "plan_draft"),
                "approved": ("agent", "mlab workflow submit, then mlab workflow wait --max 60.", "submit"),
                "running": ("agent", "mlab workflow wait --max 60; end turn if still running.", "running"),
                "retry_backend": ("agent", "Some runs failed with a backend error. mlab workflow submit reruns only those; if they fail again, end the turn and tell the student.", "retry_backend"),
                "locked": ("student", "Review the error history, then confirm mlab workflow unlock.", "locked")}[stage]

    def decide(self, candidate_id=None):
        self.frozen()
        w = self.workflow()
        require(w.get("approved_hash"), "E_NEEDS_APPROVAL", "approve the workflow before confirming its decision")
        for filename, key in [("approval.json", "approval_hash"), ("frozen_plan.json", "frozen_hash")]:
            require(hashed(load(self.folder(w) / filename)) == w[key], "E_FROZEN_CHANGED", "approved evidence changed")
        if w["status"] == "decided":
            raise LabError("E_NEEDS_APPROVAL", f"the decision of {w['id']} is already confirmed and stays as it is", 3, actions=[
                {"actor": "agent", "do": "explain", "say": "A confirmed decision is final; do not edit decision.md or decide again. Hardware points may be "
                 "any candidate of a decided round, and the student concludes on another configuration with mlab conclude --candidate ROUND/CANDIDATE."}])
        require(not w["runs"] or self.status()["status"] == "completed", "E_NEEDS_APPROVAL", "wait for runs to reach terminal states")
        text = (self.folder() / "decision.md").read_text(encoding="utf-8").strip()
        require(text, "E_SCHEMA", "student decision and rationale required in decision.md", 2)
        if w["runs"] and not set(RUN_ID.findall(text)) & set(w["runs"]):
            raise LabError("E_SCHEMA", "decision.md must cite at least one run ID of this round as evidence", 2, actions=[
                {"actor": "agent", "do": "fix", "say": "Add the run IDs the student's reasoning relies on; keep the student's words."}])
        current = self.current()
        frozen = load(self.folder() / "frozen_plan.json")
        if candidate_id:
            candidate = next((c for c in frozen["candidates"] if c["id"] == candidate_id), None)
            require(candidate, "E_SCHEMA", "decision candidate is not in the approved plan", 2)
            w["selected_candidate"] = candidate_id
            current["selection"] = self.selection_for(w, candidate)
        result = {"text": text, "at": now(), "hash": hashed(text), "selected_candidate": candidate_id}
        atomic(self.folder() / "decision.confirmed.json", result)
        analysis_path = self.folder() / "analysis.md"
        if analysis_path.exists() and analysis_path.read_text(encoding="utf-8").strip():
            snapshot = {"text": analysis_path.read_text(encoding="utf-8"),
                        "claims": load(self.folder() / "analysis.claims.json") if (self.folder() / "analysis.claims.json").exists() else None}
            atomic(self.folder() / "analysis.confirmed.json", snapshot)
            w["analysis_hash"] = hashed(snapshot)
        w["decision_hash"] = hashed(result)
        w["status"] = "decided"
        clear_repeated_errors(w)
        self.store.put("workflow", w["id"], w)
        self.store.put("settings", "task", current)
        self.store.event("workflow_decided", {"workflow": w["id"], "decision": result})
        self.store.commit()
        return {"status": "decided", "selected_candidate": (current["selection"] or {}).get("candidate")}

    def selection_for(self, workflow, candidate):
        from ..backends.local import config_hash
        frozen = load(self.folder(workflow) / "frozen_plan.json")
        spec = self.make_spec(candidate, frozen["plan"]["seeds"][0], 0, "development")
        return {"workflow": workflow["id"], "candidate": candidate["id"], "config_hash": config_hash(spec, candidate["strategy"])}

    def unlock(self):
        w = self.workflow()
        w.update(locked=False, errors={}, unlocked_at=now())
        self.store.put("workflow", w["id"], w)
        self.store.event("workflow_unlocked", {"workflow": w["id"]})
        self.store.commit()
        return {"workflow": w["id"], "status": "unlocked"}

    # Slow flywheel: hardware measurements of points the student chose.

    def measure_stage(self, m):
        """None without a measurement; "measured" once every hardware run has finished."""
        if not m:
            return None
        if m["locked"]:
            return "measure_locked"
        if m["status"] == "draft":
            return "measure_plan"
        if m["status"] == "approved":
            return "measure_approved"
        if not self.summarize(m["runs"])["done"]:
            return "measuring"
        return "measure_retry" if self.measure_failures(m) else "measured"

    def latest_hardware(self, m):
        latest = {}
        for run in self.store.all("run"):
            if run.get("measurement") == m["id"] and run.get("role") == "hardware":
                latest[run["key"]] = run
        return sorted(latest.values(), key=lambda r: (r["point"], r["split"] != "development", r["id"]))

    def measure_failures(self, m):
        return [r for r in self.latest_hardware(m) if r["failure_class"] in ENVIRONMENT_FAILURES]

    def measure_action(self, mstage, m):
        if mstage == "measure_locked":
            return "student", "Review the error history, then confirm mlab measure unlock.", "measure_locked"
        if mstage == "measure_plan":
            if not self.summarize(m["runs"])["done"] and m["runs"]:
                return "agent", "The simulator is predicting the chosen points (no hardware budget). mlab measure wait --max 60, then mlab measure check.", "points_predicting"
            if m.get("checked_hash"):
                return "student", ("Review the points, the simulator's predictions and the hardware runs they cost; "
                                   f"the agent then runs mlab measure approve --plan {m['checked_hash']}"), "points_approve"
            return "agent", "Ask the student which points to measure and why. Write their choices into points.yaml, then mlab measure check.", "points"
        if mstage == "measure_approved":
            return "agent", "mlab measure submit, then mlab measure wait --max 60. If still queued, end the turn; the student may continue a simulator round meanwhile.", "measure_submit"
        return "agent", "Some hardware runs failed for environment reasons and used no budget. mlab measure submit reruns only those.", "measure_retry"

    def measure_new(self, exploratory=False):
        position = self.current()
        current = self.measurement(required=False)
        require(exploratory or self.measure_stage(current) in {None, "measured"}, "E_NEEDS_APPROVAL",
                f"{current['id'] if current else ''} is not finished; one measurement at a time (mlab measure status)")
        decided = [w for w in self.store.all("workflow") if w["status"] == "decided" or (exploratory and w.get("approved_hash"))]
        require(decided, "E_NEEDS_APPROVAL", "decide at least one simulator round before choosing hardware points")
        index = len(self.store.all("measurement")) + 1
        identifier = f"m{index:03}"
        folder = self.root / "measurements" / identifier
        folder.mkdir(parents=True)
        atomic(folder / "points.yaml", POINTS_TEMPLATE.format(name=identifier, holdouts=", ".join(self.task["holdouts"])), text=True)
        record = {"id": identifier, "index": index, "folder": str(folder.relative_to(self.root)),
                  "status": "draft", "errors": {}, "locked": False, "runs": [], "created_at": now(), "exploratory": exploratory}
        rounds = [{"workflow": w["id"], "candidates": [c["id"] for c in load(self.folder(w) / "frozen_plan.json")["candidates"]]} for w in decided]
        self.store.begin()
        self.store.put("measurement", identifier, record)
        position["measurement"] = identifier
        self.store.put("settings", "task", position)
        self.store.event("measurement_new", {"measurement": identifier})
        self.store.commit()
        return {"measurement": identifier, "points_path": str(folder / "points.yaml"), "decided_rounds": rounds,
                "hardware_budget": self.hardware_budget()}

    def measure_points(self, m, sources=None):
        self.frozen()
        require(not m["locked"], "E_REPEAT_LIMIT", "same error occurred three times; student must inspect attempts and run mlab measure unlock")
        plan = dump(MeasurementPlan.model_validate(load(self.mfolder(m) / "points.yaml")))
        require(plan["measurement"] == m["id"], "E_SCHEMA", f"points.yaml must say measurement: {m['id']}", 2)
        decided = [w for w in (self.store.all("workflow") if sources is None else sources)
                   if w["status"] == "decided" or (m.get("exploratory") and w.get("approved_hash"))]
        resolved, seen = [], set()
        for index, point in enumerate(plan["points"]):
            if point["workflow"]:
                w = next((x for x in decided if x["id"] == point["workflow"]), None)
                require(w, "E_SCHEMA", f"point {index}: {point['workflow']} is not a decided round", 2)
            else:
                w = next((x for x in reversed(decided) if point["candidate"] in {c["id"] for c in load(self.folder(x) / "frozen_plan.json")["candidates"]}), None)
                require(w, "E_SCHEMA", f"point {index}: {point['candidate']} is not a candidate of any decided round", 2)
            frozen = load(self.folder(w) / "frozen_plan.json")
            candidate = next((c for c in frozen["candidates"] if c["id"] == point["candidate"]), None)
            require(candidate, "E_SCHEMA", f"point {index}: {point['candidate']} is not in {w['id']}", 2)
            require(point["workload"] == "development" or point["workload"] in self.task["holdouts"], "E_SCHEMA",
                    f"point {index}: workload must be development or one of {', '.join(self.task['holdouts'])}", 2)
            require(point["rationale"].strip(), "E_SCHEMA", f"point {index}: write the student's rationale", 2)
            slot = (w["id"], point["candidate"], point["workload"])
            require(slot not in seen, "E_SCHEMA", f"point {index} repeats another point; raise runs instead", 2)
            seen.add(slot)
            resolved.append({**point, "workflow": w["id"], "index": index, "seed": frozen["plan"]["seeds"][0], "candidate_config": candidate})
        total = sum(p["runs"] for p in resolved)
        limit = self.task["budget"]["hardware"]["runs_per_batch"]
        require(total <= limit, "E_BUDGET", f"{total} hardware runs exceed the per-batch limit of {limit}")
        digest = hashed({"plan": plan, "points": [[p["workflow"], p["candidate"], p["seed"]] for p in resolved], "task": hashed(self.task)})
        return plan, resolved, digest

    def point_specs(self, point):
        holdout = None if point["workload"] == "development" else point["workload"]
        candidate = point["candidate_config"]
        hardware = [self.make_spec(candidate, point["seed"], r, "holdout" if holdout else ("development" if r == 0 else "repeat"), holdout)
                    for r in range(point["runs"])]
        simulated = self.make_spec(candidate, point["seed"], 0, "holdout" if holdout else "development", holdout)
        return hardware, simulated

    def sim_prediction(self, spec):
        key = self.run_key(spec, self.simulator)
        done = [r for r in self.store.all("run") if r["key"] == key and r["status"] == "succeeded"]
        return done[-1] if done else None

    def prediction_row(self, point, run):
        result, spec = self.result(run["id"])
        metric = self.task["key_metrics"][0]
        return {"point": point["index"], "candidate": point["candidate"], "workflow": point["workflow"], "workload": point["workload"],
                "runs": point["runs"], "sim_run": run["id"], "metric": metric, "simulated": rounded(get_metric(result["metrics"], metric)),
                "sim_verdict": result["verdict"], "sim_margin_pct": margin(result, metric)}

    def measure_check(self):
        m = self.measurement()
        require(m["status"] == "draft", "E_NEEDS_APPROVAL", "these points are already approved; start a new measurement to change them")
        plan, points, digest = self.measure_points(m)
        total = sum(p["runs"] for p in points)
        budget = self.hardware_budget()
        require(total <= budget["remaining"], "E_BUDGET", f"{total} hardware runs needed, {budget['remaining']} of {budget['total']} left")
        missing, rows = [], []
        for point in points:
            _, simulated = self.point_specs(point)
            run = self.sim_prediction(simulated)
            if run:
                rows.append(self.prediction_row(point, run))
            else:
                missing.append({"spec": simulated, "backend": self.simulator, "strategy": point["candidate_config"]["strategy"], "point": point["index"]})
        if missing:
            m["checked_hash"] = None
            selected, _ = self.launch(missing, "measurement", m, self.mfolder(m))
            return {"status": "simulating", "points_without_prediction": len(missing), "run_ids": selected, "next_actions": [
                {"actor": "agent", "do": "continue", "say": "The simulator is predicting these points (no hardware budget). mlab measure wait --max 60, then mlab measure check again."}]}
        m["checked_hash"] = digest
        clear_repeated_errors(m)
        self.store.put("measurement", m["id"], m)
        self.store.commit()
        return {"plan_hash": digest, "metric": self.task["key_metrics"][0],
                "points": [{key: row[key] for key in ("candidate", "workload", "runs", "simulated", "sim_margin_pct")} for row in rows],
                "hardware_runs": total, "hardware_budget": budget}

    def measure_approve(self, prefix):
        m = self.measurement()
        require(m["status"] == "draft", "E_NEEDS_APPROVAL", "these points are already approved")
        plan, points, digest = self.measure_points(m)
        require(len(prefix.removeprefix("sha256:")) >= 8 and digest.startswith(prefix if prefix.startswith("sha256:") else "sha256:" + prefix),
                "E_APPROVAL_STALE", "points hash does not match; run mlab measure check and review again")
        require(m.get("checked_hash") == digest, "E_APPROVAL_STALE", "run mlab measure check first")
        total = sum(p["runs"] for p in points)
        budget = self.hardware_budget()
        require(total <= budget["remaining"], "E_BUDGET", f"{total} hardware runs needed, {budget['remaining']} of {budget['total']} left")
        predictions = []
        for point in points:
            _, simulated = self.point_specs(point)
            run = self.sim_prediction(simulated)
            require(run, "E_APPROVAL_STALE", "a point lost its simulator prediction; run mlab measure check")
            predictions.append(self.prediction_row(point, run))
        folder = self.mfolder(m)
        approval = {"schema": "mlab.v1", "plan_hash": digest, "at": now(), "hardware_runs": total, "sim_predictions": predictions, "identity_verified": False}
        self.store.begin()
        atomic(folder / "approval.json", approval)
        atomic(folder / "frozen_points.json", {"plan": plan, "points": points, "hash": digest})
        m.update(status="approved", approved_hash=digest, approval_hash=hashed(approval), frozen_hash=hashed(load(folder / "frozen_points.json")))
        clear_repeated_errors(m)
        self.store.put("measurement", m["id"], m)
        self.store.event("measurement_approved", {"measurement": m["id"], "plan_hash": digest, "hardware_runs": total})
        self.store.commit()
        return {"measurement": m["id"], "status": "approved", "hardware_runs": total, "sim_predictions": [
            {"candidate": p["candidate"], "workload": p["workload"], "simulated": p["simulated"], "sim_run": p["sim_run"]} for p in predictions]}

    def measure_frozen(self, m):
        folder = self.mfolder(m)
        for filename, key in [("approval.json", "approval_hash"), ("frozen_points.json", "frozen_hash")]:
            require(hashed(load(folder / filename)) == m[key], "E_FROZEN_CHANGED", filename + " changed")
        return load(folder / "frozen_points.json"), load(folder / "approval.json")

    def measure_submit(self):
        m = self.measurement()
        require(m["status"] in {"approved", "submitted"}, "E_NEEDS_APPROVAL", "the student must approve the points first (mlab measure approve)")
        require(not m["locked"], "E_REPEAT_LIMIT", "same error occurred three times; student must inspect attempts and run mlab measure unlock")
        frozen, _ = self.measure_frozen(m)
        jobs = []
        for point in frozen["points"]:
            hardware, _ = self.point_specs(point)
            jobs += [{"spec": spec, "backend": self.hardware, "strategy": point["candidate_config"]["strategy"], "point": point["index"]} for spec in hardware]
        m["status"] = "submitted"
        clear_repeated_errors(m)
        selected, new = self.launch(jobs, "measurement", m, self.mfolder(m))
        return {"measurement": m["id"], "run_ids": selected, "created": len(new), "status": "submitted", "hardware_budget": self.hardware_budget()}

    def measure_status(self):
        m = self.measurement()
        summary = self.summarize(m["runs"])
        return {"measurement": m["id"], "status": "completed" if summary["done"] else m["status"], "eta_s": summary["eta_s"],
                "counts": summary["counts"], "run_ids": summary["run_ids"]}

    def measure_wait(self, maximum):
        deadline = time.monotonic() + maximum
        while True:
            state = self.measure_status()
            if state["status"] == "completed" or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        if state["status"] != "completed":
            state["next_actions"] = [{"actor": "agent", "do": "end_turn", "say": "Still queued. End this turn; the student may continue a simulator round. Resume with mlab next."}]
        return state

    def alignment_row(self, run, prediction):
        result, spec = self.result(run["id"])
        sim_result, _ = self.result(prediction["sim_run"])
        metric = self.task["key_metrics"][0]
        folder = self.root / run["folder"]
        rows = read_rows(folder / "requests.jsonl")
        sim_rows = read_rows(self.root / self.store.get("run", prediction["sim_run"])["folder"] / "requests.jsonl")
        hardware, simulated = get_metric(result["metrics"], metric), get_metric(sim_result["metrics"], metric)
        gap = round((hardware - simulated) / simulated * 100, 1) if type(hardware) in {int, float} and simulated else None
        # The same requests ran in both, so pair them. A gap shared by most requests is systematic, not noise;
        # a gap only in the slowest requests moves the tail metrics while the rest agree.
        measured, predicted = ttft_by_request(rows), ttft_by_request(sim_rows)
        pairs = sorted(((measured[i], measured[i] / predicted[i]) for i in measured if predicted.get(i)), reverse=True)
        def summary(values):
            values = sorted(values)
            return {"median": round(percentile(values, .5), 3), "p10": round(percentile(values, .1), 3), "p90": round(percentile(values, .9), 3),
                    "min": round(values[0], 3), "max": round(values[-1], 3), "requests": len(values)} if values else None
        everyone = summary([ratio for _, ratio in pairs])
        slowest = summary([ratio for _, ratio in pairs[:max(3, len(pairs) // 4)]])
        if everyone and ((everyone["p10"] > 1 and everyone["median"] > 1.03) or (everyone["p90"] < 1 and everyone["median"] < .97)):
            shift = "all"
        elif slowest and ((slowest["min"] > 1 and slowest["median"] > 1.05) or (slowest["max"] < 1 and slowest["median"] < .95)):
            shift = "tail"
        else:
            shift = None
        beyond = shift is not None
        manifest = load(folder / "manifest.json")
        effective = (manifest.get("effective_config") or {}).get("serving", {})
        requested = spec["strategy"]["serving"]
        differences = [f"serving.{key}: {requested.get(key)} → {value}" for key, value in effective.items() if requested.get(key) != value]
        return {"point": run["point"], "candidate": run["candidate"], "workflow": prediction["workflow"], "workload": prediction["workload"],
                "run_id": run["id"], "split": run["split"], "sim_run": prediction["sim_run"], "metric": metric,
                "simulated": rounded(simulated), "hardware": rounded(hardware), "gap_pct": gap, "per_request_ratio": everyone,
                "slowest_ratio": slowest, "shift": shift, "beyond_noise": beyond, "hardware_interval": interval(rows, metric), "sim_verdict": sim_result["verdict"],
                "hardware_verdict": result["verdict"], "verdict_flip": sim_result["verdict"] != result["verdict"],
                "sim_margin_pct": margin(sim_result, metric), "hardware_margin_pct": margin(result, metric),
                "config_diff": differences,
                "evidence": [str(folder / "result.json"), str(folder / "manifest.json")]}

    def alignment(self, m):
        """Each finished hardware run of a submitted measurement next to the simulator prediction frozen at approval. Writes nothing."""
        _, approval = self.measure_frozen(m)
        predictions = {p["point"]: p for p in approval["sim_predictions"]}
        latest = self.latest_hardware(m)
        rows = [self.alignment_row(run, predictions[run["point"]]) for run in latest if run["status"] in TERMINAL]
        for row in rows:
            same = [r["hardware"] for r in rows if r["point"] == row["point"] and r["workload"] == row["workload"]]
            row["repeat_values"] = same if len(same) > 1 else []
        firsts = {}
        for row in rows:
            if row["workload"] == "development" and row["split"] == "development":
                firsts.setdefault(row["point"], row)
        def order(field):
            return [r["candidate"] for r in sorted(firsts.values(), key=lambda r: (r[field] is None, r[field] or 0))]
        ranking = {"simulated": order("simulated"), "hardware": order("hardware")}
        return {"schema": "mlab.v1", "measurement": m["id"], "metric": self.task["key_metrics"][0], "rows": rows, "ranking": ranking,
                "rank_flip": ranking["simulated"] != ranking["hardware"], "pending": [r["id"] for r in latest if r["status"] not in TERMINAL],
                "notes": ["beyond_noise compares hardware and simulation request by request on the same requests: shift=all means most requests moved "
                          "the same way, shift=tail means only the slowest few did.",
                          "All requests shifting often comes from an inaccurate parameter such as a fixed overhead; only the slowest shifting often comes "
                          "from a mechanism that appears only with long requests or high load.",
                          "hardware_interval is a per-request resampling interval within one run; it leaves out the noise between runs and is wide when "
                          "there are few requests.",
                          "The hardware stand-in's numbers are made up."]}

    def align(self, identifier=None):
        """mlab align [m001]: the pairs for analysing simulator against hardware when the student asks. Writes nothing."""
        m = self.store.get("measurement", identifier) if identifier else self.measurement()
        require(m, "E_SCHEMA", f"unknown measurement {identifier}, e.g. m001", 2)
        require(m["status"] == "submitted", "E_NEEDS_APPROVAL", f"{m['id']} has no hardware runs yet")
        data = self.alignment(m)
        require(data["rows"], "E_NEEDS_APPROVAL", f"no hardware run of {m['id']} has finished yet; mlab measure wait --max 60")
        return {"measurement": m["id"], "metric": data["metric"], "rank_flip": data["rank_flip"], "pending": data["pending"],
                "rows": [{"run": r["run_id"], "candidate": r["candidate"], "workload": r["workload"], "sim": r["simulated"], "hw": r["hardware"],
                          "gap_pct": r["gap_pct"], "beyond_noise": r["beyond_noise"], "shift": r["shift"], "verdict_flip": r["verdict_flip"],
                          "config_diff": r["config_diff"]} for r in data["rows"]],
                "how_to_read": "config_diff=[]: no recorded setting mismatch. shift=all: most requests off in one direction; "
                               "shift=tail: only the slowest ones. "
                               f"Per-request ratios and intervals: mlab show {m['id']}."}

    def measure_unlock(self):
        m = self.measurement()
        m.update(locked=False, errors={}, unlocked_at=now())
        self.store.put("measurement", m["id"], m)
        self.store.event("measurement_unlocked", {"measurement": m["id"]})
        self.store.commit()
        return {"measurement": m["id"], "status": "unlocked"}

    def state(self):
        """Where the task stands and who acts next. Pure: writes nothing, so views can call it."""
        self.frozen()
        current = self.current()
        w = self.store.get("workflow", current["workflow"]) if current["workflow"] else None
        stage = self.stage(w)
        m = self.measurement(required=False)
        mstage = self.measure_stage(m)
        if stage == "locked" or mstage in {None, "measuring", "measured"}:
            actor, say, step = self.next_action(stage, w)
            if mstage == "measuring" and stage != "locked":
                say = f"Hardware runs of {m['id']} are still queued; check them later with mlab measure wait --max 60. Meanwhile: " + say
            elif mstage == "measured" and stage in {"decided", "new_workflow"}:
                say = (f"Hardware results of {m['id']} are in. If the student wants them compared with the simulator, "
                       f"mlab align {m['id']} gives the pairs. " + say)
        else:
            actor, say, step = self.measure_action(mstage, m)
        def rel(path):
            return path.relative_to(self.root).as_posix()
        previous = None
        decisions = [e for e in self.store.all("workflow") if e["status"] == "decided"]
        if decisions:
            previous = rel(self.folder(decisions[-1]) / "decision.confirmed.json")
        selection = current["selection"] or {}
        latest = self.latest_conclusion()
        state = {"schema": "mlab.v1", "task": self.task["id"], "workflow": w["id"] if w else None, "stage": stage,
                 "measurement": m["id"] if m else None, "measurement_stage": mstage, "mode": self.mode,
                 "budget": {"simulator": "unlimited", "hardware": self.hardware_budget()},
                 "selected_candidate": selection.get("candidate"),
                 "selection": f"{selection['workflow']}/{selection['candidate']}" if selection else None,
                 "previous_decision": previous, "conclusion": latest["conclusion"] if latest else None,
                 "next_actions": [{"actor": actor, "do": "continue", "say": say, "step": step}]}
        if w:
            # Drafts can be temporarily invalid while the student or assistant edits them.
            # Validation still rejects them; reading progress must remain available.
            try:
                plan = load(self.folder(w) / "plan.yaml")
            except (OSError, ValueError, LabError):
                plan = {}
            state["context"] = {"question": plan.get("question", "") if isinstance(plan, dict) else "",
                                "iteration": w["iteration"], "selected_candidate": w.get("selected_candidate"),
                                "hypothesis_path": rel(self.folder(w) / "hypothesis.md"), "review_path": rel(self.folder(w) / "review.md")}
        if m and mstage not in {None, "measured"}:
            state["measurement_context"] = {"points_path": rel(self.mfolder(m) / "points.yaml")}
        return state

    def next(self):
        state = self.state()
        if not self.readonly:
            atomic(self.root / "state.yaml", state)
        return state

    def conclusion_for(self, selection, runs):
        """Conclusion for the selected configuration, from the strongest complete validation."""
        mine = [r for r in runs if r.get("config_hash") == selection["config_hash"] and r["status"] in TERMINAL]
        def groups(role):
            found = {}
            for r in mine:
                if r.get("role", "simulator") == role:
                    key = (r["backend"], hashed(r.get("backend_identity", {})))
                    found.setdefault(key, []).append(r)
            return list(found.values())
        def complete(group):
            ok = [r for r in group if r["status"] == "succeeded" and r.get("verdict") == "constraints_met"]
            held = {r["holdout"] for r in ok if r["split"] == "holdout"}
            return {"development", "repeat", "holdout"} <= {r["split"] for r in ok} and set(self.task["holdouts"]) <= held
        hardware = [r for r in mine if r.get("role") == "hardware"]
        violated = [r["id"] for r in hardware if r.get("verdict") == "constraints_violated"]
        if violated:
            return "unresolved", f"Hardware results conflict with the selection: {', '.join(violated)} violated the constraints."
        for group in groups("hardware"):
            if complete(group):
                results = [self.result(r["id"])[0] for r in group]
                if all(r["evidence_level"] == "measured" for r in results):
                    if all(r["quality"]["status"] == "measured" for r in results):
                        return "hardware_validated", "Development, repeat and holdout runs on real hardware all meet the constraints."
                    return "unresolved", "Runs on real hardware meet the performance constraints, but quality was not evaluated on real hardware."
                return "mock_only", "Development, repeat and holdout runs on the hardware stand-in all meet the constraints; the stand-in's numbers are made up."
        for group in groups("simulator"):
            if complete(group):
                if all(self.result(r["id"])[0]["evidence_level"] == "simulated" for r in group):
                    return "simulation_validated", "Development, repeat and holdout runs on the real simulator all meet the constraints."
                return "mock_only", "Development, repeat and holdout runs in the simulator all meet the constraints; the LLM simulator and the fixture report placeholder numbers."
        return "unresolved", "The selected configuration has not completed development, repeat and holdout validation."

    def selection_of(self, reference):
        """The configuration named r002/c16, which must be a candidate of a decided round."""
        rid, _, cid = (reference or "").strip().lstrip("@").partition("/")
        w = self.store.get("workflow", rid)
        require(w and (w["status"] == "decided" or (w.get("exploratory") and w.get("approved_hash"))) and cid,
                "E_SCHEMA", f"{reference} is not a candidate of an approved exploration or decided round", 2)
        candidate = next((c for c in load(self.folder(w) / "frozen_plan.json")["candidates"] if c["id"] == cid), None)
        require(candidate, "E_SCHEMA", f"{rid} has no candidate {cid}", 2)
        return self.selection_for(w, candidate)

    def latest_conclusion(self):
        found = self.store.all("conclusion")
        return found[-1] if found else None

    def conclude(self, reference=None):
        """Submit the task's conclusion for the selected configuration. Later submissions supersede earlier ones, and exploring may go on."""
        m = self.measurement(required=False)
        require(not m or self.measure_stage(m) not in {"measuring", "measure_retry", "measure_approved"}, "E_NEEDS_APPROVAL",
                "hardware runs are still in progress; finish or cancel them first")
        current = self.current()
        selection = self.selection_of(reference) if reference else current["selection"]
        require(selection, "E_NEEDS_APPROVAL", "no configuration selected; mlab workflow decide --candidate ID or mlab conclude --candidate r002/c16")
        runs = self.store.all("run")
        for run in runs:
            if run["status"] in TERMINAL:
                self.result(run["id"])
        conclusion, reason = self.conclusion_for(selection, runs)
        evidence = [r["id"] for r in runs if r.get("config_hash") == selection["config_hash"] and r["status"] in TERMINAL]
        key = f"{len(self.store.all('conclusion')) + 1:03}"
        record = {"schema": "mlab.v1", "id": key, "task": self.task["id"], "selection": selection, "conclusion": conclusion,
                  "reason": reason, "evidence": evidence, "at": now()}
        path = self.root / "conclusions" / f"{key}.json"
        atomic(path, record)
        current["selection"] = selection
        self.store.put("settings", "task", current)
        self.store.put("conclusion", key, {**record, "hash": hashed(record)})
        self.store.event("concluded", {"conclusion": key, "level": conclusion, "selection": selection})
        self.store.commit()
        return {"submission": key, "conclusion": conclusion, "candidate": selection["candidate"],
                "selection": f"{selection['workflow']}/{selection['candidate']}", "reason": reason, "result_path": str(path)}
