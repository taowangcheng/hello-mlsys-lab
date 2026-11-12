"""Seeded draft workload generation from the task's empirical token distributions.

These are teaching workloads, not real traces or calibrated performance data.
"""
import copy
import json
import random
from .io import atomic, hashed


def generate(task, base, seed=42, variant="development"):
    rng = random.Random(seed)
    rows = copy.deepcopy(base)
    if task == "9.3":
        # Preserve model mix, fan-in and token distributions; resample each
        # model's requests and permute edges only within the same DAG depth.
        depths = {}
        for row in rows:
            depths[row["request_id"]] = 1 + max((depths[d] for d in row.get("depends_on", [])), default=-1)
        mapping = {}
        for depth in sorted(set(depths.values())):
            ids = [key for key, value in depths.items() if value == depth]
            shuffled = ids.copy()
            rng.shuffle(shuffled)
            mapping.update(zip(ids, shuffled))
        for row in rows:
            pool = [r for r in base if r.get("target_model") == row.get("target_model")]
            sample = rng.choice(pool)
            row.update(input_tokens=sample["input_tokens"], output_tokens_target=sample["output_tokens_target"])
            if "depends_on" in row:
                row["depends_on"] = [mapping[d] for d in row["depends_on"]]
    else:
        # Keep open-loop arrival times fixed: only prompt mix changes.
        for row in rows:
            pool = base
            if task == "9.2" and variant in {"short_mix", "long_mix"}:
                short = rng.random() < (.8 if variant == "short_mix" else .2)
                pool = [r for r in base if (r["input_tokens"] <= 1024) == short] or base
            sample = rng.choice(pool)
            for key in ("input_tokens", "output_tokens_target", "category", "prefix_group"):
                if key in sample:
                    row[key] = sample[key]
    return rows


def write_workload(path, rows, task, seed, variant):
    atomic(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), text=True)
    metadata = {"schema": "mlab.v1", "task": task, "seed": seed, "variant": variant,
                "generator": "empirical-draft-v1", "requests": len(rows), "sha256": hashed(path),
                "notice": "Synthetic draft teaching workload; not a measured trace."}
    atomic(path.with_suffix(".meta.json"), metadata)
    return metadata
