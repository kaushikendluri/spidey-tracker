"""Global search across sightings, cameras, areas, cities and predictions."""

import time

from flask import Blueprint, current_app, jsonify, request

from .. import db as dbmod
from ..services import prediction

bp = Blueprint("search", __name__, url_prefix="/api/search")


@bp.get("")
def search():
    term = (request.args.get("q") or "").strip()
    city = request.args.get("city")
    if len(term) < 2:
        return jsonify({"query": term, "groups": [], "total": 0})

    conn = dbmod.get_db()
    like = "%" + term + "%"
    groups = []

    city_clause, city_params = ("", [])
    if city and city != "all":
        city_clause, city_params = (" AND city_id = ?", [city])

    rows = dbmod.query(
        conn,
        "SELECT id, ref, area, latitude, longitude, ts, confidence, status, city_id "
        "FROM sightings WHERE (ref LIKE ? OR area LIKE ? OR description LIKE ?)"
        + city_clause + " ORDER BY ts DESC LIMIT 8",
        [like, like, like] + city_params,
    )
    if rows:
        groups.append({
            "kind": "sightings", "label": "SIGHTINGS",
            "items": [{
                "id": r["id"], "title": r["ref"], "subtitle": r["area"],
                "meta": "%.0f%% / %s" % (r["confidence"], r["status"].upper()),
                "latitude": r["latitude"], "longitude": r["longitude"],
                "city_id": r["city_id"],
                "action": {"type": "sighting.open", "sighting_id": r["id"]},
            } for r in rows],
        })

    rows = dbmod.query(
        conn,
        "SELECT c.id, c.label, c.status, c.latitude, c.longitude, c.city_id, "
        "l.name AS location_name FROM cameras c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE (c.id LIKE ? OR c.label LIKE ? OR l.name LIKE ?) LIMIT 6",
        [like, like, like],
    )
    if rows:
        groups.append({
            "kind": "cameras", "label": "CAMERAS",
            "items": [{
                "id": r["id"], "title": r["id"], "subtitle": r["label"],
                "meta": r["status"].upper(),
                "latitude": r["latitude"], "longitude": r["longitude"],
                "city_id": r["city_id"],
                "action": {"type": "camera.open", "camera_id": r["id"]},
            } for r in rows],
        })

    rows = dbmod.query(
        conn,
        "SELECT l.id, l.name, l.latitude, l.longitude, l.city_id, c.name AS city_name, "
        "(SELECT COUNT(*) FROM sightings s WHERE s.location_id = l.id) AS count "
        "FROM locations l JOIN cities c ON c.id = l.city_id "
        "WHERE l.name LIKE ? ORDER BY count DESC LIMIT 6",
        [like],
    )
    if rows:
        groups.append({
            "kind": "areas", "label": "AREAS",
            "items": [{
                "id": r["id"], "title": r["name"], "subtitle": r["city_name"],
                "meta": "%d records" % r["count"],
                "latitude": r["latitude"], "longitude": r["longitude"],
                "city_id": r["city_id"],
                "action": {"type": "map.focus", "latitude": r["latitude"],
                           "longitude": r["longitude"], "zoom": 14},
            } for r in rows],
        })

    rows = dbmod.query(
        conn,
        "SELECT id, name, country, latitude, longitude, default_zoom FROM cities "
        "WHERE name LIKE ? OR country LIKE ? LIMIT 5",
        [like, like],
    )
    if rows:
        groups.append({
            "kind": "cities", "label": "CITIES",
            "items": [{
                "id": r["id"], "title": r["name"], "subtitle": r["country"],
                "meta": "SWITCH CITY",
                "latitude": r["latitude"], "longitude": r["longitude"],
                "city_id": r["id"],
                "action": {"type": "city.set", "city_id": r["id"]},
            } for r in rows],
        })

    # Prediction candidates matching the term, for the active city only.
    target_city = city if city and city != "all" else "nyc"
    payload = prediction.current(current_app._get_current_object(), target_city)
    matches = [c for c in payload.get("candidates", [])
               if term.lower() in c["name"].lower()]
    if matches:
        groups.append({
            "kind": "predictions", "label": "PREDICTIONS",
            "items": [{
                "id": c["location_id"], "title": c["name"],
                "subtitle": "PREDICTED %.0f%%" % c["probability"],
                "meta": "ETA %.0f MIN" % c["eta_min"],
                "latitude": c["latitude"], "longitude": c["longitude"],
                "city_id": target_city,
                "action": {"type": "panel.open", "panel": "prediction"},
            } for c in matches],
        })

    return jsonify({
        "query": term,
        "groups": groups,
        "total": sum(len(g["items"]) for g in groups),
        "generated_at": time.time(),
    })
