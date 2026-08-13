"""Global network + city/location reference endpoints."""

import time

from flask import Blueprint, current_app, jsonify

from .. import db as dbmod
from .. import seed

bp = Blueprint("network", __name__, url_prefix="/api")


@bp.get("/network")
def get_network():
    """Per-city tallies for the global network panel.

    Counters are recomputed on read when the cache is older than 20s, which is
    cheap at this data volume and keeps the panel honest after bulk changes.
    """
    conn = dbmod.get_db()
    stale = dbmod.query(
        conn, "SELECT MIN(updated_at) AS oldest FROM network_stats", one=True
    )
    if not stale or not stale["oldest"] or time.time() - stale["oldest"] > 20:
        seed.refresh_network_stats(current_app._get_current_object())

    rows = dbmod.query(
        conn,
        "SELECT c.id, c.name, c.country, c.latitude, c.longitude, c.default_zoom, "
        "c.is_primary, COALESCE(n.total,0) AS total, COALESCE(n.last_24h,0) AS last_24h, "
        "COALESCE(n.high_confidence,0) AS high_confidence, n.last_sighting_ts "
        "FROM cities c LEFT JOIN network_stats n ON n.city_id = c.id "
        "ORDER BY total DESC, c.name",
    )
    cities = []
    for row in rows:
        d = dict(row)
        d["is_primary"] = bool(d["is_primary"])
        d["last_sighting_age_sec"] = (
            max(0.0, time.time() - d["last_sighting_ts"]) if d["last_sighting_ts"] else None
        )
        cities.append(d)

    return jsonify({
        "cities": cities,
        "grand_total": sum(c["total"] for c in cities),
        "total_24h": sum(c["last_24h"] for c in cities),
        "generated_at": time.time(),
    })


@bp.get("/cities")
def get_cities():
    conn = dbmod.get_db()
    rows = dbmod.query(conn, "SELECT * FROM cities ORDER BY is_primary DESC, name")
    return jsonify({"cities": [dict(r) for r in rows]})


@bp.get("/locations")
def get_locations():
    from flask import request
    conn = dbmod.get_db()
    city = request.args.get("city")
    if city and city != "all":
        rows = dbmod.query(
            conn, "SELECT * FROM locations WHERE city_id = ? ORDER BY name", (city,)
        )
    else:
        rows = dbmod.query(conn, "SELECT * FROM locations ORDER BY city_id, name")
    return jsonify({"locations": [dict(r) for r in rows]})


@bp.get("/alerts")
def get_alerts():
    from flask import request
    conn = dbmod.get_db()
    only_open = request.args.get("open") in ("1", "true")
    where = " WHERE a.acknowledged = 0" if only_open else ""
    rows = dbmod.query(
        conn,
        "SELECT a.*, s.ref, s.area, s.confidence, s.latitude, s.longitude "
        "FROM alerts a LEFT JOIN sightings s ON s.id = a.sighting_id" + where +
        " ORDER BY a.created_at DESC LIMIT 50",
    )
    alerts = []
    for row in rows:
        d = dict(row)
        d["acknowledged"] = bool(d["acknowledged"])
        d["is_demo"] = bool(d["is_demo"])
        d["age_sec"] = max(0.0, time.time() - d["created_at"])
        alerts.append(d)
    return jsonify({"alerts": alerts})


@bp.post("/alerts/<int:alert_id>/ack")
def ack_alert(alert_id):
    conn = dbmod.get_db()
    _, count = dbmod.execute(
        conn, "UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,)
    )
    if not count:
        return jsonify({"error": "Alert %d not found." % alert_id}), 404
    return jsonify({"acknowledged": alert_id})
