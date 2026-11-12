"""`mlab web`: the course page on 127.0.0.1, one agent per session.

A session is one attempt at a task from the start, in its own workspace of the course directory; the student starts
sessions from the page and can go back to any of them.

Rules stay in mlab: records are read through a read-only Lab, forms go through core.student and the Lab methods the
CLI uses, and runs are started only by the agents' mlab commands. Every response is JSON in the CLI's envelope; a
refused check is HTTP 200 with ok:false.
"""
import asyncio
import contextlib
import webbrowser
from pathlib import Path
from starlette.applications import Starlette
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from ..core.io import NOTICE, LabError
from ..core.workspace import Lab
from . import approvals, forms, i18n, views
from .agent import TaskRuntime
from .bus import Bus
from .course import Course
from .watcher import Watcher

STATIC = Path(__file__).parent / "static"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class Guard:
    """JSON-only writes, so a plain cross-site form cannot post here, and the same headers on every response.

    There is no login: the server listens on 127.0.0.1, and whoever reaches that address, directly or through a port
    forward, uses the page.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["method"] not in {"GET", "HEAD"} and not Headers(scope=scope).get("content-type", "").startswith("application/json"):
            return await self.secure(PlainTextResponse("JSON only", 415), scope, receive, send)
        await self.secure(self.app, scope, receive, send)

    async def secure(self, app, scope, receive, send):
        # Page files are revalidated rather than downloaded again; data is never cached.
        cache = "no-cache" if scope["path"] == "/" or scope["path"].startswith("/static/") else "no-store"
        async def with_headers(message):
            if message["type"] == "http.response.start":
                extra = MutableHeaders(scope=message)
                extra["Content-Security-Policy"] = CSP
                extra["X-Content-Type-Options"] = "nosniff"
                extra["Referrer-Policy"] = "no-referrer"
                extra["X-Frame-Options"] = "DENY"
                extra.setdefault("Cache-Control", cache)
            await send(message)
        await app(scope, receive, with_headers)


def failure(code, message):
    return {"ok": False, "status": "rejected", "error": {"code": code, "message": message, "field": None}}


def read(root, function, *args):
    """function(read-only Lab, *args) in the CLI's envelope."""
    lab = None
    try:
        lab = Lab(root, readonly=True)
        output = function(lab, *args)
        return {"ok": True, **output} if isinstance(output, dict) else {"ok": True, "items": output}
    except (LabError, ValueError, KeyError, TypeError, OSError) as error:
        return forms.rejected(error)
    finally:
        if lab:
            lab.close()


def agent_factory(kind, provider=None):
    if kind == "fake":
        from .fake_client import FakeAgentClient
        return FakeAgentClient
    from .sdk_client import factory
    return factory(provider)


def create_app(root, agent="fake", provider=None, opening=True, auto_continue=False, watch_interval=1.0):
    course = Course(root)
    bus = Bus()
    factory = agent_factory(agent, provider)
    runtimes = {}

    def add_runtime(session):
        runtimes[session["id"]] = TaskRuntime(session["id"], session["root"], bus, factory, opening, auto_continue)

    for session in course.sessions():
        add_runtime(session)
    watcher = Watcher(runtimes, bus, watch_interval)

    def runtime_of(request):
        runtime = runtimes.get(request.path_params["session"])
        if not runtime:
            raise LabError("E_SCHEMA", f"unknown session {request.path_params['session']}", 2)
        # The page sends its language with every request; the agent is told to reply in it.
        runtime.lang = i18n.language(request.headers.get("x-mlab-lang")) or runtime.lang
        return runtime

    async def body_of(request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise LabError("E_SCHEMA", "the body must be a JSON object", 2)
        return body

    def endpoint(handler):
        async def wrapped(request):
            try:
                return JSONResponse(await handler(request))
            except LabError as error:
                status = 404 if str(error).startswith("unknown session") else 200
                return JSONResponse(forms.rejected(error), status)
        return wrapped

    async def index(request):
        page = STATIC / "index.html"
        if not page.exists():
            return PlainTextResponse("static files missing", 500)
        return FileResponse(page)

    async def course_view(request):
        tasks = course.tasks()
        titles = {item["task"]: item["title"] for item in tasks}
        sessions = []
        for session in course.sessions():
            runtime = runtimes.get(session["id"])
            if not runtime:
                continue
            state = runtime.state or (await asyncio.to_thread(read, runtime.root, views.task_state))
            sessions.append({"id": session["id"], "task": session["task"], "number": session["number"], "title": titles.get(session["task"], ""),
                             "created_at": session["created_at"], "badge": state.get("badge"), "step": state.get("step"),
                             "step_label": state.get("step_label"), "selection": state.get("selection"), "conclusion": state.get("conclusion"),
                             "budget": (state.get("budget") or {}).get("hardware"), "agent": runtime.snapshot()["status"]})
        settings = course.settings()
        return {"ok": True, "tasks": tasks, "sessions": sessions, "notice": NOTICE, "agent": agent, "provider": settings.get("provider"),
                "simulator": settings.get("simulator"), "hardware": settings.get("hardware"), "last_event": bus.last}

    async def create_session(request):
        """A new session: the task from the start, with its own workspace and agent."""
        body = await body_of(request)
        name = await asyncio.to_thread(course.create, str(body.get("task") or ""))
        session = next(item for item in course.sessions() if item["id"] == name)
        add_runtime(session)
        watcher.poke()
        bus.publish("course.changed", name, created=name)
        return {"ok": True, "session": name, "task": session["task"], "number": session["number"]}

    async def state(request):
        runtime = runtime_of(request)
        result = await asyncio.to_thread(read, runtime.root, views.task_state, runtime.status)
        return {**result, "agent": runtime.snapshot()}

    def page_of(runtime, record, with_chat):
        """Everything a task's page shows, from one read-only Lab: one request per navigation through a slow forward."""
        def build(lab):
            state = views.task_state(lab, runtime.status)
            identifier = record or views.default_record(state)
            try:
                shown = {"ok": True, **views.record(lab, identifier, runtime.lang)}
            except (LabError, ValueError, KeyError, TypeError, OSError) as error:
                shown = forms.rejected(error)
            notebook = shown if shown.get("kind") == "overview" else views.task_notebook(lab)
            return {"session": runtime.key, "state": {"ok": True, **state}, "overview": {"ok": True, **views.task_overview(lab)},
                    "record_id": identifier, "record": shown, "notebook": {"ok": True, **notebook},
                    "chat": runtime.chat.read(120) if with_chat else None}
        return read(runtime.root, build)

    async def page(request):
        runtime = runtime_of(request)
        result = await asyncio.to_thread(page_of, runtime, request.query_params.get("record") or None, request.query_params.get("chat") != "0")
        return {**result, "agent": runtime.snapshot()}

    def reader(function, *names):
        async def handler(request):
            runtime = runtime_of(request)
            return await asyncio.to_thread(read, runtime.root, function, *[request.path_params[n] for n in names])
        return handler

    async def record_view(request):
        runtime = runtime_of(request)
        return await asyncio.to_thread(read, runtime.root, views.record, request.path_params["record"], runtime.lang)

    async def pairs(request):
        runtime = runtime_of(request)
        return await asyncio.to_thread(read, runtime.root, views.alignment_pairs, request.path_params["measurement"],
                                       request.query_params.get("run", ""))

    async def compare(request):
        runtime = runtime_of(request)
        body = await body_of(request)
        runs = [run for run in body.get("runs") or [] if isinstance(run, str)][:12]
        return await asyncio.to_thread(read, runtime.root, views.comparison_view, runs)

    async def preview(request):
        runtime = runtime_of(request)
        kind = request.path_params["kind"]
        if kind not in approvals.TITLES:
            raise LabError("E_SCHEMA", f"unknown preview {kind}", 2)
        args = dict(request.query_params)
        return await asyncio.to_thread(read, runtime.root, lambda lab: approvals.summarize(lab, kind, args))

    async def chat_history(request):
        runtime = runtime_of(request)
        limit = max(1, min(int(request.query_params.get("limit", 200)), 500))
        entries = await asyncio.to_thread(runtime.chat.read, limit, request.query_params.get("before"))
        return {"ok": True, "entries": entries, "agent": runtime.snapshot()}

    async def form(request, kind=None, extra=None):
        runtime = runtime_of(request)
        kind = kind or request.path_params["kind"]
        body = {**await body_of(request), **(extra or {}), "lang": runtime.lang}
        if runtime.busy():
            if not body.get("interrupt"):
                return failure("E_AGENT_BUSY", "The agent is working. Wait for it to finish, or stop the agent and submit.")
            if not await runtime.interrupt_and_wait():
                return failure("E_AGENT_BUSY", "The agent did not stop in time; try again later or restart it.")
        async with runtime.form_lock:
            result, notice = await asyncio.to_thread(forms.submit, runtime.root, kind, body)
        if result["ok"]:
            watcher.poke()
            bus.publish("records.changed", runtime.key, form=kind)
            if notice:
                result["agent"] = await runtime.notify(notice)
        return result

    async def chat(request):
        runtime = runtime_of(request)
        body = await body_of(request)
        text = str(body.get("text") or "").strip()
        if not text:
            return failure("E_SCHEMA", "The message is empty")
        refs = list(dict.fromkeys(views.references(text) + [r for r in body.get("refs") or [] if isinstance(r, str)]))
        mode = body.get("mode", "queue")
        if mode not in {"queue", "interrupt_then_send"}:
            return failure("E_SCHEMA", "mode must be queue or interrupt_then_send")
        outcome = await runtime.say(text[:4000], refs, interrupt=mode == "interrupt_then_send")
        return {"ok": True, "status": outcome, "refs": refs, "agent": runtime.snapshot()}

    async def agent_action(request):
        runtime = runtime_of(request)
        action = request.path_params["action"]
        body = await body_of(request)
        if action == "open":
            outcome = await runtime.open()
        elif action == "interrupt":
            await runtime.interrupt()
            outcome = "interrupted"
        elif action == "restart":
            await runtime.restart()
            outcome = "restarting"
        elif action == "continue":
            outcome = await runtime.continue_after(str(body.get("owner") or "the current record"))
        else:
            raise LabError("E_SCHEMA", f"unknown agent action {action}", 2)
        return {"ok": True, "status": outcome, "agent": runtime.snapshot()}

    async def approval(request):
        runtime = runtime_of(request)
        body = await body_of(request)
        decision = body.get("decision")
        if decision not in {"allow", "deny"}:
            return failure("E_SCHEMA", "decision must be allow or deny")
        done = runtime.resolve(request.path_params["card"], {"allow": decision == "allow", "reason": str(body.get("reason") or "")[:2000]})
        return {"ok": done, **({} if done else failure("E_SCHEMA", "This card was already handled or does not exist"))}

    async def question(request):
        runtime = runtime_of(request)
        body = await body_of(request)
        answers = body.get("answers")
        if body.get("cancel"):
            done = runtime.resolve(request.path_params["card"], {"cancelled": True, "reason": "The student skipped the question."})
        elif isinstance(answers, dict) and answers:
            done = runtime.resolve(request.path_params["card"], {"answers": {str(k): str(v)[:2000] for k, v in answers.items()}})
        else:
            return failure("E_SCHEMA", "answers must map each question to the chosen option")
        return {"ok": done, **({} if done else failure("E_SCHEMA", "This card was already handled or does not exist"))}

    async def events(request):
        """Events after ?last=N, answered at once. Without last: only the current number, to start from."""
        last = request.query_params.get("last", "")
        if not last.isdigit():
            return {"ok": True, "last": bus.last, "events": []}
        missed = bus.since(int(last))
        if missed is None:
            return {"ok": True, "last": bus.last, "events": [], "resync": True}
        batch = missed[:500]
        return {"ok": True, "last": batch[-1]["id"] if batch else int(last), "events": batch}

    get, post = ["GET"], ["POST"]
    routes = [
        Route("/", index, methods=get),
        Mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static"),
        Route("/api/course", endpoint(course_view), methods=get),
        Route("/api/sessions", endpoint(create_session), methods=post),
        Route("/api/events", endpoint(events), methods=get),
        Route("/api/sessions/{session}/state", endpoint(state), methods=get),
        Route("/api/sessions/{session}/page", endpoint(page), methods=get),
        Route("/api/sessions/{session}/overview", endpoint(reader(views.task_overview)), methods=get),
        Route("/api/sessions/{session}/notebook", endpoint(reader(views.task_notebook)), methods=get),
        Route("/api/sessions/{session}/records/{record:path}", endpoint(record_view), methods=get),
        Route("/api/sessions/{session}/runs/{run}/requests", endpoint(reader(views.run_requests, "run")), methods=get),
        Route("/api/sessions/{session}/measurements/{measurement}/pairs", endpoint(pairs), methods=get),
        Route("/api/sessions/{session}/preview/{kind}", endpoint(preview), methods=get),
        Route("/api/sessions/{session}/chat", endpoint(chat_history), methods=get),
        Route("/api/sessions/{session}/compare", endpoint(compare), methods=post),
        Route("/api/sessions/{session}/forms/{kind}", endpoint(form), methods=post),
        Route("/api/sessions/{session}/chat", endpoint(chat), methods=post),
        Route("/api/sessions/{session}/agent/{action}", endpoint(agent_action), methods=post),
        Route("/api/sessions/{session}/approvals/{card}", endpoint(approval), methods=post),
        Route("/api/sessions/{session}/questions/{card}", endpoint(question), methods=post),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(app):
        watcher.start()
        try:
            yield
        finally:
            await watcher.stop()
            await asyncio.gather(*(runtime.stop() for runtime in runtimes.values()))

    # Compressed responses matter through a port forward.
    app = Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(GZipMiddleware, minimum_size=1000, compresslevel=6), Middleware(Guard)])
    app.state.runtimes, app.state.bus, app.state.watcher, app.state.course = runtimes, bus, watcher, course
    return app


def serve(root, simulator="llm", provider=None, agent="sdk", port=8765, browser=True, opening=True, auto_continue=False):
    import socket
    import uvicorn
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            print(f"Port {port} is in use. Pick another one: mlab web {root} --port {port + 1}")
            return 2
    course = Course(root)
    settings = course.ensure(simulator, "standin", provider)
    application = create_app(course.root, agent, settings.get("provider"), opening, auto_continue)
    url = f"http://127.0.0.1:{port}/"
    print(f"Course directory: {course.root} ({len(course.sessions())} sessions; start new ones on the page)")
    print(f"Simulator: {settings['simulator']}; hardware: {settings['hardware']}; agent: {agent}" + (f", {settings['provider']}" if settings.get("provider") else ""))
    if settings["simulator"] != simulator or settings.get("provider") != provider:
        print("This course directory keeps the settings it was created with; use a new directory for another simulator or model.")
    print(f"Open: {url} (on a remote machine, use the address your editor's port forward gives)")
    if browser:
        webbrowser.open(url)
    # Ctrl+C waits at most two seconds for open requests, then exits.
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=2)
    return 0
