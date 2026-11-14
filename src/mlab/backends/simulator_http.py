"""HTTP transport and lossless result adaptation for the external simulator.

No engine imports, model aliases, scheduling or performance estimates live here.
The caller selects explicit catalog identities and retains the raw exchange.
"""
import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..contracts import RequestObservation
from ..contracts.simulator import RunRequest, check_response
from ..core.metrics import calculate


class SimulatorAPIError(RuntimeError):
    def __init__(self, status, error, raw, headers):
        self.status, self.error, self.raw, self.headers = status, error, raw, headers
        super().__init__(f"HTTP {status}: {error.get('code', 'INVALID_RESPONSE')}: {error.get('message', '')}")


class SimulatorTransportError(RuntimeError):
    """The remote outcome is unknown; an interrupted POST must not be retried."""


class SimulatorProtocolError(ValueError):
    def __init__(self, message, raw):
        self.raw = raw
        super().__init__(message)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def decode_response(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate response field: " + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("Non-finite response value: " + value)

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError("Simulator response must be a JSON object")
    return result


def adapt_response(request_bytes, payload):
    """Validate the wire record and calculate metrics without inventing observations."""
    request = RunRequest.model_validate_json(request_bytes, strict=True)
    response = check_response(request_bytes, payload)
    if response.evidence_level != "simulated":
        raise ValueError("The simulator HTTP client requires real simulated evidence")
    inputs = {row.request_id: row for row in request.workload.requests}
    rows = [RequestObservation(**record.model_dump(), depends_on=inputs[record.request_id].depends_on,
                               gap_after_deps_ms=inputs[record.request_id].gap_after_deps_ms).model_dump()
            for record in response.requests]
    metrics, counts, notes = calculate(rows, request.evaluation.model_dump())
    devices = {item.device_id: item for item in response.per_device}
    used = {device for deployment in request.strategy.deployments for device in deployment.devices}
    unsupported = {}
    for metric, field in (("vram_peak_gib", "physical_peak_bytes"), ("kv_peak_gib", "kv_peak_bytes")):
        values = [getattr(devices[device], field) if device in devices else None for device in sorted(used)]
        metrics[metric] = max(values) / 2**30 if values and all(value is not None for value in values) else None
        if metrics[metric] is None:
            unsupported[metric] = response.unsupported_fields.get("per_device." + field,
                response.unsupported_fields.get("per_device", "Complete device observations are unavailable."))
    metrics["per_device"] = [record.model_dump() for record in response.per_device]
    if metrics["critical_path_s"] is None:
        unsupported["critical_path_s"] = response.unsupported_fields.get("requests.service_ms",
            "Complete independent service-time observations are unavailable.")
    if counts.get("unknown"):
        for metric in ("ttft_ms", "tpot_ms", "e2e_ms", "completion_rate", "throughput_tok_s", "goodput_tok_s",
                       "decode_tok_s", "makespan_s", "critical_path_s"):
            unsupported[metric] = "Some request observations are unknown; whole-workload metrics cannot be evaluated."
    failure = {"modeled_oom": "oom", "simulation_limit": "timeout", "request_failure": "request_failure",
               "backend_error": "backend_error"}.get(response.failure_class,
                   "backend_error" if response.failure_class else None)
    return {
        "status": response.status, "failure_class": failure, "native_failure_class": response.failure_class,
        "evidence_level": response.evidence_level, "backend": response.backend.model_dump(),
        "catalog_version": response.catalog_version, "model_revisions": response.model_revisions,
        "input_sha256": response.input_sha256, "client_request_id": response.client_request_id,
        "effective_config": response.effective_config.model_dump() if response.effective_config else None,
        "calibration": response.calibration.model_dump(),
        "coverage": {"in_domain": response.calibration.in_domain,
                     "assumptions": response.calibration.assumptions + notes},
        "observations": rows, "metrics": metrics, "requests": counts,
        "unsupported_metrics": unsupported, "unsupported_fields": response.unsupported_fields,
        "logs": response.logs,
    }


class SimulatorHTTPClient:
    """One attempt per operation; the service owns its queue and execution state."""

    def __init__(self, base_url="http://127.0.0.1:8877", timeout_s=1830):
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("Use an HTTP(S) service root without credentials, query or fragment")
        if type(timeout_s) not in {int, float} or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("HTTP timeout must be positive and finite")
        self.timeout_s = timeout_s
        self._opener = build_opener(_NoRedirect)

    def _exchange(self, path, body=None):
        request = Request(self.base_url + path, data=body,
                          headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            try:
                response = self._opener.open(request, timeout=self.timeout_s)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read()
                status = response.status
                headers = {key.lower(): value for key, value in response.headers.items()
                           if key.lower() in {"content-type", "retry-after"}}
        except (URLError, OSError, TimeoutError) as error:
            raise SimulatorTransportError("No complete response received; remote execution state is unknown. No retry was attempted.") from error
        try:
            payload = decode_response(raw)
        except (ValueError, UnicodeError) as error:
            raise SimulatorProtocolError(str(error), raw) from error
        if status != 200:
            detail = payload.get("error")
            if not isinstance(detail, dict):
                detail = {"code": "INVALID_RESPONSE", "message": "Expected a structured simulator error"}
            raise SimulatorAPIError(status, detail, raw, headers)
        return payload, raw, headers

    def catalog(self):
        payload, _, _ = self._exchange("/v1/catalog")
        return payload

    def simulate(self, request_bytes):
        if not isinstance(request_bytes, bytes):
            raise TypeError("Pass exact UTF-8 request bytes")
        RunRequest.model_validate_json(request_bytes, strict=True)
        payload, raw, headers = self._exchange("/v1/simulate", request_bytes)
        try:
            result = adapt_response(request_bytes, payload)
        except (ValueError, TypeError) as error:
            raise SimulatorProtocolError(str(error), raw) from error
        return {"result": result, "response_bytes": raw, "headers": headers}
