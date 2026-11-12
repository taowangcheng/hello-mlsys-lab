"""Check the LLM mock estimator's teaching directions, not physical accuracy."""
import argparse
import copy
import json
import os
from pathlib import Path
import statistics
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from qwen_demo import load_settings, read_key, make_endpoint, connect_claude
from mlab.core.workspace import Lab, initialize
from mlab.core.io import atomic, hashed
from mlab.core.metrics import percentile
from mlab.backends.llm import estimate
from mlab.backends.local import fixture, load_requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/qwen.local.json")
    parser.add_argument("--model")
    parser.add_argument("--login", action="store_true", help="Use Claude Code account login instead of an API config")
    parser.add_argument("--fixture", action="store_true", help="Offline estimator harness check, NOT real-model validation")
    args = parser.parse_args()
    workspace = ROOT / "workspaces" / ("direction-check-" + uuid.uuid4().hex[:10])
    initialize(workspace, "9.2", "llm")
    lab = Lab(workspace)
    previous = {name: os.environ.get(name) for name in ("MLAB_LLM_BASE_URL", "MLAB_LLM_MODEL", "MLAB_LLM_API_KEY", "MLAB_LLM_PROTOCOL", "MLAB_BACKEND_IDENTITY", "MLAB_LLM_TIMEOUT_S")}
    server = None
    try:
        lab.new_workflow(example=True)
        atomic(lab.folder() / "hypothesis.md", "## Hypothesis\n\nScripted direction check; not a real student or a validation of system performance.\n\n"
               "## What result would prove me wrong\n\nAny of the direction checks below fails.\n", text=True)
        _, candidates, _ = lab.specifications()
        base = lab.make_spec(candidates[0], 42, 0, "development")
        if args.login:
            model = args.model or "haiku"
            for name in list(os.environ):
                if name.startswith("ANTHROPIC_") or name.startswith("MLAB_LLM_"):
                    os.environ.pop(name)
            os.environ["MLAB_LLM_MODEL"] = model
        elif not args.fixture:
            config, url, model, key_env = load_settings(args.config)
            endpoint = make_endpoint(config, url, args.model or model, read_key(config, key_env))
            server, url, key = connect_claude(endpoint)
            os.environ.update(MLAB_LLM_BASE_URL=url, MLAB_LLM_MODEL=endpoint.model,
                              MLAB_LLM_API_KEY=key, MLAB_LLM_PROTOCOL="anthropic",
                              MLAB_BACKEND_IDENTITY=hashed({"upstream": endpoint.url, "model": endpoint.model}),
                              MLAB_LLM_TIMEOUT_S=str(endpoint.timeout + 15))
        else:
            model = "offline-fixture"
        def run(spec):
            requests = load_requests(lab.root, spec)
            values = fixture(spec, requests, 4.3) if args.fixture else estimate(spec, requests, lab.root / ".mlab/cache")[0]
            return values
        baseline = run(base)
        chunked = copy.deepcopy(base)
        chunked["strategy"]["serving"]["chunked_prefill_tokens"] = 256
        low = copy.deepcopy(base)
        low["strategy"]["serving"]["max_concurrency"] = 8
        bf16, int4 = copy.deepcopy(base), copy.deepcopy(base)
        bf16["strategy"]["deployments"][0]["weight_precision"] = "BF16"
        int4["strategy"]["deployments"][0]["weight_precision"] = "INT4"
        chunked_result, low_result, bf16_result, int4_result = map(run, (chunked, low, bf16, int4))
        short_ids = {r["request_id"] for r in load_requests(lab.root, base) if r.get("category") == "short"}
        def p99(data, short=False):
            return percentile([r["ttft_ms"] for r in data["requests"] if r["status"] == "completed" and (not short or r["request_id"] in short_ids)], .99)
        tests = [
            {"name": "chunked_prefill_short_tail", "baseline": p99(baseline, True), "changed": p99(chunked_result, True), "relation": "changed < baseline"},
            {"name": "high_concurrency_tail", "baseline": p99(baseline), "changed": p99(low_result), "relation": "changed < baseline"},
            {"name": "int4_decode_overhead", "baseline": statistics.median(r["tpot_ms"] for r in bf16_result["requests"]),
             "changed": statistics.median(r["tpot_ms"] for r in int4_result["requests"]), "relation": "changed > baseline"}]
        for item in tests:
            item["passed"] = (item["changed"] < item["baseline"]) if "<" in item["relation"] else (item["changed"] > item["baseline"])
        result = {"schema": "mlab.v1", "kind": "teaching_direction_check", "model": args.model or model,
                  "source": "fixture" if args.fixture else "account_login" if args.login else "model_api", "evidence_level": "mock", "tests": tests,
                  "passed": all(t["passed"] for t in tests), "note": "Single sample per variant; does not establish real performance or robust model reliability."}
        atomic(workspace / "direction-check.json", result)
        atomic(workspace / "observations.json", {"baseline": baseline, "chunked": chunked_result, "low_concurrency": low_result, "bf16": bf16_result, "int4": int4_result})
        print(json.dumps({**result, "result_path": str(workspace / "direction-check.json")}, ensure_ascii=False))
        return 0 if result["passed"] else 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        lab.close()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
