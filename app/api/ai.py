"""NED AI and image-analysis endpoints."""

import os
import uuid

from flask import Blueprint, current_app, jsonify, request, session

from .. import db as dbmod
from ..services import ai_analysis, ned

bp = Blueprint("ai", __name__, url_prefix="/api/ai")


def _session_id():
    if "ned_session" not in session:
        session["ned_session"] = uuid.uuid4().hex
    return session["ned_session"]


@bp.get("/status")
def status():
    app = current_app._get_current_object()
    info = ned.engine_status(app)
    info["image_analysis"] = {
        "engine": "model" if ai_analysis.image_model_available(app) else "heuristic",
        "is_real_model": ai_analysis.image_model_available(app),
        "label": ("MODEL ANALYSIS" if ai_analysis.image_model_available(app)
                  else "HEURISTIC ANALYSIS"),
        "note": ("Colour-palette, saturation, contrast and edge measurement plus "
                 "motion and location correlation. Not a trained detector."),
    }
    return jsonify(info)


@bp.get("/chat")
def chat_history():
    conn = dbmod.get_db()
    return jsonify({"messages": ned.history(conn, _session_id()),
                    "session_id": _session_id()})


@bp.post("/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required.", "field": "message"}), 400
    if len(message) > 2000:
        return jsonify({"error": "Message too long (2000 char limit).",
                        "field": "message"}), 400

    conn = dbmod.get_db()
    result = ned.ask(
        current_app._get_current_object(), conn, _session_id(),
        message, payload.get("context") or {},
    )
    return jsonify(result)


@bp.delete("/chat")
def clear_chat():
    conn = dbmod.get_db()
    dbmod.execute(conn, "DELETE FROM ai_messages WHERE session_id = ?", (_session_id(),))
    return jsonify({"cleared": True})


@bp.post("/analyze")
def analyze():
    """Analyse an uploaded image without creating a sighting.

    Used by the report form to preview the confidence a photo would produce.
    """
    conn = dbmod.get_db()
    app = current_app._get_current_object()

    image_path = None
    temp = False
    if request.files.get("image"):
        upload = request.files["image"]
        ext = os.path.splitext(upload.filename or "")[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            return jsonify({"error": "Unsupported image type.", "field": "image"}), 400
        os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
        image_path = os.path.join(app.config["UPLOAD_DIR"],
                                  "preview-%s%s" % (uuid.uuid4().hex, ext))
        upload.save(image_path)
        temp = True

    form = request.form if request.form else (request.get_json(silent=True) or {})
    try:
        payload = {
            "city_id": form.get("city_id") or "nyc",
            "latitude": float(form.get("latitude")),
            "longitude": float(form.get("longitude")),
            "ts": float(form.get("ts")) if form.get("ts") else None,
            "source": form.get("source") or "citizen",
            "direction": float(form["direction"]) if form.get("direction") else None,
            "speed_kmh": float(form["speed_kmh"]) if form.get("speed_kmh") else None,
            "image_path": image_path,
        }
    except (TypeError, ValueError):
        if temp and image_path:
            _cleanup(image_path)
        return jsonify({"error": "latitude and longitude are required numbers.",
                        "field": "latitude"}), 400

    import time as _time
    if payload["ts"] is None:
        payload["ts"] = _time.time()

    try:
        result = ai_analysis.analyze(app, conn, payload)
    finally:
        if temp and image_path:
            _cleanup(image_path)

    result.pop("measurements", None)
    return jsonify(result)


def _cleanup(path):
    try:
        os.remove(path)
    except OSError:
        pass
