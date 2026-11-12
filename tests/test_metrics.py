import pytest
from mlab.core.metrics import calculate, percentile, verdict
from mlab.contracts import RequestObservation, Candidate
from pydantic import ValidationError


def test_hand_computed_metrics_and_partial_failure():
    rows = [dict(request_id="a", ready_ms=0, first_token_ms=10, finished_ms=30, output_tokens=3, output_tokens_target=3, status="completed", service_ms=30, depends_on=[], gap_after_deps_ms=0),
            dict(request_id="b", ready_ms=0, first_token_ms=None, finished_ms=50, output_tokens=0, output_tokens_target=3, status="rejected", service_ms=50, depends_on=[], gap_after_deps_ms=0)]
    evaluation = {"duration_s": 1, "goodput_policy": "ttft_only_if_tpot_tbd", "constraints": [{"metric": "ttft_ms.p99", "value": 20, "status": "draft"}]}
    result, counts, notes = calculate(rows, evaluation)
    assert result["completion_rate"] == .5
    assert result["throughput_tok_s"] == result["goodput_tok_s"] == 3
    assert result["tpot_ms"]["p50"] == 10
    assert result["makespan_s"] is None
    assert counts == {"total": 2, "completed": 1, "failed": 1}
    assert notes
    assert percentile([0, 10], .95) == 9.5
    evaluation.pop("goodput_policy")
    assert calculate(rows, evaluation)[0]["goodput_tok_s"] is None


def test_verdict_priority():
    task = {"constraints": [{"metric": "completion_rate", "op": ">=", "value": 1, "status": "draft"}]}
    assert verdict({}, task, {}, "failed", "oom")[0] == "constraints_violated"
    assert verdict({}, task, {}, "failed", "backend_error")[0] == "not_evaluated"
    assert verdict({"completion_rate": 0}, task, {"passes_floor": None}, "succeeded", None)[0] == "constraints_violated"
    assert verdict({"completion_rate": 1}, task, {"passes_floor": None}, "succeeded", None)[0] == "not_evaluated"


def test_strict_contract_and_truncation():
    with pytest.raises(ValidationError):
        RequestObservation(request_id="a", ready_ms=0, first_token_ms=1, finished_ms=2, output_tokens=1, output_tokens_target=3, status="completed", service_ms=2)
    with pytest.raises(ValidationError):
        Candidate.model_validate({"id": "a", "infra": "x", "rationale": "x", "strategy": {"deployments": [], "unknown": True}})
