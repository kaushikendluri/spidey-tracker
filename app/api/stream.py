"""Server-Sent Events endpoint.

One long-lived response per browser tab. Keepalive comments stop proxies from
closing an idle connection, and the client reconnects automatically because
EventSource does that natively.
"""

import json
import time

from flask import Blueprint, Response, current_app, stream_with_context

from .. import events

bp = Blueprint("stream", __name__, url_prefix="/api")


@bp.get("/stream")
def stream():
    app = current_app._get_current_object()
    keepalive = app.config["SSE_KEEPALIVE"]

    def generate():
        sub = events.subscribe()
        try:
            hello = json.dumps({
                "id": 0, "type": "stream.connected", "ts": time.time(),
                "data": {"subscribers": events.subscriber_count(),
                         "version": app.config["VERSION"]},
            })
            yield events.format_sse(hello)

            # Tell the client how often to expect traffic so it can flag a
            # silent-but-open connection as stale.
            yield "retry: 3000\n\n"

            while True:
                payload = sub.get(timeout=keepalive)
                if payload is None:
                    yield events.keepalive()
                else:
                    yield events.format_sse(payload)
        finally:
            events.unsubscribe(sub)
            events.publish("system.status_changed",
                           {"subscribers": events.subscriber_count()})

    response = Response(stream_with_context(generate()),
                        mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"   # disable nginx buffering
    response.headers["Connection"] = "keep-alive"
    return response
