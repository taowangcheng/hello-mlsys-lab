"""Commands that need the student: recognise them in any form, and summarise what approving one would do."""
import re
import shlex
from ..core.io import LabError, load
from ..core.metrics import get_metric
from ..core.workspace import RUN_ID, comments, hypothesis_parts, margin

GATED = {("workflow", "approve"): "workflow_approve", ("workflow", "decide"): "workflow_decide", ("workflow", "unlock"): "workflow_unlock",
         ("measure", "approve"): "measure_approve", ("measure", "unlock"): "measure_unlock", ("conclude", None): "conclude"}
TITLES = {"workflow_approve": "Approve the plan", "workflow_decide": "Confirm the decision", "workflow_unlock": "Unlock the round",
          "measure_approve": "Approve the measurement", "measure_unlock": "Unlock the measurement", "conclude": "Submit the conclusion"}
# Anywhere in the text, so that quoting (bash -c '…'), backticks or eval cannot hide a gated command from the token check.
PATTERNS = {kind: re.compile(r"\bmlab\b.*?\b" + group + (r"\s+" + action if action else "") + r"\b", re.S) for (group, action), kind in GATED.items()}
OPERATORS = {"&&", "||", ";", "|", "&", ">", ">>", "<", "(", ")", ";;", "|&"}
STANDARD = "Run a command that needs approval on its own, in the standard form, e.g. mlab workflow approve --plan HASH."


def tokens_of(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def classify(command):
    """{"kind": gated kind or None, "plain": run alone in the standard form, "args": its flags, "reason": why a gated form is refused}."""
    tokens = tokens_of(command) or []
    starts = [i for i, token in enumerate(tokens) if token == "mlab" or token.endswith("/mlab") or
              (token == "-m" and i + 1 < len(tokens) and tokens[i + 1] == "mlab")]
    found = None
    for start in starts:
        words = tokens[start + (2 if tokens[start] == "-m" else 1):]
        index = 0
        while index < len(words) and words[index].startswith("--"):  # global flags such as --workspace PATH
            index += 2 if words[index] == "--workspace" else 1
        words = words[index:]
        for (group, action), kind in GATED.items():
            if words[:1] == [group] and (action is None or words[1:2] == [action]):
                found = found or kind
    hidden = next((kind for kind, pattern in PATTERNS.items() if pattern.search(command)), None)
    kind = found or hidden
    if not kind:
        return {"kind": None, "plain": True, "args": {}, "reason": None}
    group, action = next(key for key, value in GATED.items() if value == kind)
    size = 2 if action else 1
    plain = (found == kind and tokens[:1] == ["mlab"] and len(starts) == 1 and not OPERATORS & set(tokens)
             and "$(" not in command and "`" not in command and tokens[1:1 + size] == [group, action][:size])
    args = {}
    if plain:
        flag = None
        for token in tokens[1 + size:]:
            if token.startswith("--"):
                flag = token[2:]
                args[flag] = True
            elif flag:
                args[flag], flag = token, None
            else:
                args["id"] = token
    return {"kind": kind, "plain": plain, "args": args, "reason": None if plain else STANDARD}


def short(text, limit=160):
    """A record's text on one line for a card, without its Markdown headings."""
    body = "\n".join(line for line in (text or "").splitlines() if not line.lstrip().startswith("#"))
    line = " ".join(body.split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def hash_mismatch(prefix, digest):
    prefix = str(prefix or "")
    return len(prefix.removeprefix("sha256:")) < 8 or not digest.startswith(prefix if prefix.startswith("sha256:") else "sha256:" + prefix)


def summarize(lab, kind, args):
    """What the student is asked to approve, from a read-only Lab: facts, effects, and the check that would refuse it."""
    facts, effects, warnings, will_fail, record = [], [], [], None, None
    try:
        if kind == "workflow_approve":
            w = lab.workflow()
            record = w["id"]
            folder = lab.folder(w)
            review = comments(folder.joinpath("review.md").read_text(encoding="utf-8")) if (folder / "review.md").exists() else ""
            claim, falsifier = hypothesis_parts(folder.joinpath("hypothesis.md").read_text(encoding="utf-8"))
            plan, candidates, digest = lab.specifications()
            if hash_mismatch(args.get("plan"), digest):
                will_fail = "The plan hash does not match the current plan; the agent must check the plan again"
            elif not review:
                will_fail = "review.md has no plan comments yet"
            facts = [["Research question", plan["question"]], ["Hypothesis", short(claim, 120)], ["Falsification condition", short(falsifier, 120)],
                     ["Candidates", ", ".join(c["id"] for c in candidates)],
                     ["Simulation runs", f"{len(candidates) * len(plan['seeds'])} (seed {', '.join(map(str, plan['seeds']))})"],
                     ["Plan comments", short(review) or "None"]]
            effects = ["The hypothesis, plan, candidates and comments are frozen",
                       "The agent then submits the simulations; no hardware budget is used"]
        elif kind == "measure_approve":
            m = lab.measurement()
            record = m["id"]
            _, points, digest = lab.measure_points(m)
            if hash_mismatch(args.get("plan"), digest) or m.get("checked_hash") != digest:
                will_fail = "The hardware points changed; run mlab measure check first"
            total = sum(p["runs"] for p in points)
            budget = lab.hardware_budget()
            lines = []
            for point in points:
                _, simulated = lab.point_specs(point)
                run = lab.sim_prediction(simulated)
                predicted = "no simulator prediction yet"
                if run:
                    result, _ = lab.result(run["id"])
                    value = get_metric(result["metrics"], lab.task["key_metrics"][0])
                    predicted = (f"simulated {value:.1f} (margin {margin(result, lab.task['key_metrics'][0])}%)"
                                 if type(value) in {int, float} else "no simulated value")
                else:
                    will_fail = will_fail or "Some points have no simulator prediction"
                lines.append(f"{point['candidate']} · {point['workload']} · {point['runs']}×: {predicted}")
            facts = [["Hardware points", lines], ["Budget", f"Uses {total}; {budget['remaining'] - total} left after approval (of {budget['total']})"]]
            if total > budget["remaining"]:
                will_fail = will_fail or "Not enough hardware budget"
            effects = ["The simulator's predictions are frozen for comparison with the hardware", f"Uses {total} runs of the hardware budget"]
        elif kind == "workflow_decide":
            w = lab.workflow()
            record = w["id"]
            text = (lab.folder(w) / "decision.md").read_text(encoding="utf-8").strip()
            cited = sorted(set(RUN_ID.findall(text)) & set(w["runs"]))
            candidate = args.get("candidate")
            facts = [["Decision", short(text) or "Not written"], ["Cited runs", ", ".join(cited) or "None"], ["Selected candidate", candidate or "Unchanged"]]
            if w["status"] == "decided":
                will_fail = f"The decision of {w['id']} is already confirmed and cannot change; pick another configuration when submitting the conclusion"
            elif not text:
                will_fail = "decision.md is empty"
            elif w["runs"] and not cited:
                will_fail = "The decision cites no run of this round"
            elif candidate and candidate not in {c["id"] for c in load(lab.folder(w) / "frozen_plan.json")["candidates"]}:
                will_fail = f"{candidate} is not an approved candidate of this round"
            effects = ["The decision and the analysis are frozen"]
        elif kind == "conclude":
            reference = args.get("candidate")
            selection = lab.selection_of(reference) if isinstance(reference, str) else lab.current()["selection"]
            if selection:
                conclusion, reason = lab.conclusion_for(selection, lab.store.all("run"))
                record = f"{selection['workflow']}/{selection['candidate']}"
                facts = [["Selected configuration", record], ["Conclusion level", conclusion], ["Basis", reason]]
            else:
                will_fail = "No configuration is selected"
            current = lab.measurement(required=False)
            if current and lab.measure_stage(current) in {"measuring", "measure_retry", "measure_approved"}:
                will_fail = will_fail or f"Measurement {current['id']} has not finished"
            effects = ["The conclusion, its basis and the evidence runs are recorded, and the report is updated",
                       "Exploration can continue; a later submission replaces this one"]
        elif kind in {"workflow_unlock", "measure_unlock"}:
            target = lab.workflow() if kind == "workflow_unlock" else lab.measurement()
            record = target["id"]
            facts = [["Errors", ", ".join(f"{code}×{count}" for code, count in target["errors"].items()) or "None"]]
            effects = ["The repeated-error count is reset so the agent can continue"]
    except LabError as error:
        will_fail = will_fail or str(error)
    except (OSError, KeyError, IndexError, ValueError) as error:
        warnings.append(f"Summary incomplete: {type(error).__name__}")
    return {"kind": kind, "title": TITLES[kind], "facts": facts, "effects": effects, "warnings": warnings,
            "will_fail": will_fail, "record": record}
