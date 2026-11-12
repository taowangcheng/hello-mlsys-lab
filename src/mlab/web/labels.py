"""Wording for the page: step labels, badges, which form a step shows, and quick replies.

The text is English; the page shows it through its word list (static/locales), so every string here has a Chinese entry.
"""

STEPS = {
    "new_round": "Preparing a new round", "hypothesis": "Hypothesis needed", "falsifier": "Falsification condition needed",
    "plan_draft": "Drafting the plan", "plan_review": "Plan review needed", "submit": "Submitting simulations", "running": "Simulating",
    "retry_backend": "Simulator error", "analysis_draft": "Drafting the analysis", "decision": "Decision needed",
    "next_step": "Next step needed", "validation_retry": "Rerunning validation", "locked": "Locked",
    "points": "Hardware points needed", "points_predicting": "Adding simulator predictions", "points_approve": "Measurement approval needed",
    "measure_submit": "Submitting the measurement", "measuring": "Measurement queued", "measure_retry": "Hardware environment error",
    "measure_locked": "Measurement locked",
}

# Which form the middle column shows for a step; steps without one are the agent's turn.
FORMS = {"hypothesis": "hypothesis", "falsifier": "hypothesis", "plan_review": "review", "decision": "decision",
         "next_step": "next_step", "points": "points", "points_approve": "approve_points", "locked": "unlock", "measure_locked": "unlock"}

# Messages the student can send with one click, in the student's own voice.
QUICK_REPLIES = {
    "hypothesis": ["Tell me about this task", "What does this SLA mean?"],
    "falsifier": ["What is a falsification condition? Give an example"],
    "plan_review": ["Keep all and approve", "I want to change a candidate", "Explain what each candidate tests"],
    "decision": ["Do these results support my hypothesis?", "Why do p50 and p99 differ?"],
    "next_step": ["Another simulation round", "I want to measure on hardware", "Validate on the simulator", "Compare the simulation with the hardware"],
    "points": ["Which points are most worth measuring?", "What does measuring more than once add?"],
    "points_approve": ["Approve the measurement", "I want a different point"],
}

# What a step asks for, on the page's next-step card and in the fake agent's replies.
HINTS = {
    "hypothesis": "Write the hypothesis, an alternative explanation and the falsification condition.",
    "falsifier": "Missing: what result would show the hypothesis is wrong?",
    "plan_review": "The plan passed the check. Mark each candidate keep, change or drop, with a reason; if all are kept, approve directly.",
    "decision": "The analysis is ready. Write the decision and reasons, cite run IDs, and select a candidate.",
    "next_step": "Choose the next step: another simulation round, hardware points, validation on the simulator, or a conclusion.",
    "points": "Choose hardware points: candidate, workload, number of runs and reason.",
    "points_approve": "The simulator predictions and the budget used are in the middle column; after approval the agent submits the measurement.",
    "locked": "The same error occurred three times. Check the error record, then unlock.",
    "measure_locked": "The same measurement error occurred three times. Check the error record, then unlock.",
}

AGENT_STATUS = {"offline": "Agent offline", "connecting": "Agent connecting", "idle": "Agent idle", "running": "Agent working",
                "waiting_approval": "Approval needed", "waiting_answer": "Answer needed", "error": "Agent error"}


def badge(state, agent_status="idle"):
    """The tab badge: confirmations first, then the agent at work, then runs in flight, then the student's turn."""
    if agent_status in {"waiting_approval", "waiting_answer"}:
        return {"text": AGENT_STATUS[agent_status], "tone": "attention"}
    if agent_status == "running":
        return {"text": AGENT_STATUS["running"], "tone": "busy"}
    if agent_status == "error":
        return {"text": AGENT_STATUS["error"], "tone": "error"}
    action = state["next_actions"][0]
    step = action.get("step")
    # A step with a form needs the student even when the agent is the one to ask, as when choosing hardware points.
    tone = "attention" if action["actor"] == "student" or step in FORMS else "busy" if step in {"running", "measuring", "points_predicting"} else "info"
    return {"text": STEPS.get(step, step or ""), "tone": tone}
