"""Reject active mechanisms the selected backend does not implement."""
from ..core.io import require


def active_mechanisms(strategy, mode=None):
    result = set()
    serving = strategy["serving"]
    if serving["continuous_batching"]:
        result.add("continuous_batching")
    if serving["chunked_prefill_tokens"] is not None:
        result.add("chunked_prefill")
    if serving["kv_fraction"] is not None or serving["kv_budget_gib"] is not None:
        result.add("kv_budget")
    if any(d["replicas"] > 1 for d in strategy["deployments"]):
        result.add("multi_replica")
    if any(d["parallel"]["kind"] == "tp" for d in strategy["deployments"]):
        result.add("tensor_parallel")
    if any(d["parallel"]["kind"] == "pp" for d in strategy["deployments"]):
        result.add("pipeline_parallel")
    if strategy["scheduling"].get("priority", "fifo") != "fifo":
        result.add("priority_scheduling")
    if strategy["scheduling"].get("preemption", "none") != "none":
        result.add("preemption")
    if strategy["cache"].get("prefix_cache"):
        result.add("prefix_cache")
    if strategy["speculative"]:
        result.add("speculative_decoding")
    if mode == "dag":
        result.add("dag_arrival")
    return result


def check_capabilities(capabilities, strategy, mode=None):
    unsupported = active_mechanisms(strategy, mode) - set(capabilities.mechanisms)
    require(not unsupported, "E_MECHANISM_UNSUPPORTED",
            capabilities.backend + " does not support: " + ", ".join(sorted(unsupported)))
