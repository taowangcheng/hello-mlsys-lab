"""The page loads nothing from outside, runs no inline script, inserts data only as text, and calls only real endpoints."""
import json
import re
import shutil
import subprocess
import pytest
from starlette.testclient import TestClient
from mlab.web import approvals, forms
from mlab.web.course import Course
from mlab.web.server import STATIC, create_app

PAGE = (STATIC / "index.html").read_text(encoding="utf-8")
SCRIPTS = {name: (STATIC / name).read_text(encoding="utf-8") for name in ("app.js", "charts.js", "i18n.js")}
STYLE = (STATIC / "app.css").read_text(encoding="utf-8")


def test_page_has_no_inline_code_or_outside_resources():
    everything = (PAGE + STYLE + "".join(SCRIPTS.values())).replace('"http://www.w3.org/2000/svg"', "")  # The SVG namespace is a name, not a load.
    assert not re.search(r"https?://", everything)
    assert re.findall(r"<script[^>]*>", PAGE) == ['<script type="module" src="/static/app.js">']
    assert not re.search(r"\son\w+=", PAGE) and " style=" not in PAGE
    assert "@import" not in STYLE and not re.search(r"url\((?!data:)", STYLE)


def test_scripts_insert_data_as_text_only():
    for name, code in SCRIPTS.items():
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert unsafe not in code, (name, unsafe)


def test_forms_and_previews_the_page_uses_exist():
    code = SCRIPTS["app.js"]
    used = set(re.findall(r'submitForm\("(\w+)"', code))
    assert used and used <= set(forms.HANDLERS), used - set(forms.HANDLERS)
    previews = set(re.findall(r"preview/(\w+)", code))
    assert previews and previews <= set(approvals.TITLES), previews - set(approvals.TITLES)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_scripts_parse_and_chart_helpers_behave(tmp_path):
    for name, code in SCRIPTS.items():
        (tmp_path / name.replace(".js", ".mjs")).write_text(code.replace('"./charts.js"', '"./charts.mjs"').replace('"./i18n.js"', '"./i18n.mjs"'),
                                                            encoding="utf-8")
        process = subprocess.run(["node", "--check", str(tmp_path / name.replace(".js", ".mjs"))], capture_output=True, text=True)
        assert process.returncode == 0, process.stderr
    probe = ("import { ticks, format, unitOf } from './charts.mjs';"
             "console.log(JSON.stringify([ticks(0, 2125, 5), format(2370.04), format(3.14159), unitOf('ttft_ms.p99'), unitOf('makespan_s'), unitOf('goodput_tok_s')]));")
    (tmp_path / "probe.mjs").write_text(probe, encoding="utf-8")
    process = subprocess.run(["node", str(tmp_path / "probe.mjs")], capture_output=True, text=True, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == '[[0,500,1000,1500,2000,2500],"2,370","3.14","ms","s","tok/s"]'


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_record_ids_in_text_become_links(tmp_path):
    """Rounds, measurements, configurations and runs named in the agent's words become links; paths and commands stay text."""
    code = SCRIPTS["app.js"]
    parts = [re.search(rf"^const {name} = .*;$", code, re.M).group(0) for name in ("RECORD_ID", "INLINE")]
    parts.append(re.search(r"^function inline\(text\) \{.*?^\}", code, re.M | re.S).group(0))
    # The page may be in Chinese: an ID right after a Chinese character still links (the escapes keep this file English).
    samples = ["Pick r002/c16 over r001 and m001; hw_0001 is far off. \u5bf9\u7167r003\u3002",
               "See rounds/r002/analysis.md, r002/analysis.md and `mlab align m001`.",
               "**r001/low_concurrency** is steadier; sim_0004 is its simulation."]
    probe = ("const document = { createTextNode: (text) => text };\n"
             "const el = (tag, props, ...children) => ({ tag, ...props, children: children.flat() });\n"
             "const link = (id) => `#/9.2-1/${encodeURIComponent(id)}`;\n" + "\n".join(parts) +
             "\nconst links = (nodes) => nodes.flatMap((node) => node.tag === 'a' ? [node.text] : links(node.children || []));\n"
             f"console.log(JSON.stringify({json.dumps(samples, ensure_ascii=False)}.map((text) => links(inline(text)))));")
    (tmp_path / "links.mjs").write_text(probe, encoding="utf-8")
    process = subprocess.run(["node", str(tmp_path / "links.mjs")], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == [["r002/c16", "r001", "m001", "hw_0001", "r003"], [], ["r001/low_concurrency", "sim_0004"]]


def test_page_and_assets_are_compressed_and_revalidated(workspace_path):
    """Through a port forward every byte and round trip counts: page files are gzipped and answered with 304 when unchanged."""
    course = Course(workspace_path)
    course.ensure("fixture")
    course.create("9.2")
    with TestClient(create_app(workspace_path, "fake"), base_url="http://testserver") as web:
        page = web.get("/")
        assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "default-src 'self'" in page.headers["content-security-policy"]
        script = web.get("/static/app.js", headers={"accept-encoding": "gzip"})
        assert script.status_code == 200 and "javascript" in script.headers["content-type"]
        assert script.headers["content-encoding"] == "gzip" and script.headers["cache-control"] == "no-cache"
        again = web.get("/static/app.js", headers={"if-none-match": script.headers["etag"]})
        assert again.status_code == 304
        assert web.get("/static/charts.js").status_code == 200 and web.get("/static/app.css").status_code == 200
        assert web.get("/api/sessions/9.2-1/overview", headers={"accept-encoding": "gzip"}).headers.get("content-encoding") == "gzip"
