"""Check explicit numeric claims against immutable run artifacts."""
import math
from .io import load
from .metrics import get_metric


def check_claims(lab, workflow=None):
    workflow = workflow or lab.workflow()
    path = lab.folder(workflow) / "analysis.claims.json"
    if not path.exists():
        return {"available": False, "valid": False, "errors": ["analysis.claims.json missing"]}
    claims = load(path)
    errors = []
    if not isinstance(claims, list) or not claims:
        return {"available": True, "valid": False, "errors": ["claims must be a nonempty array"]}
    current = set(workflow["runs"])
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict) or set(claim) != {"run_id", "metric", "value"} or claim["run_id"] not in current:
            errors.append(f"claim {index}: invalid fields or run outside this iteration")
            continue
        result, _ = lab.result(claim["run_id"])
        actual = get_metric(result["metrics"], claim["metric"])
        value = claim["value"]
        if type(value) not in {int, float} or type(actual) not in {int, float} or not math.isfinite(value) or not math.isclose(actual, value, rel_tol=1e-6, abs_tol=1e-6):
            errors.append(f"claim {index}: {claim['run_id']}/{claim['metric']} does not match verified metric")
    return {"available": True, "valid": not errors, "checked": len(claims), "errors": errors,
            "scope": "Verifies declared claims; does not prove every free-text assertion is correct."}
