"""The page in Chinese: every text the page shows has an entry in the word list, the server translates what it renders
whole, and the agent is told the page's language. The rest of the repository is English."""
import asyncio
import json
import re
import time
from pathlib import Path
import yaml
from starlette.testclient import TestClient
from mlab.core.io import ROOT, atomic, load
from mlab.core import student
from mlab.core.student import SOURCES
from mlab.core.workspace import Lab, initialize
from mlab.web import approvals, i18n, labels, views, watcher
from mlab.web.agent import TaskRuntime
from mlab.web.bus import Bus
from mlab.web.course import Course
from mlab.web.server import STATIC, create_app
from test_workflow import HYPOTHESIS, decided, ready

ZH = json.loads((STATIC / "locales/zh.json").read_text(encoding="utf-8"))
CJK = re.compile("[%s-%s%s-%s%s-%s]" % tuple(map(chr, (0x3000, 0x303F, 0x4E00, 0x9FFF, 0xFF00, 0xFFEF))))  # Chinese text and punctuation


def literals(text):
    """Double-quoted strings in a piece of JavaScript, without those compared against (x === "save") or passed to another call."""
    return [m.group(2) for m in re.finditer(r'(===\s*|!==\s*|==\s*|\w\(\s*)?"((?:[^"\\]|\\.)*)"', text) if not m.group(1) and m.group(2)]


def first_argument(code, start):
    """The first argument of the call whose "(" is at start: up to its top-level comma or closing parenthesis."""
    depth, index, quote = 0, start, None
    while index < len(code):
        char = code[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return code[start + 1:index]
        elif char == "," and depth == 1:
            return code[start + 1:index]
        index += 1
    return ""


def page_texts():
    """Every English text the page shows: what it passes to t(), the label tables it shows through t(), and the server's labels."""
    texts = set()
    for name in ("app.js", "charts.js"):
        code = (STATIC / name).read_text(encoding="utf-8")
        for match in re.finditer(r"\bt\(", code):
            texts.update(literals(first_argument(code, match.end() - 1)))
        for table in ("SOURCE", "STATUS", "VERDICT", "CARD_STATE", "ASK", "TOOLS"):
            found = re.search(rf"const {table} = \{{(.*?)\}};", code, re.S)
            if found:
                texts.update(re.findall(r':\s*"((?:[^"\\]|\\.)*)"', found.group(1)))
        # Tables the page shows through t(label): tabs, next-step options, review verdicts, shift names, empty texts.
        for pattern in (r"const tabs = (\[.*?\]);", r"const options = (\[.*?\]\]);", r"(\[\[\"keep\".*?\]\])", r"const shift = (\{.*?\});"):
            for found in re.finditer(pattern, code, re.S):
                texts.update(text for text in literals(found.group(1)) if " " in text or text[:1].isupper())
        texts.update(re.findall(r'textBlock\([^;]*?,\s*"([^"]+)"\)', code))
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    texts.update(re.findall(r"data-i18n>([^<]+)<", page))
    texts.update(re.findall(r'aria-label="([^"]+)"', page))
    texts.update([*labels.STEPS.values(), *labels.HINTS.values(), *labels.AGENT_STATUS.values(), *approvals.TITLES.values(),
                  *views.EVENT_LABELS.values(), *watcher.LABELS.values(), *SOURCES.values()])
    for replies in labels.QUICK_REPLIES.values():
        texts.update(replies)
    texts.update(yaml.safe_load(path.read_text(encoding="utf-8"))["title"] for path in (ROOT / "tasks").glob("*/task.yaml"))
    for path in (ROOT / "frontend/src").glob("*.ts*"):
        code = path.read_text(encoding="utf-8")
        texts.update(re.findall(r"\bt\(\s*'([^']+)'", code))
    return texts


def test_the_word_list_covers_the_page():
    entries = list(ZH.items())
    assert entries and all(value.strip() and CJK.search(value) or not CJK.search(key) for key, value in entries)
    for key, value in entries:
        assert sorted(re.findall(r"\{(\w+)\}", key)) == sorted(re.findall(r"\{(\w+)\}", value)), key
    missing = sorted(text for text in page_texts() if text not in ZH)
    assert not missing, missing


def test_sentences_the_server_formats_are_translated():
    assert i18n.translate("zh", "Simulation finished (r001).") == ZH["{label} finished ({owner})."].format(label=ZH["Simulation"], owner="r001")
    reason = "Hardware results conflict with the selection: hw_0003, hw_0004 violated the constraints."
    assert i18n.translate("zh", reason) == ZH["Hardware results conflict with the selection: {runs} violated the constraints."].format(runs="hw_0003, hw_0004")
    notice = "Decision saved and confirmed on the page for r001, selecting c16."
    assert i18n.translate("zh", notice) == ZH["Decision saved and confirmed on the page for {round}, selecting {candidate}."].format(round="r001", candidate="c16")
    point = "c20 · development · 1×: simulated 1940.0 (margin 3.0%)"
    assert "1940.0" in i18n.translate("zh", point) and ZH["simulated {value} (margin {margin}%)"].split("{")[0] in i18n.translate("zh", point)
    assert i18n.translate("zh", "an agent's own sentence") == "an agent's own sentence"
    assert i18n.translate("en", reason) == reason
    assert i18n.english(ZH["Keep all and approve"]) == "Keep all and approve"
    assert i18n.english("@m001 " + ZH["Do the hardware results match the simulation? If not, what might explain the gap?"]).startswith("@m001 Do the")


def test_cards_and_the_report_in_chinese(workspace_path):
    lab = ready(workspace_path)
    try:
        checked = lab.check()
        atomic(lab.folder() / "review.md", "## Comments on the plan\n\nAgree.\n", text=True)
        view = Lab(workspace_path, readonly=True)
        try:
            card = approvals.summarize(view, "workflow_approve", {"plan": checked["plan_hash"]})
        finally:
            view.close()
        assert all(name in ZH for name, _ in card["facts"]) and card["title"] in ZH
        assert all(i18n.translate("zh", effect) != effect for effect in card["effects"])
        lab.approve(checked["plan_hash"])
        lab.submit()
        assert lab.wait(20)["status"] == "completed"
        view = Lab(workspace_path, readonly=True)
        try:
            chinese, english = views.report_view(view, "zh")["markdown"], views.report_view(view)["markdown"]
        finally:
            view.close()
    finally:
        lab.close()
    title = ZH[load(ROOT / "tasks/9.2-sla/task.yaml")["title"]]
    assert chinese.startswith("# " + ZH["Report: {task} {title}"].format(task="9.2", title=title))
    assert f"### {ZH['Comments on the plan']}" in chinese and f"#### {ZH['What result would prove me wrong']}" in chinese
    assert english.startswith("# Report: 9.2 ") and not CJK.search(english)



def test_words_the_review_form_adds_follow_the_page_language(workspace_path):
    """The verdicts and the stamp are the form's words, so they follow the page; the reasons stay as the student wrote them."""
    lab = ready(workspace_path)
    try:
        student.write_review(lab, [{"candidate": "baseline", "verdict": "keep", "reason": "the control"}], "fine", tr=i18n.translator("zh"))
        review = (lab.folder() / "review.md").read_text(encoding="utf-8")
    finally:
        lab.close()
    assert ZH["- {candidate}: {verdict}. {reason}"].format(candidate="baseline", verdict=ZH["keep"], reason="the control") in review
    assert ZH["- Overall: {text}"].format(text="fine") in review and review.startswith("## Comments on the plan")


class Recorder:
    """A client that only records the prompts it gets."""

    def __init__(self, runtime):
        self.runtime, self.prompts = runtime, []

    async def connect(self, session):
        return session or "recorder"

    async def disconnect(self):
        pass

    async def interrupt(self):
        pass

    async def turn(self, prompt):
        self.prompts.append(prompt)
        yield {"type": "text", "text": "OK"}
        yield {"type": "result", "usage": {}, "cost_usd": 0}


def test_the_agent_is_told_the_page_language_when_it_changes(workspace_path):
    initialize(workspace_path, "9.2", "fixture")
    clients = []

    def factory(runtime):
        clients.append(Recorder(runtime))
        return clients[-1]

    async def scenario():
        runtime = TaskRuntime("9.2-1", workspace_path, Bus(), factory, opening=False)
        async def say(text):
            await runtime.say(text)
            while runtime.busy() or runtime.turning:
                await asyncio.sleep(.01)
        await say("no language yet")
        runtime.lang = "zh"
        await say("first")
        await say("second")
        runtime.lang = "en"
        await say("third")
        await runtime.stop()
    asyncio.run(scenario())
    prompts = clients[0].prompts
    assert "[Page note]" not in prompts[0]
    assert prompts[1].endswith("[Page note] The page is set to Chinese (Simplified): reply to the student in Chinese (Simplified).")
    assert prompts[2] == "second" and prompts[3].endswith("reply to the student in English.")


def test_the_fake_agent_answers_in_the_page_language(workspace_path):
    course = Course(workspace_path)
    course.ensure("fixture")
    course.create("9.2")
    with TestClient(create_app(workspace_path, "fake"), base_url="http://testserver") as web:
        step = web.get("/api/sessions/9.2-1/state").json()["step"]
        assert web.post("/api/sessions/9.2-1/agent/open", json={}, headers={"X-Mlab-Lang": "zh"}).json()["ok"]
        for _ in range(200):
            replies = [e["text"] for e in web.get("/api/sessions/9.2-1/chat").json()["entries"] if e["role"] == "agent"]
            if replies:
                break
            time.sleep(.05)
        assert replies and replies[0].startswith(ZH[labels.STEPS[step]]) and CJK.search(replies[0]), replies


def test_the_repository_is_english():
    """Chinese appears only in the page's word list, the Chinese copy of the plan, and the language switch's own name."""
    allowed = {"src/mlab/web/static/locales/zh.json", "docs/plan.zh-CN.md"}
    # Also kept: the link to the plan's Chinese copy, and the name of the course's implementation plan, a file outside this repo.
    external = "".join(map(chr, (0x5B9E, 0x65BD, 0x65B9, 0x6848))) + "_1.md"
    roots = ["src", "frontend/src", "frontend/tests", "tests", "evals", "scripts", "template", "tasks", "examples", "catalog", "agent", "config", "docs", ".github"]
    found = []
    for path in [ROOT / "README.md", ROOT / "pyproject.toml", *(p for root in roots for p in (ROOT / root).rglob("*") if p.is_file())]:
        relative = path.relative_to(ROOT).as_posix()
        if relative in allowed or relative.startswith("src/mlab/web/static/workbench/") or "__pycache__" in relative or path.suffix in {".pyc", ".png", ".jpg", ".mp4"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            kept = (relative == "src/mlab/web/static/i18n.js" and "LANGUAGES" in line) or "](plan.zh-CN.md)" in line or external in line
            if CJK.search(line) and not kept:
                found.append(f"{relative}:{number}")
    assert not found, found
