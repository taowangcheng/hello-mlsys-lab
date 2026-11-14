"""mlab.v1 public contracts; export with `mlab schema --out <directory>`."""
from typing import Any, Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, model_validator

Mechanism = Literal["continuous_batching", "chunked_prefill", "multi_replica", "tensor_parallel", "kv_budget",
                    "dag_arrival", "priority_scheduling", "preemption", "prefix_cache", "speculative_decoding", "pipeline_parallel"]
FailureClass = Literal["oom", "deploy_failed", "not_ready", "config_mismatch", "gpu_busy", "interference",
                       "timeout", "lost", "backend_error", "request_failure"]
Role = Literal["simulator", "hardware"]
# Failures caused by the environment say nothing about the plan, so they never use hardware budget.
ENVIRONMENT_FAILURES = {"backend_error", "gpu_busy", "interference", "lost", "not_ready", "config_mismatch"}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)
    schema_: Literal["mlab.v1"] = Field(default="mlab.v1", alias="schema")


class Constraint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    metric: str
    op: Literal["<", "<=", ">", ">=", "=="]
    value: float | None = None
    status: str = "draft"
    source: str | None = None
    note: str | None = None


class TaskDefinition(Contract):
    id: Literal["9.1", "9.2", "9.3"]
    title: str
    status: str
    source: str
    objective: dict[str, Any]
    constraints: list[Constraint]
    required_metrics: list[str]
    key_metrics: list[str] = Field(min_length=1)  # Shown first in compare, align and reports.
    duration_s: float = Field(gt=0)
    quality_table: str
    workload_mode: Literal["sequential", "open_loop", "dag"]
    frozen: dict[str, dict[str, str]]
    infra_options: list[str]
    decision_space: dict[str, dict[str, Any]]
    budget: "Budget"
    holdouts: list[str]
    min_candidates: int = Field(default=1, ge=1)
    stages: list[str] = ["hypothesis", "requirements", "candidates", "review", "run", "analysis", "decision", "validation"]


class SimulatorBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runs_per_batch: int = Field(ge=1)  # Limits waiting time only; simulator runs have no total budget.


class HardwareBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runs_total: int = Field(ge=0)
    runs_per_batch: int = Field(ge=1)
    status: str = "draft"


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simulator: SimulatorBudget
    hardware: HardwareBudget


class Parallel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["none", "tp", "pp"] = "none"
    degree: int = Field(default=1, ge=1)


class Deployment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model: str
    weight_precision: Literal["BF16", "FP8", "INT8", "INT4"]
    kv_precision: Literal["BF16", "FP8", "INT8"] = "BF16"
    context_tokens: int = Field(ge=1)
    devices: list[str] = Field(min_length=1)
    replicas: int = Field(default=1, ge=1)
    parallel: Parallel = Parallel()


class Serving(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    max_concurrency: int = Field(default=1, ge=1)
    gpu_memory_utilization: float = Field(default=.8, gt=0, le=1)
    kv_fraction: float | None = Field(default=None, gt=0, le=1)
    kv_budget_gib: float | None = Field(default=None, gt=0)
    chunked_prefill_tokens: int | None = Field(default=None, ge=1)
    continuous_batching: bool = False


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployments: list[Deployment] = Field(min_length=1)
    serving: Serving = Serving()
    cache: dict[Literal["prefix_cache"], bool] = {}
    scheduling: dict[Literal["priority", "preemption"], str] = {}
    speculative: bool = False


class Candidate(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
    infra: str
    rationale: str
    strategy: Strategy


class Plan(Contract):
    task: str
    iteration: int = Field(ge=1)
    question: str
    hypothesis_ref: str = "hypothesis.md"
    derived_requirements: list[dict[str, str]]
    candidates: list[str] = Field(min_length=1)
    seeds: list[int] = Field(min_length=1)
    simulator: Literal["llm", "fixture", "mlsys"] = "llm"

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.seeds)) != len(self.seeds) or any(s < 0 for s in self.seeds):
            raise ValueError("seeds must be distinct nonnegative integers")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("candidate paths must be distinct")
        return self


class MeasurementPoint(BaseModel):
    """One configuration to measure on hardware, chosen by the student from a decided round."""
    model_config = ConfigDict(extra="forbid")
    candidate: str
    workflow: str | None = None  # Defaults to the latest decided round that has this candidate.
    workload: str = "development"  # development, or one of the task's holdout workloads.
    runs: int = Field(default=1, ge=1, le=4)  # More than one measures run-to-run variation.
    rationale: str


class MeasurementPlan(Contract):
    measurement: str
    points: list[MeasurementPoint] = Field(min_length=1)


TaskDefinition.model_rebuild()


class RunSpec(Contract):
    """What to run. The simulator brings its own calibration and reports its version in the manifest."""
    task: str
    candidate_id: str
    workload: dict[str, Any]
    infra: dict[str, Any]
    strategy: Strategy
    evaluation: dict[str, Any]
    execution: dict[str, Any]


class RequestObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: str
    ready_ms: float | None = Field(ge=0)
    first_token_ms: float | None = Field(default=None, ge=0)
    finished_ms: float | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    output_tokens_target: int = Field(ge=1)
    status: Literal["completed", "failed", "rejected", "timed_out", "truncated", "cancelled", "blocked", "not_started", "unknown"]
    service_ms: float | None = Field(ge=0)
    started_ms: float | None = Field(default=None, ge=0)
    deployment_model: str | None = None
    replica_index: int | None = Field(default=None, ge=0)
    device_ids: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    depends_on: list[str] = []
    gap_after_deps_ms: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def time_order(self):
        observed = [value for value in (self.ready_ms, self.started_ms, self.first_token_ms, self.finished_ms) if value is not None]
        if observed != sorted(observed):
            raise ValueError("request timestamps are out of order")
        if self.status == "completed" and (any(value is None for value in (self.ready_ms, self.first_token_ms, self.finished_ms))
                                           or self.output_tokens != self.output_tokens_target):
            raise ValueError("completed request needs complete timing and full output")
        if self.output_tokens is None and self.status != "unknown":
            raise ValueError("only unknown observations may omit the output count")
        if self.output_tokens is not None and self.output_tokens > self.output_tokens_target:
            raise ValueError("output exceeds forced target")
        if self.output_tokens == 0 and self.first_token_ms is not None:
            raise ValueError("zero output cannot have a first token")
        if self.status in {"blocked", "not_started"} and (any(value is not None for value in
                (self.ready_ms, self.started_ms, self.first_token_ms)) or self.output_tokens != 0):
            raise ValueError("unreleased requests cannot have activity")
        if self.status == "blocked" and not self.blocked_by:
            raise ValueError("blocked requests must identify their dependency failure")
        return self


class RunResult(Contract):
    run_id: str
    task: str
    candidate_id: str
    spec_hash: str
    backend: dict[str, str]
    status: Literal["succeeded", "failed", "cancelled", "timed_out"]
    verdict: Literal["constraints_met", "constraints_violated", "not_evaluated"]
    evidence_level: Literal["mock", "simulated", "measured"]
    failure_class: FailureClass | None = None
    constraints: list[dict[str, Any]]
    metrics: dict[str, Any]
    unsupported_metrics: dict[str, str]
    quality: dict[str, Any]
    coverage: dict[str, Any]
    requests: dict[str, int]


class Capabilities(Contract):
    backend: str
    version: str
    role: Role
    evidence_level: Literal["mock", "simulated", "measured"]
    tasks: list[str]
    mechanisms: list[Mechanism]
    metrics: list[str]
    notes: list[str] = []


class Backend(Protocol):
    def capabilities(self, task: TaskDefinition) -> Capabilities: ...
    def submit(self, spec: RunSpec, key: str) -> dict: ...
    def poll(self, handle: dict) -> dict: ...
    def cancel(self, handle: dict) -> None: ...
    def fetch(self, handle: dict, dest: str) -> dict: ...
