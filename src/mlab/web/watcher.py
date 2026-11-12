"""Polls each task workspace about once a second and turns what changed into page events.

It reads through a read-only Lab only. Runs are started by the agent's mlab commands and finish in their own worker
processes, so the ledger and the files are the only source of truth; nothing here starts or finalizes a run.
"""
import asyncio
import os
from ..core.io import TERMINAL
from ..core.workspace import Lab
from . import views

LABELS = {("workflow", "submitted"): "Simulation", ("workflow", "decided"): "Validation", ("measurement", "draft"): "Predictions for the hardware points",
          ("measurement", "submitted"): "Measurement"}


def signature(root, folders):
    """Modification times of the files the agent and the student edit in the current round and measurement."""
    found = []
    for folder in folders:
        for directory in (folder, folder / "candidates"):
            try:
                with os.scandir(directory) as entries:
                    found += [(entry.path, entry.stat().st_mtime_ns, entry.stat().st_size) for entry in entries if entry.is_file()]
            except OSError:
                continue
    return sorted(found)


def look(runtime):
    lab = Lab(runtime.root, readonly=True)
    try:
        state = views.task_state(lab, runtime.status)
        if runtime.exploration:
            state["runs_in_flight"] = [{"id": run["id"], "role": run.get("role", "simulator"), "candidate": run["candidate"],
                                        "status": run["status"], "phase": (run.get("progress") or {}).get("phase")}
                                       for run in lab.store.all("run") if run["status"] not in TERMINAL]
            state = runtime.present_state(state)
        owners, folders = {}, []
        for kind in ("workflow", "measurement"):
            records = lab.store.all(kind) if runtime.exploration else [lab.store.get(kind, state[kind])] if state.get(kind) else []
            for owner in records:
                folders.append(lab.root / owner["folder"])
                runs = [lab.store.get("run", r) for r in owner["runs"]]
                owners[owner["id"]] = {"kind": kind, "status": owner["status"], "runs": len(runs),
                                       "done": bool(runs) and all(r["status"] in TERMINAL for r in runs)}
        return {"state": state, "owners": owners, "files": signature(lab.root, folders)}
    finally:
        lab.close()


class Watcher:
    def __init__(self, runtimes, bus, interval=1.0):
        self.runtimes, self.bus, self.interval = runtimes, bus, interval
        self.seen = {}
        self.wake = asyncio.Event()
        self.task = None

    def poke(self):
        self.wake.set()

    def start(self):
        self.task = asyncio.create_task(self.run(), name="watcher")

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run(self):
        while True:
            for runtime in list(self.runtimes.values()):  # A session may be added while this loop awaits.
                await self.check(runtime)
            try:
                await asyncio.wait_for(self.wake.wait(), self.interval)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def check(self, runtime):
        try:
            snapshot = await asyncio.to_thread(look, runtime)
        except Exception:  # noqa: BLE001 - a file in the middle of a write; the next tick reads it again
            return
        before = self.seen.get(runtime.key)
        self.seen[runtime.key] = snapshot
        runtime.state = snapshot["state"]
        if before is None:
            return
        state, old = snapshot["state"], before["state"]
        if state["ledger_seq"] != old["ledger_seq"] or snapshot["files"] != before["files"]:
            self.bus.publish("records.changed", runtime.key, seq=state["ledger_seq"],
                             workflow=state.get("workflow"), measurement=state.get("measurement"))
        if {k: v for k, v in state.items() if k != "ledger_seq"} != {k: v for k, v in old.items() if k != "ledger_seq"}:
            self.bus.publish("task.state", runtime.key, state=state)
        if state["runs_in_flight"] != old["runs_in_flight"]:
            self.bus.publish("runs.progress", runtime.key, runs=state["runs_in_flight"])
        for owner, info in snapshot["owners"].items():
            previous = before["owners"].get(owner)
            if info["done"] and previous and (not previous["done"] or info["runs"] > previous["runs"]):
                label = LABELS.get((info["kind"], info["status"]), "Runs")
                asyncio.create_task(runtime.runs_finished(owner, label))
