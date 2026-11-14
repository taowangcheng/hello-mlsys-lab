"""Run ledger integration for the simulator's public HTTP API only."""
from copy import deepcopy
import hashlib
import json

from ..contracts.simulator import RunRequest
from ..core.io import ROOT, TERMINAL, hashed, inside, require
from ..core.locking import file_lock
from ..core.store import Store
from .simulator_http import SimulatorAPIError, SimulatorHTTPClient, SimulatorProtocolError, SimulatorTransportError


def request_bytes(root, spec, identifier, settings):
    """Translate explicitly declared identities and inline the verified trace."""
    aliases = settings.get("model_aliases", {})

    def model(name):
        require(name in aliases or name in aliases.values(), "E_OUT_OF_SPACE",
                "No explicit simulator catalog identity for model " + name)
        return aliases.get(name, name)

    reference = spec["workload"]["requests"]
    path = inside(root / "task", reference["path"])
    require(hashed(path) == reference["sha256"], "E_FROZEN_CHANGED", "Workload trace changed")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for row in rows:
        if row.get("target_model"):
            row["target_model"] = model(row["target_model"])
    strategy = deepcopy(spec["strategy"])
    for deployment in strategy["deployments"]:
        deployment["model"] = model(deployment["model"])
        require(deployment["weight_precision"] == deployment["kv_precision"] == "BF16", "E_OUT_OF_SPACE",
                "The pinned simulator catalog supports BF16 weights and KV only")
    require(all(device["hardware"] == "rx9070xt" for node in spec["infra"]["nodes"] for device in node["devices"]),
            "E_OUT_OF_SPACE", "The pinned simulator catalog supports RX9070XT only")
    execution = {key: spec["execution"][key] for key in ("seed", "repeat", "split", "holdout", "initial_state", "warmup")}
    execution["max_simulated_ms"] = spec["evaluation"]["duration_s"] * 1000
    payload = {"schema": "mlab.simulator.v1", "client_request_id": identifier, "catalog_version": settings["catalog_version"],
               "task": spec["task"], "candidate_id": spec["candidate_id"],
               "workload": {"mode": spec["workload"]["mode"], "requests": rows, "source_sha256": reference["sha256"]},
               "infra": spec["infra"], "strategy": strategy, "evaluation": spec["evaluation"], "execution": execution}
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    RunRequest.model_validate_json(raw, strict=True)
    return raw


def execute(root, run, spec, settings):
    """Serialize local submissions to the service's one slot, without retries."""
    from .local import finalize
    endpoint = settings["base_url"]
    identifier = hashlib.sha256(str(root).encode()).hexdigest()[:16] + ":" + run["id"]
    raw = request_bytes(root, spec, identifier, settings)
    wire = {"simulator-request.json": raw}
    provenance = {"source": "external simulator HTTP API", "endpoint": endpoint,
                  "model_aliases": settings["model_aliases"], "expected_version": settings["version"],
                  "input_sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
    lock = ROOT / "workspaces/.simulator-locks" / (hashlib.sha256(endpoint.encode()).hexdigest() + ".lock")
    with file_lock(lock):
        store = Store(root)
        try:
            store.begin()
            current = store.get("run", run["id"])
            if current["status"] in TERMINAL:
                store.rollback()
                return
            current.update(status="running", progress={"phase": "simulator_api"})
            store.put("run", run["id"], current)
            store.commit()
        finally:
            store.close()
        try:
            exchange = SimulatorHTTPClient(endpoint, timeout_s=spec["evaluation"]["timeout_s"] + 10).simulate(raw)
            wire["simulator-response.json"] = exchange["response_bytes"]
            result = exchange["result"]
            if result["backend"]["version"] != settings["version"]:
                raise SimulatorProtocolError("Simulator version differs from the configured pin", exchange["response_bytes"])
            if result["catalog_version"] != settings["catalog_version"]:
                raise SimulatorProtocolError("Simulator catalog differs from the configured pin", exchange["response_bytes"])
            reverse = {value: key for key, value in settings["model_aliases"].items()}
            rows = result["observations"]
            # The public response remains untouched in the wire artifact.
            for row in rows:
                if row.get("deployment_model"):
                    row["deployment_model"] = reverse.get(row["deployment_model"], row["deployment_model"])
            effective = deepcopy(result["effective_config"])
            if effective:
                for deployment in effective["strategy"]["deployments"]:
                    deployment["model"] = reverse.get(deployment["model"], deployment["model"])
            provenance.update({key: result[key] for key in ("calibration", "catalog_version", "model_revisions", "native_failure_class", "unsupported_fields")})
            provenance["effective_config"] = effective["strategy"] if effective else None
            wire["simulator-exchange.json"] = json.dumps({"status": 200, "headers": exchange["headers"]}).encode()
            finalize(root, run["id"], result["status"], result["failure_class"], rows, result["metrics"], result["logs"],
                     provenance, external=result, wire=wire)
        except SimulatorAPIError as error:
            wire["simulator-response.json"] = error.raw
            wire["simulator-exchange.json"] = json.dumps({"status": error.status, "headers": error.headers}).encode()
            failure = "gpu_busy" if error.status == 429 else "timeout" if error.status == 504 else "config_mismatch" if error.status in {400, 413, 422} else "backend_error"
            finalize(root, run["id"], "timed_out" if failure == "timeout" else "failed", failure,
                     [], {}, [str(error), "No automatic retry was attempted."], provenance, wire=wire)
        except SimulatorTransportError as error:
            finalize(root, run["id"], "failed", "lost", [], {}, [str(error)], provenance, wire=wire)
        except SimulatorProtocolError as error:
            wire["simulator-response.json"] = error.raw
            finalize(root, run["id"], "failed", "backend_error", [], {}, [str(error)], provenance, wire=wire)
