"""In-process pub/sub feeding the browser over Server-Sent Events.

Publishers (API handlers, the demo simulator) call `publish(type, data)`.
Every connected browser holds one subscriber queue; slow clients drop their
oldest events rather than blocking the publisher.

Event names mirror the domain vocabulary:
    sighting.created / sighting.updated / sighting.deleted
    prediction.updated
    camera.detected / camera.status_changed
    alert.created
    network.updated
    system.status_changed
"""

import json
import queue
import threading
import time
import itertools

_subscribers = set()
_lock = threading.Lock()
_seq = itertools.count(1)

MAX_QUEUE = 256


class Subscriber(object):
    def __init__(self, maxsize=MAX_QUEUE):
        self.queue = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, payload):
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            # Drop the oldest so a stalled tab cannot wedge the publisher.
            try:
                self.queue.get_nowait()
                self.dropped += 1
                self.queue.put_nowait(payload)
            except queue.Empty:
                pass

    def get(self, timeout):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None


def subscribe():
    sub = Subscriber()
    with _lock:
        _subscribers.add(sub)
    return sub


def unsubscribe(sub):
    with _lock:
        _subscribers.discard(sub)


def subscriber_count():
    with _lock:
        return len(_subscribers)


def publish(event_type, data=None):
    payload = {
        "id": next(_seq),
        "type": event_type,
        "ts": time.time(),
        "data": data if data is not None else {},
    }
    encoded = json.dumps(payload, default=str)
    with _lock:
        targets = list(_subscribers)
    for sub in targets:
        sub.put(encoded)
    return payload


def format_sse(encoded):
    """Wire format. `event:` lets EventSource dispatch by name client-side."""
    try:
        name = json.loads(encoded).get("type", "message")
    except (ValueError, TypeError):
        name = "message"
    return "event: %s\ndata: %s\n\n" % (name, encoded)


def keepalive():
    return ": keepalive %d\n\n" % int(time.time())
