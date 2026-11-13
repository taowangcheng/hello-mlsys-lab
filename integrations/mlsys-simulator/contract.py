"""Data-contract models for negotiation, examples and validation only.

This module is not installed as a lab backend and imports no simulator code.
It validates data and causal timestamps; it never schedules or simulates work.
"""
import hashlib
from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mlab.contracts import Constraint, Strategy


class Data(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True, strict=True)


class WorkloadRequest(Data):
    request_id: str = Field(min_length=1)
    input_tokens: int = Field(ge=1, strict=True)
    output_tokens_target: int = Field(ge=1, strict=True)
    arrival_ms: float | None = Field(default=None, ge=0)
    target_model: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    gap_after_deps_ms: float = Field(default=0, ge=0)
    category: str | None = None
    prefix_group: str | None = None
    controller_id: str | None = None
    subagent_id: str | None = None


class Workload(Data):
    mode: Literal["sequential", "open_loop", "dag"]
    requests: list[WorkloadRequest] = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_graph(self):
        by_id = {row.request_id: row for row in self.requests}
        if len(by_id) != len(self.requests):
            raise ValueError("Request IDs must be unique")
        children = {name: [] for name in by_id}
        degree = {}
        for row in self.requests:
            if len(set(row.depends_on)) != len(row.depends_on) or any(parent not in by_id for parent in row.depends_on):
                raise ValueError("Dependencies must be distinct existing IDs")
            if self.mode != "dag" and (row.depends_on or row.gap_after_deps_ms):
                raise ValueError("Only DAG workloads have dependencies and gaps")
            if self.mode == "sequential" and row.arrival_ms is not None:
                raise ValueError("Sequential release follows prior termination, not explicit arrivals")
            if self.mode == "open_loop" and row.arrival_ms is None:
                raise ValueError("Open-loop requests require exact arrival_ms")
            if self.mode == "dag" and row.depends_on and row.arrival_ms is not None:
                raise ValueError("DAG children use relative dependency gaps, not absolute arrivals")
            if self.mode == "dag" and not row.depends_on and row.gap_after_deps_ms:
                raise ValueError("DAG roots have no dependency gap")
            degree[row.request_id] = len(row.depends_on)
            for parent in row.depends_on:
                children[parent].append(row.request_id)
        pending = deque(name for name, count in degree.items() if count == 0)
        visited = 0
        while pending:
            visited += 1
            for child in children[pending.popleft()]:
                degree[child] -= 1
                if degree[child] == 0:
                    pending.append(child)
        if visited != len(by_id):
            raise ValueError("Dependencies contain a cycle")
        return self


class Device(Data):
    id: str
    hardware: str


class Node(Data):
    id: str
    devices: list[Device] = Field(min_length=1)


class Infra(Data):
    id: str
    nodes: list[Node] = Field(min_length=1)


class Evaluation(Data):
    constraints: list[Constraint]
    duration_s: float = Field(gt=0)
    timeout_s: float = Field(gt=0)
    goodput_policy: Literal["both_thresholds_required", "ttft_only_if_tpot_tbd"]


class Execution(Data):
    seed: int = Field(ge=0, strict=True)
    repeat: int = Field(ge=0, strict=True)
    split: Literal["development", "repeat", "holdout"]
    holdout: str | None
    initial_state: Literal["empty"]
    warmup: None
    max_simulated_ms: float = Field(gt=0)


class RunRequest(Data):
    schema_: Literal["mlab.simulator.v1"] = Field(alias="schema")
    client_request_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    task: str
    candidate_id: str
    workload: Workload
    infra: Infra
    strategy: Strategy
    evaluation: Evaluation
    execution: Execution

    @model_validator(mode="after")
    def references(self):
        models = {deployment.model: deployment for deployment in self.strategy.deployments}
        if len(models) != len(self.strategy.deployments):
            raise ValueError("Use one deployment per model and its replicas field")
        devices = [device.id for node in self.infra.nodes for device in node.devices]
        if len(set(devices)) != len(devices):
            raise ValueError("Infra device IDs must be unique")
        used = set()
        for deployment in models.values():
            if deployment.parallel.kind == "none" and deployment.parallel.degree != 1:
                raise ValueError("No parallelism requires degree 1")
            placement = deployment.devices
            if (len(placement) != deployment.replicas * deployment.parallel.degree
                    or len(set(placement)) != len(placement) or set(placement) & used
                    or not set(placement) <= set(devices)):
                raise ValueError("Placement must use exclusive, known devices in replica/rank order")
            used.update(placement)
        if self.strategy.serving.kv_fraction is not None and self.strategy.serving.kv_budget_gib is not None:
            raise ValueError("KV fraction and absolute budget are mutually exclusive")
        for row in self.workload.requests:
            model = row.target_model or (next(iter(models)) if len(models) == 1 else None)
            if model not in models:
                raise ValueError("Every request must resolve to one deployed model")
            if row.input_tokens + row.output_tokens_target > models[model].context_tokens:
                raise ValueError("Context cannot hold input plus forced output")
        return self


class BackendIdentity(Data):
    name: str
    version: str


class Calibration(Data):
    version: str | None
    kind: Literal["uncalibrated", "calibrated", "extrapolated"]
    in_domain: bool | None
    assumptions: list[str]

    @model_validator(mode="after")
    def domain(self):
        if self.kind == "uncalibrated" and self.in_domain is not None:
            raise ValueError("Uncalibrated domain coverage is unknown")
        if self.kind == "extrapolated" and self.in_domain is not False:
            raise ValueError("Extrapolation cannot claim in-domain coverage")
        return self


class Error(Data):
    code: str
    message: str
    field: str | None = None


class EffectiveConfig(Data):
    infra: Infra
    strategy: Strategy


class RequestRecord(Data):
    request_id: str
    status: Literal["completed", "failed", "rejected", "timed_out", "cancelled", "truncated", "blocked", "not_started", "unknown"]
    ready_ms: float | None = Field(ge=0)
    first_token_ms: float | None = Field(ge=0)
    finished_ms: float | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0, strict=True)
    output_tokens_target: int = Field(ge=1, strict=True)
    started_ms: float | None = Field(default=None, ge=0)
    service_ms: float | None = Field(default=None, ge=0)
    deployment_model: str | None = None
    replica_index: int | None = Field(default=None, ge=0, strict=True)
    device_ids: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    blocked_by: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def observation(self):
        times = [t for t in (self.ready_ms, self.started_ms, self.first_token_ms, self.finished_ms) if t is not None]
        if times != sorted(times):
            raise ValueError("Observed timestamps are out of order")
        if self.output_tokens is None and self.status != "unknown":
            raise ValueError("Only unknown observations may omit the actual output count")
        if self.output_tokens is not None and self.output_tokens > self.output_tokens_target:
            raise ValueError("Output exceeds the forced target")
        if self.status == "completed":
            if self.output_tokens != self.output_tokens_target or any(t is None for t in (self.ready_ms, self.first_token_ms, self.finished_ms)):
                raise ValueError("Completed requests need full output and timing evidence")
            if self.deployment_model is None or self.replica_index is None or not self.device_ids:
                raise ValueError("Completed requests need their actual placement")
        if self.status in {"blocked", "not_started"} and (self.ready_ms is not None or self.first_token_ms is not None or self.started_ms is not None or self.output_tokens != 0):
            raise ValueError("Unreleased requests cannot have invented activity")
        if self.status == "blocked" and not self.blocked_by:
            raise ValueError("Blocked requests must name their failed dependencies")
        if self.status != "completed" and not self.failure_code:
            raise ValueError("Noncompleted records need a machine-readable reason")
        if self.output_tokens == 0 and self.first_token_ms is not None:
            raise ValueError("Zero-output requests cannot have a first token")
        if self.output_tokens and self.first_token_ms is None and self.status != "unknown":
            raise ValueError("Observed output requires its first token timestamp")
        if self.service_ms is not None and self.ready_ms is not None and self.finished_ms is not None and self.service_ms > self.finished_ms - self.ready_ms + 1e-6:
            raise ValueError("Service time cannot exceed the request's elapsed time")
        return self


class DeviceRecord(Data):
    device_id: str
    capacity_bytes: int = Field(ge=1, strict=True)
    physical_peak_bytes: int | None = Field(ge=0, strict=True)
    kv_peak_bytes: int | None = Field(ge=0, strict=True)
    kv_pool_reserved_bytes: int | None = Field(ge=0, strict=True)


class RunResponse(Data):
    schema_: Literal["mlab.simulator.v1"] = Field(alias="schema")
    client_request_id: str
    input_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["succeeded", "failed", "timed_out", "cancelled"]
    failure_class: str | None
    error: Error | None
    evidence_level: Literal["simulated", "mock"]
    backend: BackendIdentity
    catalog_version: str
    model_revisions: dict[str, str]
    calibration: Calibration
    effective_config: EffectiveConfig | None
    requests: list[RequestRecord]
    per_device: list[DeviceRecord]
    unsupported_fields: dict[str, str]
    logs: list[str]


def check_response(request_bytes, payload):
    """Check identity, complete rows and timing semantics; no performance model."""
    request = RunRequest.model_validate_json(request_bytes, strict=True)
    response = RunResponse.model_validate(payload, strict=True)
    if response.client_request_id != request.client_request_id or response.input_sha256 != "sha256:" + hashlib.sha256(request_bytes).hexdigest():
        raise ValueError("Response does not identify the submitted input bytes")
    if response.catalog_version != request.catalog_version:
        raise ValueError("The catalog version changed")
    if response.status == "succeeded":
        if response.effective_config is None:
            raise ValueError("Successful execution must identify its effective configuration")
        if response.effective_config.infra != request.infra or response.effective_config.strategy != request.strategy:
            raise ValueError("Successful execution silently changed the selected configuration")
        if set(response.model_revisions) != {item.model for item in request.strategy.deployments}:
            raise ValueError("Every deployed model needs its resolved revision identity")
    expected = {row.request_id: row for row in request.workload.requests}
    observed = {row.request_id: row for row in response.requests}
    if len(observed) != len(response.requests) or set(observed) != set(expected):
        raise ValueError("Return exactly one record per offered request, including failures")
    deployments = {item.model: item for item in request.strategy.deployments}
    previous = None
    for name, source in expected.items():
        row = observed[name]
        if row.output_tokens_target != source.output_tokens_target:
            raise ValueError("The forced output target changed")
        if any(value is not None and value > request.execution.max_simulated_ms + 1e-6
               for value in (row.ready_ms, row.started_ms, row.first_token_ms, row.finished_ms)):
            raise ValueError("An observation exceeds the declared simulation deadline")
        if row.status == "blocked":
            ancestors, pending = set(), list(source.depends_on)
            while pending:
                parent = pending.pop()
                if parent not in ancestors:
                    ancestors.add(parent)
                    pending.extend(expected[parent].depends_on)
            if any(parent not in ancestors or observed[parent].status == "completed" for parent in row.blocked_by):
                raise ValueError("Blocked causes must identify unsuccessful dependency ancestors")
        if row.ready_ms is not None:
            mode = request.workload.mode
            if mode == "sequential":
                ready = 0 if previous is None else observed[previous].finished_ms
            elif mode == "open_loop":
                ready = source.arrival_ms
            elif source.depends_on:
                parents = [observed[parent] for parent in source.depends_on]
                if any(parent.status != "completed" for parent in parents):
                    raise ValueError("A DAG child ran after a parent failed")
                ready = max(parent.finished_ms for parent in parents) + source.gap_after_deps_ms
            else:
                ready = source.arrival_ms or 0
            if ready is None or abs(row.ready_ms - ready) > 1e-6:
                raise ValueError("Ready time violates the workload release rule")
        if row.status == "completed":
            model = source.target_model or next(iter(deployments))
            deployment = deployments[model]
            degree = deployment.parallel.degree
            start = row.replica_index * degree
            if (row.deployment_model != model or row.replica_index >= deployment.replicas
                    or row.device_ids != deployment.devices[start:start + degree]):
                raise ValueError("Observed placement does not match the selected deployment")
            for field in ("started_ms", "service_ms"):
                if getattr(row, field) is None and "requests." + field not in response.unsupported_fields:
                    raise ValueError("Missing diagnostic observations need an explicit reason")
        previous = name
    if response.status == "succeeded" and any(row.status == "unknown" for row in response.requests):
        raise ValueError("Lost observations cannot be reported as a successful collection")
    device_ids = [row.device_id for row in response.per_device]
    available = {device.id for node in request.infra.nodes for device in node.devices}
    used = {device for item in request.strategy.deployments for device in item.devices}
    if len(set(device_ids)) != len(device_ids) or not set(device_ids) <= available:
        raise ValueError("Per-device observations need distinct, known device IDs")
    if not used <= set(device_ids) and "per_device" not in response.unsupported_fields:
        raise ValueError("Missing device observations need an explicit reason")
    for row in response.per_device:
        for field in ("physical_peak_bytes", "kv_peak_bytes", "kv_pool_reserved_bytes"):
            if getattr(row, field) is None and "per_device." + field not in response.unsupported_fields:
                raise ValueError("Unknown memory observations must not be disguised as zero")
    return response
