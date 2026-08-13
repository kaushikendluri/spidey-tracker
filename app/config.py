"""Central configuration for Spidey Tracker.

Every tunable lives here so that no magic numbers hide in the services.
Environment variables override defaults, so deployment does not require code edits.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _flag(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _num(name, default, cast=float):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


class Config:
    BASE_DIR = BASE_DIR
    VERSION = "4.7.2"

    SECRET_KEY = os.environ.get("SPIDEY_SECRET_KEY", "spidey-tracker-dev-key")

    DATABASE = os.environ.get("SPIDEY_DB", os.path.join(BASE_DIR, "data", "spidey.db"))
    UPLOAD_DIR = os.environ.get("SPIDEY_UPLOADS", os.path.join(BASE_DIR, "data", "uploads"))
    MAX_CONTENT_LENGTH = int(_num("SPIDEY_MAX_UPLOAD_MB", 16, int)) * 1024 * 1024

    # --- Demo mode -------------------------------------------------------
    # The simulator invents sightings so the dashboard is alive with no real
    # data. Everything it produces is tagged `is_demo = 1` in the database and
    # rendered with a DEMO badge in the UI. See app/services/simulator.py.
    DEMO_MODE_DEFAULT = _flag("SPIDEY_DEMO", True)
    DEMO_MIN_INTERVAL = _num("SPIDEY_DEMO_MIN", 15.0)
    DEMO_MAX_INTERVAL = _num("SPIDEY_DEMO_MAX", 45.0)

    # --- AI --------------------------------------------------------------
    # When ANTHROPIC_API_KEY is present NED talks to the real Claude API with
    # tool calling. Otherwise a deterministic local intent engine answers from
    # live application state and the UI labels the source honestly.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ANTHROPIC_MODEL = os.environ.get("SPIDEY_AI_MODEL", "claude-sonnet-5")
    ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    AI_TIMEOUT = _num("SPIDEY_AI_TIMEOUT", 30.0)

    # Image analysis is a heuristic colour/pattern scorer, not a trained
    # detector. `image_model_available` stays False until a real model is wired
    # into app/services/ai_analysis.py, and the UI shows HEURISTIC accordingly.
    VISION_MODEL_PATH = os.environ.get("SPIDEY_VISION_MODEL", "").strip()

    # --- Prediction ------------------------------------------------------
    PREDICTION_MIN_SIGHTINGS = int(_num("SPIDEY_PRED_MIN", 2, int))
    PREDICTION_HORIZON_MIN = _num("SPIDEY_PRED_HORIZON", 12.0)
    PREDICTION_LOOKBACK_MIN = _num("SPIDEY_PRED_LOOKBACK", 180.0)

    # --- Real time -------------------------------------------------------
    SSE_KEEPALIVE = _num("SPIDEY_SSE_KEEPALIVE", 15.0)
    SSE_QUEUE_SIZE = int(_num("SPIDEY_SSE_QUEUE", 256, int))

    # A sighting stops counting as "active" after this many minutes.
    ACTIVE_WINDOW_MIN = _num("SPIDEY_ACTIVE_WINDOW", 30.0)

    JSON_SORT_KEYS = False
