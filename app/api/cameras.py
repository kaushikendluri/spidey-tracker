"""Camera network endpoints.

There are no real camera feeds here. Every camera row carries `is_mock = 1`
and the API echoes a `feed_notice` so the UI can state plainly that the tiles
are simulated rather than live CCTV.
"""

import time

from flask import Blueprint, current_app, jsonify, request

from .. import db as dbmod
from .. import events

bp = Blueprint("cameras", __name__, url_prefix="/api/cameras")

FEED_NOTICE = ("Simulated feed. This system has no access to real CCTV, "
               "satellite or law-enforcement networks.")

VALID_STATUS = ("live", "offline", "analyzing", "detected", "error")


def _serialize(row, last_detection=None):
    d = dict(row)
    d["is_mock"] = bool(d.get("is_mock", 1))
    d["feed_notice"] = FEED_NOTICE if d["is_mock"] else None
    d["status_age_sec"] = max(0.0, time.time() - (d.get("last_status_at") or 0))
    d["last_detection"] = last_detection
    return d


@bp.get("")
def list_cameras():
    conn = dbmod.get_db()
    clauses, params = [], []
    city = request.args.get("city")
    if city and city != "all":
        clauses.append("c.city_id = ?")
        params.append(city)
    status = request.args.get("status")
    if status and status in VALID_STATUS:
        clauses.append("c.status = ?")
        params.append(status)
    location = request.args.get("location")
    if location:
        clauses.append("c.location_id = ?")
        params.append(location)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = dbmod.query(
        conn,
        "SELECT c.*, l.name AS location_name, ci.name AS city_name FROM cameras c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "JOIN cities ci ON ci.id = c.city_id" + where + " ORDER BY c.id",
        params,
    )

    ids = [r["id"] for r in rows]
    detections = {}
    if ids:
        det_rows = dbmod.query(
            conn,
            "SELECT d.camera_id, d.ts, d.confidence, d.summary, d.sighting_id, d.is_demo "
            "FROM camera_detections d JOIN ("
            "  SELECT camera_id, MAX(ts) AS mts FROM camera_detections "
            "  WHERE camera_id IN (%s) GROUP BY camera_id"
            ") m ON m.camera_id = d.camera_id AND m.mts = d.ts" % ",".join("?" * len(ids)),
            ids,
        )
        for row in det_rows:
            detections[row["camera_id"]] = {
                "ts": row["ts"], "confidence": row["confidence"],
                "summary": row["summary"], "sighting_id": row["sighting_id"],
                "is_demo": bool(row["is_demo"]),
                "age_sec": max(0.0, time.time() - row["ts"]),
            }

    cameras = [_serialize(r, detections.get(r["id"])) for r in rows]
    counts = {}
    for cam in cameras:
        counts[cam["status"]] = counts.get(cam["status"], 0) + 1

    return jsonify({
        "cameras": cameras,
        "counts": counts,
        "total": len(cameras),
        "feed_notice": FEED_NOTICE,
    })


@bp.get("/<camera_id>")
def get_camera(camera_id):
    conn = dbmod.get_db()
    row = dbmod.query(
        conn,
        "SELECT c.*, l.name AS location_name, ci.name AS city_name FROM cameras c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "JOIN cities ci ON ci.id = c.city_id WHERE c.id = ?",
        (camera_id,),
        one=True,
    )
    if not row:
        return jsonify({"error": "Camera %s not found." % camera_id}), 404

    detections = dbmod.query(
        conn,
        "SELECT d.*, s.ref, s.area FROM camera_detections d "
        "LEFT JOIN sightings s ON s.id = d.sighting_id "
        "WHERE d.camera_id = ? ORDER BY d.ts DESC LIMIT 20",
        (camera_id,),
    )
    payload = _serialize(row)
    payload["detections"] = [dict(d) for d in detections]
    if detections:
        first = detections[0]
        payload["last_detection"] = {
            "ts": first["ts"], "confidence": first["confidence"],
            "summary": first["summary"], "sighting_id": first["sighting_id"],
            "is_demo": bool(first["is_demo"]),
            "age_sec": max(0.0, time.time() - first["ts"]),
        }
    return jsonify(payload)


@bp.patch("/<camera_id>")
def update_camera(camera_id):
    """Operator override of camera status."""
    conn = dbmod.get_db()
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in VALID_STATUS:
        return jsonify({"error": "status must be one of %s" % ", ".join(VALID_STATUS),
                        "field": "status"}), 400

    row = dbmod.query(conn, "SELECT status, label FROM cameras WHERE id = ?",
                      (camera_id,), one=True)
    if not row:
        return jsonify({"error": "Camera %s not found." % camera_id}), 404

    now = time.time()
    dbmod.execute(conn, "UPDATE cameras SET status = ?, last_status_at = ? WHERE id = ?",
                  (status, now, camera_id))
    events.publish("camera.status_changed", {
        "camera_id": camera_id, "label": row["label"], "status": status,
        "previous": row["status"], "ts": now, "is_demo": False,
    })
    return jsonify({"camera_id": camera_id, "status": status, "ts": now})
