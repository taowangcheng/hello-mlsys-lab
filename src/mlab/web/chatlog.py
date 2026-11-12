"""The chat a task shows on the page, kept in .mlab/web/chat.jsonl of its workspace.

Roles: student (the student's words), agent (the agent's replies), tool (the agent's tool calls), card (confirmation and
question cards), notice (what the page told the agent or the student), error. Later changes to an entry, such as a
card's resolution or a tool's result, are appended as update lines and merged on reading.
"""
import json
from pathlib import Path
from ..core.store import now


class ChatLog:
    def __init__(self, root):
        self.path = Path(root) / ".mlab/web/chat.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = sum(1 for entry in self.lines() if "update" not in entry)

    def lines(self):
        if not self.path.exists():
            return []
        found = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                found.append(json.loads(line))
            except ValueError:
                continue
        return found

    def write(self, record):
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append(self, role, text="", **extra):
        self.count += 1
        entry = {"id": f"m{self.count:06}", "at": now(), "role": role, "text": text, **extra}
        self.write(entry)
        return entry

    def update(self, entry_id, **changes):
        self.write({"update": entry_id, **changes})
        return {"id": entry_id, **changes}

    def read(self, limit=200, before=None):
        entries, order = {}, []
        for record in self.lines():
            if "update" in record:
                if record["update"] in entries:
                    entries[record["update"]].update({k: v for k, v in record.items() if k != "update"})
            elif "id" in record:
                entries[record["id"]] = record
                order.append(record["id"])
        if before:
            order = [identifier for identifier in order if identifier < before]
        return [entries[identifier] for identifier in order[-limit:]]
