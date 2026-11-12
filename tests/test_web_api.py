"""The web API with the rule-following fake agent: access, forms, confirmation cards, chat and events.

The 9.2 story through the page must end where the CLI story ends: a conclusion on c16, 6 of 8 hardware runs,
mock_only. The student's log holds only the student's words.
"""
import asyncio
import json
import time
import pytest
from starlette.testclient import TestClient
from mlab.core.io import atomic, load
from mlab.core.workspace import Lab
from mlab.web.agent import TaskRuntime
from mlab.web.bus import Bus
from mlab.web.course import Course
from mlab.web.server import create_app
from test_workflow import HYPOTHESIS, decided


@pytest.fixture
def course(workspace_path):
    """A course with one session of each task: 9.1-1, 9.2-1 and 9.3-1."""
    made = Course(workspace_path)
    made.ensure("fixture")
    for task in ("9.1", "9.2", "9.3"):
        made.create(task)
    return workspace_path


def connect(root, **options):
    return TestClient(create_app(root, "fake", watch_interval=0.2, **options), base_url="http://testserver")


def post(web, path, session="9.2-1", **body):
    return web.post(f"/api/sessions/{session}/{path}", json=body).json()


def get(web, path, session="9.2-1"):
    return web.get(f"/api/sessions/{session}/{path}").json()


def idle(web, session="9.2-1", timeout=90):
    """The session's state once its agent has finished the turn and nothing is queued or waiting."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get(web, "state", session)
        agent = state["agent"]
        if agent["status"] in {"idle", "offline", "error"} and not agent["queued"] and not agent["cards"]:
            return state
        time.sleep(.1)
    raise AssertionError(f"agent still busy: {state['agent']}")


def card(web, session="9.2-1", timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cards = get(web, "state", session)["agent"]["cards"]
        if cards:
            return cards[0]
        time.sleep(.1)
    raise AssertionError("no card appeared")


def answer(web, pending, decision, reason="", session="9.2-1"):
    assert post(web, f"approvals/{pending['id']}", session, decision=decision, reason=reason)["ok"]


def student_log(root, session="9.2-1"):
    path = root / session / ".mlab/student.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_new_sessions_start_over_and_share_only_the_hardware_budget(course):
    """A new session is the task from the start, in its own workspace; the sessions of a task share its hardware budget."""
    with connect(course) as web:
        created = web.post("/api/sessions", json={"task": "9.2"}).json()
        assert created["ok"] and created["session"] == "9.2-2" and created["number"] == 2
        assert web.post("/api/sessions", json={"task": "9.9"}).json()["error"]["code"] == "E_SCHEMA"
        assert [s["id"] for s in web.get("/api/course").json()["sessions"]] == ["9.1-1", "9.2-1", "9.3-1", "9.2-2"]
        assert get(web, "state", "9.2-2")["workflow"] is None
        assert post(web, "agent/new-session", "9.2-2")["error"]["code"] == "E_SCHEMA"  # Starting over is a new session.
    lab = Lab(course / "9.2-1")
    try:
        lab.new_workflow(example=True)
        atomic(lab.folder() / "hypothesis.md", HYPOTHESIS, text=True)
        decided(lab)
        lab.measure_new()
        atomic(lab.mfolder() / "points.yaml", {"schema": "mlab.v1", "measurement": "m001", "points": [
            {"candidate": "low_concurrency", "runs": 2, "rationale": "see the noise between two runs"}]})
        lab.measure_approve(lab.measure_check()["plan_hash"])
        lab.measure_submit()
        assert lab.measure_wait(20)["status"] == "completed"
    finally:
        lab.close()
    second, other = Lab(course / "9.2-2", readonly=True), Lab(course / "9.1-1", readonly=True)
    try:
        assert second.hardware_budget() == {"used": 2, "remaining": 6, "total": 8}
        assert not second.store.all("workflow") and not second.store.all("run")
        assert other.hardware_budget()["used"] == 0
    finally:
        second.close()
        other.close()


def test_any_forwarded_address_works_and_writes_are_json(course):
    """A port forward (VS Code, dev tunnels, ssh -L) brings its own host and origin; the page works through it."""
    tunnel = {"host": "abcd1234-8765.usw2.devtunnels.ms", "origin": "https://abcd1234-8765.usw2.devtunnels.ms"}
    with connect(course) as web:
        listed = web.get("/api/course", headers={"host": tunnel["host"]})
        assert listed.status_code == 200 and [t["task"] for t in listed.json()["tasks"]] == ["9.1", "9.2", "9.3"]
        assert [s["id"] for s in listed.json()["sessions"]] == ["9.1-1", "9.2-1", "9.3-1"]
        assert "script-src 'self'" in listed.headers["content-security-policy"] and listed.headers["cache-control"] == "no-store"
        sent = web.post("/api/sessions/9.2-1/chat", json={"text": "Hello"}, headers=tunnel)
        assert sent.status_code == 200 and sent.json()["ok"]
        assert web.post("/api/sessions/9.2-1/chat", content="hi", headers={"content-type": "text/plain"}).status_code == 415
        assert web.get("/api/sessions/9.9-1/state").status_code == 404
        idle(web)


def test_one_request_brings_a_whole_task_page(course):
    with connect(course) as web:
        page = get(web, "page")
        assert page["ok"] and page["record_id"] == "overview" and page["record"]["kind"] == "overview"
        assert page["state"]["ok"] and page["overview"]["ok"] and page["notebook"]["ok"] and page["chat"] == [] and page["agent"]["status"]
        missing = get(web, "page?record=sim_0099&chat=0")
        assert missing["ok"] and not missing["record"]["ok"] and missing["chat"] is None and missing["notebook"]["id"] == "overview"


def test_the_9_2_story_through_forms_and_cards_matches_the_cli(course):
    claim = "Long prefills block decode: in a burst, short requests wait behind long ones"
    with connect(course) as web:
        done = post(web, "forms/hypothesis", claim=claim, alternative="overall overload",
                    falsifier="If only lower concurrency helps and chunking does not, it is overload")
        assert done["ok"] and done["agent"] == "sent"
        assert idle(web)["step"] == "plan_review"
        keep = [{"candidate": c, "verdict": "keep", "reason": "clear controls"} for c in ("baseline", "chunked_256", "low_concurrency")]
        assert post(web, "forms/review", entries=keep, then="confirm")["status"] == "approved"
        state = idle(web)
        assert state["step"] == "decision"
        round_one = get(web, "records/r001")
        low = next(r for r in round_one["runs"] if r["candidate"] == "low_concurrency")
        assert round_one["claims"]["valid"] and low["id"] in round_one["analysis"]
        assert post(web, "forms/decision", text=f"Based on {low['id']}, only lower concurrency meets the SLA", candidate="low_concurrency", then="confirm")["ok"]
        assert idle(web)["step"] == "next_step"
        assert post(web, "forms/next_step", choice="round", detail="look for margin near low concurrency")["workflow"] == "r002"
        idle(web)
        post(web, "forms/hypothesis", claim="Lower concurrency is steadier but loses throughput", alternative="", falsifier="Concurrency 24 also meets the SLA")
        assert idle(web)["step"] == "plan_review"
        round_two = get(web, "records/r002")
        assert [c["id"] for c in round_two["candidates"]] == ["c16", "c20", "c24"]
        assert [d["field"] for d in round_two["candidates"][1]["diff"]] == ["serving.max_concurrency"]
        sweep = [{"candidate": c, "verdict": "keep", "reason": "only concurrency changes"} for c in ("c16", "c20", "c24")]
        assert post(web, "forms/review", entries=sweep, then="save").get("agent") is None
        # The student approves through the agent: the gated command becomes a card, first refused with a reason.
        post(web, "chat", text="Approve it")
        pending = card(web)
        assert pending["summary"]["title"] == "Approve the plan" and pending["summary"]["will_fail"] is None
        answer(web, pending, "deny", "Wait, I want to look at the reason for c24 again")
        assert idle(web)["step"] == "plan_review"
        post(web, "chat", text="OK, approve")
        answer(web, card(web), "allow")
        assert idle(web)["step"] == "decision"
        c20 = next(r for r in get(web, "records/r002")["runs"] if r["candidate"] == "c20")
        assert c20["margin_pct"] == 3.0
        post(web, "forms/decision", text=f"Based on {c20['id']}, c20 has a 3% margin; pick it for now", candidate="c20", then="notify")
        pending = card(web)
        assert pending["command"] == "mlab workflow decide --candidate c20" and pending["summary"]["will_fail"] is None
        answer(web, pending, "allow")
        assert idle(web)["step"] == "next_step"
        assert post(web, "forms/next_step", choice="measure")["measurement"] == "m001"
        idle(web)
        assert get(web, "page")["record_id"] == "m001"  # The page opens where the student's next step is.
        post(web, "forms/points", points=[{"candidate": "c20", "rationale": "my pick, but the simulation is only 3% under the SLA"},
                                          {"candidate": "low_concurrency", "rationale": "a low-concurrency control"}])
        assert idle(web)["step"] == "points_approve"
        post(web, "chat", text="Approve the measurement")
        pending = card(web)
        assert ["Budget", "Uses 2; 6 left after approval (of 8)"] in pending["summary"]["facts"]
        answer(web, pending, "allow")
        # The results are in and nothing waits on them; the student asks for the comparison.
        assert idle(web)["step"] == "next_step"
        measured = get(web, "records/m001")
        assert measured["stage"] == "measured" and len(measured["flagged"]) == 2
        post(web, "chat", text="Compare the simulation with the hardware")
        idle(web)
        reply = [e for e in get(web, "chat")["entries"] if e["role"] == "agent"][-1]["text"]
        assert reply.startswith("Simulation vs. hardware:") and all(row["run_id"] in reply for row in measured["alignment"]["rows"])
        post(web, "forms/next_step", choice="measure")
        idle(web)
        post(web, "forms/points", points=[{"candidate": "c16", "runs": 2, "rationale": "final validation; see the noise between two runs"},
                                          {"candidate": "c16", "workload": "short_prompt_distribution", "rationale": "holdout: more short requests"},
                                          {"candidate": "c16", "workload": "long_prompt_distribution", "rationale": "holdout: more long requests"}])
        assert idle(web)["step"] == "points_approve"
        assert post(web, "forms/approve_points")["status"] == "approved"
        assert idle(web)["step"] == "next_step" and get(web, "records/m002")["stage"] == "measured"
        preview = web.get("/api/sessions/9.2-1/preview/conclude").json()
        assert dict(preview["facts"])["Conclusion level"] == "unresolved" and dict(preview["facts"])["Selected configuration"] == "r002/c20"
        preview = web.get("/api/sessions/9.2-1/preview/conclude?candidate=r002/c16").json()
        assert dict(preview["facts"])["Conclusion level"] == "mock_only" and dict(preview["facts"])["Selected configuration"] == "r002/c16"
        concluded = post(web, "forms/conclude", candidate="r002/c16")
        assert concluded["conclusion"] == "mock_only" and concluded["candidate"] == "c16" and concluded["selection"] == "r002/c16"
        state = idle(web)
        assert state["budget"]["hardware"] == {"used": 6, "remaining": 2, "total": 8} and "calibration" not in state
        assert state["conclusion"] == "mock_only" and state["step"] == "next_step"
        c16 = get(web, "records/r002/c16")
        assert c16["selected"] and c16["missing"]["hardware"] == [] and c16["conclusions"] == ["001"]
        report = get(web, "records/report")
        assert report["written"] and report["conclusion"] == "mock_only"
        chat = get(web, "chat")["entries"]
        assert {e.get("state") for e in chat if e["role"] == "card"} == {"approved", "denied"}
    words = student_log(course)
    assert all("[Page " not in entry["text"] for entry in words)
    sources = [entry["source"] for entry in words]
    assert (sources.count("web-chat"), sources.count("web-card"), sources.count("web-form")) == (4, 1, 11)
    assert next(e for e in words if e["source"] == "web-card")["text"] == "Wait, I want to look at the reason for c24 again"
    assert f"## Hypothesis\n\n{claim}\n" in (course / "9.2-1/rounds/r001/hypothesis.md").read_text(encoding="utf-8")
    assert "calibration" not in load(course / "9.2-1/.mlab/config.json")


def test_blocked_commands_busy_forms_and_chat_answers(course):
    with connect(course) as web:
        for command in ("mlab --workspace . conclude", "bash -c 'mlab conclude'", "cd . && mlab measure unlock", "ls"):
            post(web, "chat", text=f"Run: {command}")
            idle(web)
        notices = [e for e in get(web, "chat")["entries"] if e["role"] == "notice" and e.get("to") == "student"]
        assert len(notices) == 4 and all(e["text"].startswith("Blocked agent") for e in notices)
        assert "standard form" in notices[0]["reason"] and "one mlab command at a time" in notices[3]["reason"]
        assert not get(web, "state")["agent"]["cards"]
        # A waiting card makes the agent busy: forms wait, or stop it first.
        post(web, "chat", text="Run: mlab conclude")
        pending = card(web)
        assert pending["summary"]["title"] == "Submit the conclusion" and pending["summary"]["will_fail"] == "No configuration is selected"
        refused = post(web, "forms/hypothesis", claim="x", falsifier="y")
        assert refused["error"]["code"] == "E_AGENT_BUSY"
        post(web, "chat", text="Not yet")
        idle(web)
        entry = next(e for e in get(web, "chat")["entries"] if e.get("card_id") == pending["id"])
        assert entry["state"] == "denied"
        post(web, "chat", text="Run: mlab conclude")
        card(web)
        assert post(web, "forms/hypothesis", claim="Overload", falsifier="Lowering concurrency does not help", interrupt=True)["ok"]
        idle(web)
        assert get(web, "records/overview")["conclusion"] is None
    sources = [entry["source"] for entry in student_log(course)]
    assert "web-card" not in sources and sources.count("web-chat") == 7


class Scripted:
    """A client whose turns wait for the test, to check queueing, interruption and question cards."""

    def __init__(self, runtime):
        self.runtime, self.prompts, self.release = runtime, [], asyncio.Event()

    async def connect(self, session):
        return session or "scripted"

    async def disconnect(self):
        pass

    async def interrupt(self):
        self.release.set()

    async def turn(self, prompt):
        self.prompts.append(prompt)
        if "quiz" in prompt:
            decision = await self.runtime.permission("AskUserQuestion", {"questions": [
                {"question": "Which one first?", "header": "Pick", "options": [{"label": "c16", "description": ""}, {"label": "c20", "description": ""}],
                 "multiSelect": False}]})
            yield {"type": "text", "text": json.dumps(decision.get("updated_input", {}).get("answers"), ensure_ascii=False)}
        else:
            await self.release.wait()
            self.release.clear()
            yield {"type": "text", "text": "OK"}
        yield {"type": "result", "usage": {"input_tokens": 10, "output_tokens": 5}, "cost_usd": 0.01, "cost_estimated": True}


def test_queue_merge_interrupt_questions_and_usage(course):
    async def scenario():
        clients = []
        def factory(runtime):
            clients.append(Scripted(runtime))
            return clients[-1]
        lab = Lab(course / "9.2-1")
        lab.new_workflow()  # A record to cite.
        lab.close()
        runtime = TaskRuntime("9.2-1", course / "9.2-1", Bus(), factory)
        await runtime.say("first")
        while not clients or not clients[0].prompts:
            await asyncio.sleep(.01)
        assert await runtime.say("second") == "queued" and await runtime.say("third @r001", ["r001"]) == "queued"
        clients[0].release.set()
        while len(clients[0].prompts) < 2:
            await asyncio.sleep(.01)
        merged = clients[0].prompts[1]
        assert merged.startswith("second\n\nthird @r001") and "[Page note] Records cited" in merged and "- r001 (round 1" in merged and "The 2 messages above" in merged
        await runtime.interrupt()
        assert await runtime.idle.wait() and runtime.status == "idle"
        await runtime.send("Give me a quiz")
        while not runtime.cards:
            await asyncio.sleep(.01)
        pending = next(iter(runtime.cards.values()))
        assert pending["kind"] == "question" and runtime.status == "waiting_answer"
        runtime.resolve(pending["id"], {"answers": {"Which one first?": "c20"}})
        while runtime.turning or runtime.inbox:
            await asyncio.sleep(.01)
        assert runtime.chat.read()[-1]["text"] == '{"Which one first?": "c20"}'
        usage = runtime.snapshot()["usage"]
        assert usage["turns"] == 3 and usage["input_tokens"] == 30 and usage["cost_estimated"]
        await runtime.stop()
    asyncio.run(scenario())
    words = student_log(course)
    assert [e["source"] for e in words] == ["web-chat", "web-chat", "web-chat", "web-question"]
    assert words[2]["refs"] == ["r001"] and words[3]["text"] == "Which one first?: c20"


def test_events_are_polled_from_the_last_number_or_ask_for_a_reload(course):
    """Short requests instead of a stream: they pass through any port forward."""
    bus = Bus(size=3)
    for index in range(5):
        bus.publish("records.changed", "9.2-1", seq=index)
    assert [event["id"] for event in bus.since(3)] == [4, 5] and bus.since(5) == [] and bus.since(0) is None
    with connect(course) as web:
        start = web.get("/api/events").json()
        assert start["ok"] and start["events"] == []
        web.app.state.bus.publish("notify", "9.2-1", text="Measurement finished (m001).")
        got = web.get(f"/api/events?last={start['last']}").json()
        notes = [(event["type"], event["session"], event["data"]["text"]) for event in got["events"] if event["type"] == "notify"]
        assert notes == [("notify", "9.2-1", "Measurement finished (m001).")] and got["last"] > start["last"]
        assert [event for event in web.get(f"/api/events?last={got['last']}").json()["events"] if event["type"] == "notify"] == []
        for index in range(4100):
            web.app.state.bus.publish("chat.delta", "9.2-1", text="x")
        behind = web.get(f"/api/events?last={got['last']}").json()
        assert behind["resync"] and behind["last"] == web.app.state.bus.last
