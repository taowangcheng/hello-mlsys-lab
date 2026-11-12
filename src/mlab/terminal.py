"""Terminal chat using the same isolated Codex session and lab tools as the page."""
import argparse
import asyncio
import sys
from pathlib import Path

from .core.io import LabError, load
from .core.workspace import initialize
from .web.agent import TaskRuntime
from .web.bus import Bus
from .web.codex_client import factory


async def read_input(prompt):
    """Wait for terminal input without leaving an input thread blocked after Ctrl+C."""
    print(prompt, end="", flush=True)
    loop = asyncio.get_running_loop()
    answer = loop.create_future()
    def ready():
        line = sys.stdin.readline()
        if not answer.done():
            if line:
                answer.set_result(line.rstrip("\r\n"))
            else:
                answer.set_exception(EOFError())
    loop.add_reader(sys.stdin.fileno(), ready)
    try:
        return await answer
    finally:
        loop.remove_reader(sys.stdin.fileno())


async def chat(root):
    provider = load(root / ".mlab/config.json").get("provider")
    runtime = TaskRuntime(root.name, root, Bus(), factory(provider), opening=False, source="cli")
    seen = {entry["id"] for entry in runtime.chat.read(100)}
    print("Codex / GPT-6 Luna. Type /quit to save and exit, or /stop while an approval is pending.")
    try:
        while True:
            text = await read_input("\nYou> ")
            if text.strip() in {"/quit", "/exit"}:
                break
            if not text.strip():
                continue
            await runtime.say(text)
            while runtime.busy():
                for entry in runtime.chat.read(100):
                    if entry["id"] not in seen and entry["role"] in {"agent", "tool", "error"}:
                        print(f"[{entry['role']}] {entry['text']}", flush=True)
                    seen.add(entry["id"])
                for card in list(runtime.cards.values()):
                    if card["future"].done():
                        continue
                    stop = False
                    if card["kind"] == "approval":
                        summary = card["data"]["summary"]
                        print(summary["title"])
                        for label, value in summary.get("facts", []):
                            print(f"  {label}: {value}")
                        answer = await read_input("Confirm [y/N], or explain what to change: ")
                        stop = answer == "/stop"
                        runtime.resolve(card["id"], {"allow": answer.lower() in {"y", "yes"},
                                                    "reason": "" if answer.lower() in {"y", "yes"} else answer})
                    else:
                        answers = {}
                        for question in card["data"]["questions"]:
                            print(question["question"])
                            print(" / ".join(x["label"] for x in question.get("options", [])))
                            answers[question["question"]] = await read_input("> ")
                        stop = "/stop" in answers.values()
                        runtime.resolve(card["id"], {"answers": answers})
                    if stop:
                        await runtime.interrupt()
                await asyncio.sleep(.05)
            for entry in runtime.chat.read(100):
                if entry["id"] not in seen and entry["role"] in {"agent", "error"}:
                    print(f"[{entry['role']}] {entry['text']}", flush=True)
                seen.add(entry["id"])
    finally:
        await runtime.interrupt()
        await runtime.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--task", choices=["9.1", "9.2", "9.3"], default="9.2")
    parser.add_argument("--backend", choices=["fixture", "llm"], default="fixture")
    args = parser.parse_args()
    root = args.workspace.expanduser().resolve()
    if not root.exists():
        initialize(root, args.task, args.backend, "codex-luna")
    try:
        asyncio.run(chat(root))
    except (EOFError, KeyboardInterrupt):
        print("\nSaved. Start the same workspace to continue.")
    except (LabError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
