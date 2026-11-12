"""SoCLaaS defaults and a process-shared inference lease.

The lease covers inference, not time spent reviewing a page or running a lab tool.
All lab processes for this OS user share it, including the mock estimator. No
credential, prompt or response is written into the lock file.
"""
import asyncio
import fcntl
import os
import tempfile
import time
from pathlib import Path

MODEL = "qwen3.8:27b"
BASE_URL = "https://soclaas-api.comp.nus.edu.sg/v1"


class InferenceCancelled(Exception):
    pass


class InferenceLease:
    def __init__(self, path=None, interval=1.0):
        self.path = Path(path) if path else Path(tempfile.gettempdir()) / f"mlab-soclaas-{os.getuid()}" / "inference.lock"
        self.interval = interval
        self.stream = None
        self.cancelled = False

    async def acquire(self):
        if self.stream:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stream = self.path.open("a+")
        try:
            while True:
                if self.cancelled:
                    raise InferenceCancelled()
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(.1)
            stream.seek(0)
            previous = float(stream.read() or 0)
            while time.time() - previous < self.interval:
                if self.cancelled:
                    raise InferenceCancelled()
                await asyncio.sleep(.1)
            stream.seek(0)
            stream.truncate()
            stream.write(str(time.time()))
            stream.flush()
            self.stream = stream
        except BaseException:
            stream.close()
            raise

    def release(self):
        if self.stream:
            self.stream.close()
            self.stream = None
