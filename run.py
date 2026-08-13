#!/usr/bin/env python3
"""Development entrypoint.

    python3 run.py                 # http://127.0.0.1:5000
    SPIDEY_DEMO=0 python3 run.py   # start with the demo network off
    PORT=8080 python3 run.py

Threaded mode is required: each browser tab holds an SSE connection open, and
the demo simulator runs on its own thread.
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    debug = os.environ.get("SPIDEY_DEBUG", "0").lower() in ("1", "true", "yes")
    # The reloader would start a second simulator thread and duplicate events.
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)
