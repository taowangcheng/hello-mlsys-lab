"""Data integrity and boundary regressions found during the three-pass audit."""
import asyncio
import json

import pytest
from starlette.testclient import TestClient

from mlab.core.io import LabError
from mlab.core.workspace import Lab
from mlab.web import exploration, reading, views
from mlab.web.codex_client import LabTools
from mlab.web.server import create_app
from test_codex import make_runtime
from test_exploration import draft, setup


def test_messages_preserve_long_text_and_reject_invalid_input_before_logging(workspace_path):
    _, root = setup(workspace_path)
    message = "Explain the following observation. " + "sample " * 1000 + "END_OF_QUESTION"
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        endpoint = f"/api/workbench/sessions/{root.name}/chat"
        assert web.post(endpoint, json={"text": message}).json()["ok"]
        entries = web.get(f"/api/sessions/{root.name}/chat").json()["entries"]
        assert [item["text"] for item in entries if item["role"] == "student"] == [message]
        for text in ("x" * 32001, {"question": "not a string"}, ["not a string"], 42):
            assert not web.post(endpoint, json={"text": text}).json()["ok"]
        entries = web.get(f"/api/sessions/{root.name}/chat").json()["entries"]
        assert [item["text"] for item in entries if item["role"] == "student"] == [message]


def test_combined_evidence_context_has_one_budget_and_preserves_readable_sources(workspace_path, monkeypatch):
    course, root = setup(workspace_path)
    experiments = [draft(course, root) for _ in range(12)]
    original = views.evidence_record
    def large(lab, identifier):
        return {**original(lab, identifier), "details": {f"field_{i}": "evidence " * 320 for i in range(15)}}
    monkeypatch.setattr(views, "evidence_record", large)
    refs, context = exploration.conversation_context(root, "Compare these records", [item["key"] for item in experiments])
    assert len(context) <= 60000
    data = json.loads(context.split("student instructions):\n", 1)[1])
    assert [item["reference"] for item in data["evidence"]] == refs
    for evidence in data["evidence"]:
        record = evidence["record"]
        source = record.get("_reading", {}).get("source") or record["next"]
        full = json.loads((root / source["file_path"]).read_text())
        assert len(full["details"]) == 15 and full["id"] == evidence["reference"].split("/")[1]


def test_evidence_snapshots_are_checked_on_creation_and_the_first_read(workspace_path):
    _, root = setup(workspace_path)
    source = reading.snapshot(root, '{"verdict": "original"}')
    (root / source).write_text('{"verdict": "changed"}')
    with pytest.raises(LabError, match="snapshot"):
        reading.snapshot(root, '{"verdict": "original"}')

    async def scenario():
        output, failed = await LabTools(make_runtime(root)).execute("Read", {"file_path": source})
        assert failed and "snapshot" in output
    asyncio.run(scenario())


def test_read_rejects_oversized_pointer_metadata(workspace_path):
    key = "k" * 70000
    with pytest.raises(LabError, match="pointer"):
        reading.select({key: "content"}, "/" + key)


def test_invalid_chat_pagination_is_a_structured_error(workspace_path):
    _, root = setup(workspace_path)
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        response = web.get(f"/api/sessions/{root.name}/chat?limit=invalid")
        assert response.status_code < 500 and not response.json()["ok"]


@pytest.mark.parametrize("text", ["student@report.example", "https://example.test/@overview", r"Write \@report literally", "user@r999.com"])
def test_literal_mentions_and_email_addresses_do_not_attach_evidence(workspace_path, text):
    _, root = setup(workspace_path)
    refs, _ = exploration.conversation_context(root, text, [])
    assert refs == []


@pytest.mark.parametrize("field,limit", [("title", 160), ("question", 2000), ("expectation", 4000)])
def test_oversized_plan_fields_never_create_or_replace_records(workspace_path, field, limit):
    course, root = setup(workspace_path)
    first = draft(course, root)
    with pytest.raises(LabError, match="characters"):
        draft(course, root, **{field: "x" * (limit + 1)})
    with pytest.raises(LabError, match="characters"):
        exploration.action(root, "save", {**first, "workflow": first["id"], field: "x" * (limit + 1)})
    current = exploration.session_view(root)["experiments"]
    assert len(current) == 1 and current[0]["revision"] == first["revision"]


def test_oversized_notes_and_measurement_names_do_not_leave_partial_records(workspace_path):
    course, root = setup(workspace_path)
    first = draft(course, root)
    with pytest.raises(LabError, match="characters"):
        exploration.action(root, "note", {"text": "x" * 16001})
    with pytest.raises(LabError, match="references"):
        exploration.action(root, "note", {"text": "Keep all citations", "refs": [first["key"]] * 31})
    with pytest.raises(LabError, match="characters"):
        exploration.action(root, "prepare_measurement", {"title": "x" * 161, "workflow": first["id"],
            "confirm_config": True, "revision": first["revision"], "points": [{"workflow": first["id"],
            "candidate": first["candidates"][0]["id"], "workload": "development", "runs": 1, "rationale": "Check"}]})
    lab = Lab(root)
    try:
        assert not lab.store.all("note") and not lab.store.all("measurement") and not lab.store.all("run")
        assert lab.store.get("workflow", first["id"])["status"] == "draft"
    finally:
        lab.close()


def test_malformed_event_cursors_and_tool_actions_are_rejected(workspace_path):
    _, root = setup(workspace_path)
    with TestClient(create_app(workspace_path, "fake", opening=False)) as web:
        for cursor in (chr(0x00B2), "9" * 5000, "-1"):
            assert web.get("/api/events", params={"last": cursor}).status_code < 500
        reset = web.get("/api/events", params={"last": "99999999"}).json()
        assert reset["resync"] and reset["last"] < 99999999

    async def scenario():
        runtime = make_runtime(root)
        runtime.exploration = True
        output, failed = await LabTools(runtime).execute("Explore", {"action": "unknown_action"})
        assert failed and "action" in output
    asyncio.run(scenario())


def test_explore_surfaces_record_errors_as_failed_tools(workspace_path, monkeypatch):
    from mlab.web import server
    _, root = setup(workspace_path)
    monkeypatch.setattr(server, "read", lambda *args: {"ok": False, "error": {"message": "Evidence changed"}})

    async def scenario():
        runtime = make_runtime(root)
        runtime.exploration = True
        output, failed = await LabTools(runtime).execute("Explore", {"action": "show", "reference": f"{root.name}/overview"})
        assert failed and "Evidence changed" in output
    asyncio.run(scenario())
