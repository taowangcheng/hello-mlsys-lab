"""One agent per session, a session being one attempt at a task in its own workspace. A single owner coroutine holds
the model client for its whole life; the page reaches it through a queue, so the client is never touched from two tasks.

The client is replaceable: `fake_client.FakeAgentClient` for offline walkthroughs, `codex_client` for Codex,
and the opt-in legacy `sdk_client`. A client implements connect(session) -> session, turn(prompt) -> async events,
interrupt() and disconnect(), and asks for permissions through TaskRuntime.permission().
"""
import asyncio
import contextlib
import time
from pathlib import Path
from ..core.history import brief
from ..core.io import LabError, atomic, load
from ..core.student import log_student, position
from ..core.workspace import Lab
from . import approvals, i18n, labels
from .chatlog import ChatLog

# Text the page adds to what the agent receives. It is marked so that it is never taken for the student's words.
NOTE = "[Page note]"
NOTICE = "[Page notice]"
IDLE_DISCONNECT_S = 20 * 60
OPENING_GAP_S = 10 * 60
AUTO_CONTINUE_PER_HOUR = 6
DENIED_COMMAND = "This command is not allowed: run one mlab command at a time, without loops, pipes, && or ;. Use Read to read files."
OPENING = (f"{NOTICE} The student opened this session's page.\nIn one or two sentences, say where things stand and ask for the one input the "
           "next step needs. Do not run commands that change records in this turn.")


def tool_summary(name, data):
    if name == "Bash":
        return str(data.get("command", ""))[:400]
    if name in {"Read", "Edit", "Write"}:
        return str(data.get("file_path", ""))
    if name == "AskUserQuestion":
        return "; ".join(q.get("question", "") for q in data.get("questions", []))
    return name


class TaskRuntime:
    def __init__(self, key, root, bus, factory, opening=True, auto_continue=False, source="web"):
        # key: the session's id on the page, e.g. 9.2-1.
        self.key, self.root, self.bus, self.factory = key, Path(root), bus, factory
        self.opening, self.auto_continue = opening, auto_continue
        self.source = source
        self.lang = None  # The page's language, from its requests; the agent replies in it.
        self.told = None  # The language the agent was last told.
        self.chat = ChatLog(self.root)
        for entry in self.chat.read(limit=1_000_000):
            if entry.get("role") == "card" and entry.get("state") == "pending":
                self.chat.update(entry["id"], state="expired")  # Cards live in memory; a restart ends them.
        self.status, self.detail = "offline", None
        self.client = None
        self.queue = asyncio.Queue()
        self.inbox = []
        self.cards = {}
        self.owner = None
        self.turning = False
        self.idle = asyncio.Event()
        self.idle.set()
        self.tools = {}
        self.last_opened = None
        self.auto_sent = []
        self.state = None  # The watcher keeps the latest task state here.
        self.meta_path = self.root / ".mlab/web/agent.json"
        self.meta = {"session": None, "turns": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                     "cost_estimated": False, "cost_available": True}
        if self.meta_path.exists():
            self.meta.update(load(self.meta_path))
        self.form_lock = asyncio.Lock()

    # Page-facing state.

    def snapshot(self):
        return {"status": self.status, "label": labels.AGENT_STATUS.get(self.status, self.status), "detail": self.detail,
                "cards": [self.public(card) for card in self.cards.values()], "queued": len(self.inbox), "connected": bool(self.client),
                "session": self.meta.get("session"), "usage": {k: self.meta[k] for k in ("turns", "cost_usd", "input_tokens", "output_tokens",
                                                                                         "cost_estimated", "cost_available")}}

    def busy(self):
        return self.status == "connecting" or self.turning or bool(self.inbox) or bool(self.cards)

    def set_status(self, status, detail=None):
        self.status, self.detail = status, detail
        badge = labels.badge(self.state, status) if self.state else None
        self.bus.publish("agent.status", self.key, status=status, label=labels.AGENT_STATUS.get(status, status), detail=detail,
                         badge=badge, queued=len(self.inbox))

    def add(self, role, text="", **extra):
        entry = self.chat.append(role, text, **extra)
        self.bus.publish("chat.append", self.key, entry=entry)
        return entry

    def change(self, entry_id, **changes):
        self.chat.update(entry_id, **changes)
        self.bus.publish("chat.update", self.key, entry={"id": entry_id, **changes})

    def save_meta(self):
        atomic(self.meta_path, self.meta)

    # Life of the owner coroutine.

    def start(self):
        if not self.owner:
            self.owner = asyncio.create_task(self.own(), name=f"agent-{self.key}")

    async def stop(self):
        if self.owner:
            await self.queue.put(("stop", None))
            try:
                await asyncio.wait_for(self.owner, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.owner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.owner
            self.owner = None
        await self.disconnect()

    async def own(self):
        while True:
            try:
                kind, payload = await asyncio.wait_for(self.queue.get(), IDLE_DISCONNECT_S if self.client else None)
            except asyncio.TimeoutError:
                if self.client and not self.inbox:
                    await self.disconnect()
                    self.set_status("offline", "Idle too long, disconnected; the next message resumes the conversation")
                continue
            if kind == "stop":
                await self.disconnect()
                return
            if kind == "restart":
                await self.disconnect()
                self.set_status("offline", "Restarted; the next message resumes the conversation")
                continue
            if kind == "connect":
                await self.connect()
            while self.inbox:
                prompt = self.merge()
                if not await self.connect():
                    break
                await self.turn(prompt)
            self.idle.set()

    async def connect(self):
        if self.client:
            return True
        self.set_status("connecting")
        client = self.factory(self)
        try:
            session = await asyncio.wait_for(client.connect(self.meta.get("session")), 90)
        except Exception as error:  # noqa: BLE001 - any connection failure is shown on the page
            self.inbox.clear()
            self.set_status("error", f"Agent failed to connect: {str(error)[:300]}")
            self.add("error", f"Agent failed to connect: {str(error)[:300]}")
            return False
        self.client = client
        if session and session != self.meta.get("session"):
            self.meta["session"] = session
            self.save_meta()
        self.set_status("idle")
        return True

    async def disconnect(self):
        client, self.client = self.client, None
        self.deny_all("the agent disconnected")
        if client:
            try:
                await asyncio.wait_for(client.disconnect(), 15)
            except Exception:  # noqa: BLE001 - a broken client is dropped either way
                pass

    def merge(self):
        prompts, self.inbox = self.inbox, []
        if len(prompts) == 1:
            return prompts[0]
        return "\n\n".join(prompts) + f"\n\n{NOTE} The {len(prompts)} messages above arrived while you were working; handle them in order."

    def with_language(self, prompt):
        """Tell the agent the page's language when it is new to the agent; the agent keeps replying in it."""
        if not self.lang or self.lang == self.told:
            return prompt
        self.told = self.lang
        name = i18n.LANGUAGES[self.lang]
        return prompt + f"\n\n{NOTE} The page is set to {name}: reply to the student in {name}."

    async def turn(self, prompt):
        self.turning = True
        self.set_status("running")
        prompt = self.with_language(prompt)
        try:
            async for event in self.client.turn(prompt):
                self.event(event)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the page shows it; the next message reconnects and resumes
            self.add("error", f"Could not complete the reply: {str(error)[:300]}")
            self.told = None
            self.turning = False
            self.set_status("error", str(error)[:300])
            await self.disconnect()
            return
        finally:
            self.turning = False
            self.deny_all("the turn ended")
        self.set_status("idle")

    def event(self, event):
        kind = event.get("type")
        if kind == "delta":
            self.bus.publish("chat.delta", self.key, text=event["text"])
        elif kind == "text" and event.get("text", "").strip():
            self.add("agent", event["text"].strip())
        elif kind == "tool":
            entry = self.add("tool", tool_summary(event["name"], event.get("input") or {}), tool=event["name"])
            if event.get("id"):
                self.tools[event["id"]] = entry["id"]
        elif kind == "tool_result" and event.get("id") in self.tools:
            self.change(self.tools.pop(event["id"]), result=str(event.get("text", ""))[:600], is_error=bool(event.get("is_error")))
        elif kind == "result":
            self.meta["turns"] += 1
            self.meta["cost_usd"] = round(self.meta["cost_usd"] + float(event.get("cost_usd") or 0), 6)
            usage = event.get("usage") or {}
            self.meta["input_tokens"] += int(usage.get("input_tokens") or 0)
            self.meta["output_tokens"] += int(usage.get("output_tokens") or 0)
            self.meta["cost_estimated"] = bool(self.meta["cost_estimated"] or event.get("cost_estimated"))
            if "cost_available" in event:
                self.meta["cost_available"] = event["cost_available"]
            if "cost_seen" in event:
                self.meta["session_cost_seen"] = event["cost_seen"]
            if event.get("session"):
                self.meta["session"] = event["session"]
            self.save_meta()
            self.bus.publish("agent.usage", self.key, usage=self.snapshot()["usage"], turn={"cost_usd": event.get("cost_usd"), **usage})

    # Messages from the page.

    async def send(self, prompt, interrupt=False):
        """Run the prompt now, or after the current turn. Queued prompts are merged into the next turn."""
        if interrupt and self.turning:
            await self.interrupt()
        self.inbox.append(prompt)
        self.idle.clear()
        self.start()
        await self.queue.put(("send", None))
        if self.turning:
            self.bus.publish("agent.status", self.key, status=self.status, label=labels.AGENT_STATUS.get(self.status), queued=len(self.inbox))
            return "queued"
        return "sent"

    def references(self, refs):
        """One line of facts per @reference, for the agent; the page adds them, the student did not write them."""
        if not refs:
            return []
        lab = Lab(self.root, readonly=True)
        try:
            lines = []
            for ref in refs[:8]:
                try:
                    lines.append(brief(lab, ref))
                except LabError as error:
                    lines.append(f"{ref}: {error}")
            return lines
        finally:
            lab.close()

    async def say(self, text, refs=(), interrupt=False):
        """The student wrote in the chat. While a card waits, the words answer the card."""
        text = (text or "").strip()
        refs = list(dict.fromkeys(refs))
        where = await asyncio.to_thread(position, self.root)
        await asyncio.to_thread(log_student, self.root, text, source="cli" if self.source == "cli" else "web-chat", kind="chat", refs=refs,
                                session=self.meta.get("session"), **where)
        self.add("student", text, refs=refs)
        pending = next(iter(self.cards.values()), None)
        if pending:
            if pending["kind"] == "approval":
                self.resolve(pending["id"], {"allow": False, "reason": text, "logged": True})
            else:
                first = (pending["data"]["questions"] or [{}])[0].get("question", "")
                self.resolve(pending["id"], {"answers": {first: text}, "logged": True})
            return "answered_card"
        facts = await asyncio.to_thread(self.references, refs)
        prompt = text if not facts else text + f"\n\n{NOTE} Records cited (facts the page added, not the student's words):\n" + "\n".join(f"- {line}" for line in facts)
        return await self.send(prompt, interrupt)

    async def notify(self, notice, show=True):
        """A page notice to the agent: shown in the chat as such, never logged as the student's words."""
        if show:
            self.add("notice", notice.removeprefix(NOTICE).strip().split("\n")[0], to="agent")
        return await self.send(notice)

    async def open(self):
        """The page opened this task: connect, and once per ten minutes let the agent say where things stand."""
        self.start()
        now = time.monotonic()
        if self.opening and not self.busy() and (self.last_opened is None or now - self.last_opened > OPENING_GAP_S):
            self.last_opened = now
            return await self.send(OPENING)
        await self.queue.put(("connect", None))
        return "connected"

    async def interrupt(self):
        """Stop the current turn; waiting cards are refused and queued messages dropped."""
        self.inbox.clear()
        self.deny_all("the turn was stopped")
        if self.client and self.turning:
            try:
                await asyncio.wait_for(self.client.interrupt(), 15)
            except Exception:  # noqa: BLE001 - restart remains available on the page
                pass

    async def interrupt_and_wait(self, timeout=60):
        await self.interrupt()
        try:
            await asyncio.wait_for(self.idle.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return not self.busy()

    async def restart(self):
        """Disconnect; the next message reconnects to the same conversation. A fresh start is a new session on the page."""
        await self.interrupt()
        self.start()
        await self.queue.put(("restart", {}))

    async def runs_finished(self, owner, label):
        """The watcher saw every run of the current round or measurement finish."""
        text = f"{label} finished ({owner})."
        self.bus.publish("notify", self.key, text=text, owner=owner)
        if self.busy():
            return  # The agent is working and will see it.
        self.add("notice", text, to="student", action={"label": "Continue", "owner": owner})
        now = time.monotonic()
        self.auto_sent = [t for t in self.auto_sent if now - t < 3600]
        if self.auto_continue and not getattr(self, "exploration", False) and len(self.auto_sent) < AUTO_CONTINUE_PER_HOUR:
            self.auto_sent.append(now)
            await self.continue_after(owner, label)

    async def continue_after(self, owner, label="Runs"):
        return await self.notify(f"{NOTICE} {label} finished ({owner}).\nRun mlab next and continue as it says; stop and ask when a confirmation or "
                                 "input is needed.")

    # Permissions, asked by the client in the middle of a turn.

    def public(self, card):
        return {"id": card["id"], "kind": card["kind"], "entry": card["entry"], **card["data"]}

    def open_card(self, kind, **data):
        future = asyncio.get_running_loop().create_future()
        identifier = f"{kind[0]}{int(time.time() * 1000):x}{len(self.cards)}"
        entry = self.add("card", data.get("command") or tool_summary("AskUserQuestion", data), card=kind, card_id=identifier, state="pending",
                         **{k: v for k, v in data.items() if k != "command"})
        card = {"id": identifier, "kind": kind, "data": data, "future": future, "entry": entry["id"]}
        self.cards[identifier] = card
        self.bus.publish(f"{kind}.request", self.key, card=self.public(card))
        return card

    def resolve(self, identifier, decision):
        card = self.cards.get(identifier)
        if not card or card["future"].done():
            return False
        card["future"].set_result(decision)
        return True

    def close_card(self, card, outcome):
        self.cards.pop(card["id"], None)
        self.change(card["entry"], state=outcome)
        self.bus.publish(f"{card['kind']}.resolved", self.key, id=card["id"], state=outcome)

    def deny_all(self, reason):
        for card in list(self.cards.values()):
            if not card["future"].done():
                card["future"].set_result({"allow": False, "cancelled": True, "reason": reason, "system": True})

    def summarize(self, kind, args):
        lab = Lab(self.root, readonly=True)
        try:
            return approvals.summarize(lab, kind, args)
        finally:
            lab.close()

    async def permission(self, tool, data):
        """{"allow": bool, "message": str, "updated_input": dict}: gated commands become cards, everything else is refused."""
        if tool == "AskUserQuestion":
            return await self.question(data)
        if tool == "Bash":
            command = str(data.get("command", ""))
            found = approvals.classify(command)
            if found["kind"] and found["plain"]:
                return await self.approval(command, found)
            reason = found["reason"] or DENIED_COMMAND
        else:
            reason = f"{tool} is not allowed here; only the record files the agent may write in this workspace can be edited."
            command = tool_summary(tool, data)
        self.add("notice", f"Blocked agent action: {command}", to="student", reason=reason)
        return {"allow": False, "message": reason}

    async def approval(self, command, found):
        summary = await asyncio.to_thread(self.summarize, found["kind"], found["args"])
        card = self.open_card("approval", command=command, summary=summary)
        self.set_status("waiting_approval")
        decision = await card["future"]
        allowed = bool(decision.get("allow"))
        self.close_card(card, "approved" if allowed else "cancelled" if decision.get("system") else "denied")
        if self.turning:
            self.set_status("running")
        if allowed:
            return {"allow": True}
        reason = (decision.get("reason") or "").strip()
        if decision.get("system"):
            return {"allow": False, "message": f"Not run: {reason}."}
        if reason and not decision.get("logged"):
            await asyncio.to_thread(log_student, self.root, reason, source="cli" if self.source == "cli" else "web-card", kind="denial",
                                    **await asyncio.to_thread(position, self.root))
        said = f"Reason: {reason}" if reason else "No reason given; ask what to change."
        return {"allow": False, "message": f"The student did not approve \"{summary['title']}\". {said}"}

    async def question(self, data):
        questions = [q for q in data.get("questions") or [] if isinstance(q, dict)]
        card = self.open_card("question", questions=questions)
        self.set_status("waiting_answer")
        answer = await card["future"]
        if answer.get("cancelled") or not answer.get("answers"):
            self.close_card(card, "cancelled")
            if self.turning:
                self.set_status("running")
            return {"allow": False, "message": f"The question was not answered. {answer.get('reason') or ''}".strip()}
        answers = {str(k): str(v) for k, v in answer["answers"].items()}
        self.close_card(card, "answered")
        if self.turning:
            self.set_status("running")
        if not answer.get("logged"):
            await asyncio.to_thread(log_student, self.root, "\n".join(f"{q}: {a}" for q, a in answers.items()), source="cli" if self.source == "cli" else "web-question",
                                    kind="question", **await asyncio.to_thread(position, self.root))
        return {"allow": True, "updated_input": {**data, "answers": answers}}
