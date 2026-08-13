"""System status and demo-mode control.

Status is measured, never asserted: the database check runs a real query, the
websocket/SSE check counts live subscribers, the AI check reports which engine
is actually configured. A subsystem that is down reports as down.
"""

import time

from flask import Blueprint, current_app, jsonify, request

from .. import db as dbmod
from .. import events
from ..services import ai_analysis, ned

bp = Blueprint("system", __name__, url_prefix="/api/system")

BOOT_TIME = time.time()


def _database_status():
    started = time.time()
    try:
        conn = dbmod.get_db()
        row = dbmod.query(conn, "SELECT COUNT(*) AS c FROM sightings", one=True)
        return {
            "id": "database", "label": "DATABASE", "ok": True, "state": "CONNECTED",
            "detail": "%d sighting records" % row["c"],
            "latency_ms": round((time.time() - started) * 1000, 1),
        }
    except Exception as exc:
        return {"id": "database", "label": "DATABASE", "ok": False, "state": "ERROR",
                "detail": str(exc)[:160], "latency_ms": None}


def _stream_status():
    count = events.subscriber_count()
    return {
        "id": "stream", "label": "EVENT STREAM", "ok": count > 0,
        "state": "CONNECTED" if count > 0 else "NO CLIENTS",
        "detail": "%d subscriber%s" % (count, "" if count == 1 else "s"),
        "subscribers": count,
    }


def _ai_status(app):
    info = ned.engine_status(app)
    return {
        "id": "ai", "label": "AI ENGINE", "ok": info["ready"],
        "state": "READY" if info["ready"] else "OFFLINE",
        "detail": info["label"],
        "is_real_model": info["is_real_model"],
    }


def _camera_status():
    try:
        conn = dbmod.get_db()
        rows = dbmod.query(conn, "SELECT status, COUNT(*) AS c FROM cameras GROUP BY status")
        counts = {r["status"]: r["c"] for r in rows}
        total = sum(counts.values())
        online = total - counts.get("offline", 0) - counts.get("error", 0)
        return {
            "id": "cameras", "label": "CAMERA NETWORK",
            "ok": total > 0 and online > 0,
            "state": "ONLINE" if online > 0 else "OFFLINE",
            "detail": "%d/%d reporting" % (online, total),
            "counts": counts,
        }
    except Exception as exc:
        return {"id": "cameras", "label": "CAMERA NETWORK", "ok": False,
                "state": "ERROR", "detail": str(exc)[:160]}


def _prediction_status(app):
    try:
        conn = dbmod.get_db()
        row = dbmod.query(
            conn, "SELECT COUNT(*) AS c, MAX(created_at) AS last FROM predictions "
                  "WHERE is_current = 1", one=True
        )
        return {
            "id": "prediction", "label": "PREDICTION ENGINE", "ok": True,
            "state": "READY",
            "detail": "%d city model%s" % (row["c"], "" if row["c"] == 1 else "s"),
            "last_run_age_sec": (time.time() - row["last"]) if row["last"] else None,
        }
    except Exception as exc:
        return {"id": "prediction", "label": "PREDICTION ENGINE", "ok": False,
                "state": "ERROR", "detail": str(exc)[:160]}


@bp.get("/status")
def status():
    app = current_app._get_current_object()
    sim = app.extensions.get("simulator")

    subsystems = [
        _database_status(),
        _stream_status(),
        _ai_status(app),
        _camera_status(),
        _prediction_status(app),
    ]

    demo_enabled = bool(sim and sim.enabled)
    subsystems.append({
        "id": "demo", "label": "DEMO NETWORK",
        "ok": True,
        "state": "SIMULATING" if demo_enabled else "OFF",
        "detail": ("Synthetic events every %d-%ds" % (app.config["DEMO_MIN_INTERVAL"],
                                                      app.config["DEMO_MAX_INTERVAL"]))
                  if demo_enabled else "Live data only",
        "is_demo": demo_enabled,
    })

    healthy = all(s["ok"] for s in subsystems if s["id"] in ("database", "ai", "prediction"))

    return jsonify({
        "version": app.config["VERSION"],
        "online": healthy,
        "state": "SYSTEM ONLINE" if healthy else "SYSTEM DEGRADED",
        "uptime_sec": round(time.time() - BOOT_TIME, 1),
        "server_time": time.time(),
        "subsystems": subsystems,
        "demo_mode": demo_enabled,
        "simulator": sim.status() if sim else None,
        "ai": ned.engine_status(app),
        "image_analysis_is_real_model": ai_analysis.image_model_available(app),
        "disclaimer": (
            "Fictional demo platform. No connection to real CCTV, satellite, "
            "law-enforcement or person-tracking systems."
        ),
    })


@bp.get("/demo")
def get_demo():
    sim = current_app.extensions.get("simulator")
    return jsonify(sim.status() if sim else {"enabled": False, "running": False})


@bp.post("/demo")
def set_demo():
    sim = current_app.extensions.get("simulator")
    if not sim:
        return jsonify({"error": "Simulator is not running."}), 503
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if enabled is None:
        enabled = not sim.enabled
    sim.set_enabled(bool(enabled))
    return jsonify(sim.status())


@bp.get("/settings")
def get_settings():
    conn = dbmod.get_db()
    rows = dbmod.query(conn, "SELECT key, value FROM settings")
    return jsonify({r["key"]: r["value"] for r in rows})


@bp.post("/settings")
def save_settings():
    conn = dbmod.get_db()
    payload = request.get_json(silent=True) or {}
    allowed = {"boot_sequence", "sound", "reduced_motion", "demo_mode", "map_mode"}
    saved = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        dbmod.set_setting(conn, key, value)
        saved[key] = str(value)
    return jsonify(saved)
