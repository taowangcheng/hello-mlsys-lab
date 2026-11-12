"""Follow the actual assistant tools past every preview and output boundary."""
import asyncio
import json

import pytest

from mlab.core.workspace import Lab
from mlab.web import exploration, reading, views
from mlab.web.codex_client import LabTools
from test_codex import make_runtime
from test_exploration import draft, setup, simulate


async def collect(tools, arguments):
    parts, pages = [], []
    for _ in range(200):
        output, failed = await tools.execute("Read", arguments)
        assert not failed, output
        assert len(output) <= reading.MAX_OUTPUT
        page = json.loads(output)
        assert page["offset"] == arguments.get("offset", 0)
        assert page["end"] - page["offset"] == len(page["text"])
        parts.append(page["text"])
        pages.append(page)
        if page["complete"]:
            assert page["next"] is None
            return "".join(parts), pages
        assert page["next"]["offset"] == page["end"] > page["offset"]
        arguments = page["next"]
    pytest.fail("The continuation did not terminate")


def test_attached_run_logs_and_explore_show_have_readable_tails(workspace_path, monkeypatch):
    from mlab.backends.local import LocalBackend
    monkeypatch.setattr(LocalBackend, "submit", lambda *args, **kwargs: None)
    course, root = setup(workspace_path)
    experiment = draft(course, root)
    run_id = simulate(root, experiment)["runs"][0]
    lab = Lab(root)
    try:
        path = root / lab.store.get("run", run_id)["folder"] / "logs.txt"
        original = ("request timing " + chr(0x5b9e) + "\n") * 6000 + "FINAL_LOG_OBSERVATION"
        path.write_text(original, encoding="utf-8")
        assert "FINAL_LOG_OBSERVATION" not in views.run_view(lab, run_id).get("logs", "")
    finally:
        lab.close()
    # Inline mentions adjacent to non-ASCII prose must resolve and deduplicate.
    ref = f"{root.name}/{run_id}"
    refs, prompt = exploration.conversation_context(root, f"Explain @{run_id}" + chr(0x7684), [ref])
    assert refs == [ref]
    record = json.loads(prompt.split("student instructions):\n", 1)[1])["evidence"][0]["record"]
    assert len(record["logs"]) == 3000 and not record["_reading"]["complete"]
    continuation = next(item for item in record["_reading"]["omitted"] if item["path"] == "/logs")

    async def scenario():
        runtime = make_runtime(root)
        runtime.exploration = True
        tools = LabTools(runtime)
        tail, pages = await collect(tools, continuation["next"])
        assert record["logs"] + tail == original
        assert len(pages) > 5 and "FINAL_LOG_OBSERVATION" in pages[-1]["text"]
        # A subsequent update cannot change the snapshot attached to an earlier message.
        path.write_text(original + "\nNEW_LOG_OBSERVATION", encoding="utf-8")
        old_tail, _ = await collect(tools, continuation["next"])
        assert old_tail == tail
        output, failed = await tools.execute("Explore", {"action": "show", "reference": ref})
        assert not failed
        shown = json.loads(output)
        latest = next(item for item in shown["_reading"]["omitted"] if item["path"] == "/logs")
        new_tail, _ = await collect(tools, latest["next"])
        assert shown["logs"] + new_tail == original + "\nNEW_LOG_OBSERVATION"
        assert shown["files"]["logs.txt"].endswith("logs.txt")

    asyncio.run(scenario())


def test_list_and_nested_text_previews_recover_all_items_through_read(workspace_path):
    _, root = setup(workspace_path)
    rows = [{"id": number, "note": "detail " * (900 if number == 0 or number >= 30 else 50) + str(number)} for number in range(73)]
    value = {"rows/~archive": rows, "tail": "END_OF_RECORD"}
    preview = reading.preview(root, value)
    assert len(preview["rows/~archive"]) == 30
    missing = next(item for item in preview["_reading"]["omitted"] if item["kind"] == "list")
    assert missing["path"] == "/rows~1~0archive" and missing["total_items"] == 73

    async def scenario():
        tools = LabTools(make_runtime(root))
        restored, pages = await collect(tools, missing["next"])
        assert json.loads(restored) == rows and len(pages) > 5
        source = preview["_reading"]["source"]["file_path"]
        last, _ = await collect(tools, {"file_path": source, "json_pointer": "/rows~1~0archive/72/note"})
        assert last == rows[-1]["note"]
        nested = next(item for item in preview["_reading"]["omitted"] if item["path"] == "/rows~1~0archive/0/note")
        rest, _ = await collect(tools, nested["next"])
        assert preview["rows/~archive"][0]["note"] + rest == rows[0]["note"]

    asyncio.run(scenario())


def test_wide_tool_outputs_stay_valid_and_can_be_reassembled(workspace_path):
    _, root = setup(workspace_path)
    value = {f"field_{index}": "quoted \"value\"\n" * 250 for index in range(70)}
    first = reading.preview(root, value)
    assert not first["complete"] and len(reading.encoded(first)) <= reading.MAX_OUTPUT

    async def scenario():
        tools = LabTools(make_runtime(root))
        rest, _ = await collect(tools, first["next"])
        assert json.loads(first["text"] + rest) == value

    asyncio.run(scenario())


def test_read_pages_validate_offsets_versions_and_workspace_boundaries(workspace_path):
    _, root = setup(workspace_path)
    path = root / "analysis/long.txt"
    path.parent.mkdir(exist_ok=True)
    content = "line\n" * 18000 + "AFTER_SIXTY_THOUSAND"
    path.write_text(content)

    async def scenario():
        tools = LabTools(make_runtime(root))
        output, failed = await tools.execute("Read", {"file_path": "analysis/long.txt"})
        assert not failed
        first = json.loads(output)
        rest, _ = await collect(tools, first["next"])
        assert first["text"] + rest == content
        for changes in ({"offset": -1}, {"offset": len(content) + 1}, {"offset": True}, {"limit": 0}, {"limit": 20001}):
            assert (await tools.execute("Read", {"file_path": "analysis/long.txt", **changes}))[1]
        source = reading.snapshot(root, '{"items": [1]}')
        for pointer in ("items", "/items/99", "/missing", "/bad~escape", "/items/-1"):
            assert (await tools.execute("Read", {"file_path": source, "json_pointer": pointer}))[1]
        for name in ("../other/report.md", ".mlab/config.json", ".mlab/evidence/../../config.json"):
            assert (await tools.execute("Read", {"file_path": name}))[1]
        assert (await tools.execute("Write", {"file_path": source, "content": "changed"}))[1]
        path.write_text(content + "changed")
        output, failed = await tools.execute("Read", first["next"])
        assert failed and "changed between pages" in output

    asyncio.run(scenario())


def test_attached_and_inline_references_share_one_limit(workspace_path):
    course, root = setup(workspace_path)
    records = [draft(course, root) for _ in range(12)]
    refs = [item["key"] for item in records]
    chosen, _ = exploration.conversation_context(root, "Explain " + " ".join(f"@{item['id']}" for item in records), refs)
    assert chosen == refs
