"""The course directory `mlab web` serves: its settings, and one workspace per session.

A session is one attempt at a task from the start: its own records, agent and conclusion. Sessions of the same task
share only the hardware budget, which Lab.hardware_budget counts across them.
"""
import re
from pathlib import Path
from ..core.io import ROOT, LabError, atomic, load, require
from ..core.workspace import TASKS, initialize

NAME = re.compile(r"^(\d\.\d)-(\d+)$")


class Course:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def ensure(self, simulator="llm", hardware="standin", provider=None, simulator_profile=None, simulator_url=None):
        """Create the directory and its settings. An existing course keeps the settings it was made with."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "course.json"
        if path.exists():
            return load(path)
        settings = {"schema": "mlab.v1", "simulator": simulator, "hardware": hardware, "provider": provider,
                    "simulator_profile": simulator_profile, "simulator_url": simulator_url}
        atomic(path, settings)
        return settings

    def settings(self):
        return load(self.root / "course.json")

    def tasks(self):
        """The tasks a session can start, with their titles."""
        return [{"task": task, "title": load(ROOT / "tasks" / name / "task.yaml")["title"]} for task, name in TASKS.items()]

    def sessions(self):
        """Every session, oldest first: id (also its folder), task, number within the task, root and creation time."""
        found = []
        for path in self.root.iterdir() if self.root.is_dir() else []:
            match = NAME.match(path.name)
            config = path / ".mlab/config.json"
            if match and config.is_file():
                data = load(config)
                found.append({"id": path.name, "task": data["task"], "number": int(match.group(2)), "root": path,
                              "created_at": data.get("created_at") or ""})
        return sorted(found, key=lambda s: (s["created_at"], s["task"], s["number"]))

    def create(self, task):
        """A new session: a fresh workspace for the task, named 9.2-1, 9.2-2, …"""
        require(task in TASKS, "E_SCHEMA", f"unknown task {task}; one of {', '.join(TASKS)}", 2)
        settings = self.settings()
        number = 1 + max((s["number"] for s in self.sessions() if s["task"] == task), default=0)
        name = f"{task}-{number}"
        if (self.root / name).exists():
            raise LabError("E_SCHEMA", f"{name} already exists", 2)
        # The profile's model and endpoint go into settings.local.json; an API key never does.
        initialize(self.root / name, task, settings["simulator"], settings.get("provider"), settings["hardware"],
                   simulator_profile=settings.get("simulator_profile"), simulator_url=settings.get("simulator_url"))
        return name
