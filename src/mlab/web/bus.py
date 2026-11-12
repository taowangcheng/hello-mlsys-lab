"""Page events: a numbered ring buffer that the page polls with short requests.

Short requests work through a port forward (ssh -L, VS Code); a stream that never ends can be held back or block other
requests on some forwards. A page that falls too far behind is told to reload everything.
"""
import itertools
from collections import deque


class Bus:
    def __init__(self, size=4000):
        self.events = deque(maxlen=size)
        self.counter = itertools.count(1)
        self.last = 0

    def publish(self, kind, session=None, **data):
        event = {"id": next(self.counter), "type": kind, "session": session, "data": data}
        self.last = event["id"]
        self.events.append(event)
        return event

    def since(self, last):
        """Events after `last`, or None when the buffer no longer reaches back that far."""
        if last >= self.last:
            return []
        if not self.events or last < self.events[0]["id"] - 1:
            return None
        return [event for event in self.events if event["id"] > last]
