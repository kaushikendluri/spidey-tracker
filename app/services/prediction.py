"""Movement prediction.

Everything here is computed from rows in the database - there are no fixed
probabilities anywhere in this module. The pipeline is:

    recent sightings
        -> recency-weighted movement vector (circular mean bearing + speed)
        -> dead-reckoned projection at the prediction horizon
        -> per-district scoring: heading alignment x projection proximity
           x baseline activity bias x recent local activity
        -> softmax-ish normalisation into probabilities
        -> route polyline, uncertainty zone, ETA band

When there is too little movement data the engine returns a low-confidence
result flagged `sparse`, rather than inventing a path.
"""

import json
import math
import time

from .. import db as dbmod
from .. import events
from . import geo


def _recent(conn, city_id, lookback_min):
    return dbmod.query(
        conn,
        "SELECT id, ref, latitude, longitude, ts, direction, speed_kmh, confidence, area "
        "FROM sightings WHERE city_id = ? AND ts >= ? AND status != 'dismissed' "
        "ORDER BY ts DESC LIMIT 40",
        (city_id, time.time() - lookback_min * 60),
    )


def movement_vector(rows):
    """Recency-weighted heading and speed from consecutive sighting legs.

    Legs shorter than 40 m are skipped: their bearing is dominated by GPS
    noise rather than actual direction of travel.
    """
    if len(rows) < 2:
        return None

    ordered = sorted(rows, key=lambda r: r["ts"])
    now = time.time()
    bearings, weights, speeds = [], [], []

    for prev, cur in zip(ordered, ordered[1:]):
        dist = geo.haversine_km(prev["latitude"], prev["longitude"],
                                cur["latitude"], cur["longitude"])
        dt_h = (cur["ts"] - prev["ts"]) / 3600.0
        if dist < 0.04 or dt_h <= 0:
            continue
        bearing = geo.bearing_deg(prev["latitude"], prev["longitude"],
                                  cur["latitude"], cur["longitude"])
        age_min = max(0.0, (now - cur["ts"]) / 60.0)
        # Half-life of 20 minutes, scaled by how much we trust the sighting.
        weight = math.exp(-age_min / 20.0) * (cur["confidence"] / 100.0 + 0.15)
        bearings.append(bearing)
        weights.append(weight)
        speeds.append(min(140.0, dist / dt_h))

    if not bearings:
        return None

    heading = geo.mean_bearing(bearings, weights)
    if heading is None:
        return None

    wsum = sum(weights)
    speed = sum(s * w for s, w in zip(speeds, weights)) / wsum

    # Circular concentration: 1.0 = every leg agreed, 0.0 = no coherence.
    cx = sum(w * math.cos(math.radians(b)) for b, w in zip(bearings, weights)) / wsum
    cy = sum(w * math.sin(math.radians(b)) for b, w in zip(bearings, weights)) / wsum
    coherence = min(1.0, math.hypot(cx, cy))

    return {
        "heading": round(heading, 1),
        "compass": geo.compass_label(heading),
        "speed_kmh": round(speed, 1),
        "coherence": round(coherence, 3),
        "legs": len(bearings),
    }


def compute(app, city_id):
    """Build a prediction payload for one city. Pure read - no writes."""
    lookback = app.config["PREDICTION_LOOKBACK_MIN"]
    horizon = app.config["PREDICTION_HORIZON_MIN"]

    with dbmod.connection(app.config["DATABASE"]) as conn:
        rows = _recent(conn, city_id, lookback)
        districts = dbmod.query(
            conn,
            "SELECT id, name, latitude, longitude, radius_km, activity_bias "
            "FROM locations WHERE city_id = ?",
            (city_id,),
        )
        if not rows or not districts:
            return _empty(city_id, "no recent activity")

        latest = rows[0]
        vector = movement_vector(rows)

        # Recent activity per district feeds the prior alongside the static bias.
        window_start = time.time() - lookback * 60
        local_counts = {}
        for row in rows:
            for d in districts:
                if geo.haversine_km(row["latitude"], row["longitude"],
                                    d["latitude"], d["longitude"]) <= d["radius_km"]:
                    local_counts[d["id"]] = local_counts.get(d["id"], 0.0) + (
                        row["confidence"] / 100.0
                    )
                    break
        max_local = max(local_counts.values()) if local_counts else 1.0

        hour_bias = _hour_bias(conn, city_id)

        if vector:
            travel_km = max(0.35, vector["speed_kmh"] * (horizon / 60.0))
            proj_lat, proj_lon = geo.destination(
                latest["latitude"], latest["longitude"], vector["heading"], travel_km
            )
            # Wider cone when legs disagree.
            uncertainty_km = max(0.6, travel_km * (1.35 - vector["coherence"]))
        else:
            travel_km = 1.0
            proj_lat, proj_lon = latest["latitude"], latest["longitude"]
            uncertainty_km = 2.5

        scored = []
        for d in districts:
            dist_from_proj = geo.haversine_km(proj_lat, proj_lon, d["latitude"], d["longitude"])
            dist_from_last = geo.haversine_km(latest["latitude"], latest["longitude"],
                                              d["latitude"], d["longitude"])

            proximity = math.exp(-(dist_from_proj ** 2) / (2 * max(0.5, uncertainty_km) ** 2))

            if vector and dist_from_last > 0.15:
                b = geo.bearing_deg(latest["latitude"], latest["longitude"],
                                    d["latitude"], d["longitude"])
                alignment = 0.5 + 0.5 * math.cos(math.radians(
                    geo.angular_delta(b, vector["heading"])
                ))
                alignment = alignment ** (1.0 + 2.0 * vector["coherence"])
            else:
                alignment = 0.55

            recent = local_counts.get(d["id"], 0.0) / max_local if max_local else 0.0
            prior = 0.55 * d["activity_bias"] + 0.45 * recent
            hourly = hour_bias.get(d["id"], 0.5)

            score = (proximity ** 1.15) * (alignment ** 1.25) * (0.25 + prior) * (0.55 + hourly * 0.9)

            eta_min_km = max(0.0, dist_from_last)
            speed = vector["speed_kmh"] if vector else 24.0
            eta = (eta_min_km / max(6.0, speed)) * 60.0

            scored.append({
                "location_id": d["id"],
                "name": d["name"],
                "latitude": d["latitude"],
                "longitude": d["longitude"],
                "radius_km": d["radius_km"],
                "score": score,
                "distance_km": round(dist_from_last, 2),
                "eta_min": round(eta, 1),
            })

        total = sum(s["score"] for s in scored)
        if total <= 0:
            return _empty(city_id, "no directional signal")

        for s in scored:
            s["probability"] = round(s["score"] / total * 100.0, 1)
            s.pop("score")

        scored.sort(key=lambda s: s["probability"], reverse=True)
        top = scored[:6]

        lead = top[0]
        # Confidence blends how decisive the ranking is with how coherent the
        # movement was and how much data we had.
        margin = (top[0]["probability"] - (top[1]["probability"] if len(top) > 1 else 0)) / 100.0
        coherence = vector["coherence"] if vector else 0.15
        volume = min(1.0, len(rows) / 8.0)
        confidence = round(min(96.0, (0.42 * margin + 0.34 * coherence + 0.24 * volume) * 130.0), 1)

        etas = [s["eta_min"] for s in top[:3] if s["eta_min"] > 0] or [horizon]
        eta_lo = round(max(1.0, min(etas) * 0.8), 0)
        eta_hi = round(max(eta_lo + 2, max(etas) * 1.25), 0)

        route = _route(latest, lead, vector, proj_lat, proj_lon)

        payload = {
            "city_id": city_id,
            "generated_at": time.time(),
            "sparse": vector is None,
            "origin": {
                "sighting_id": latest["id"],
                "ref": latest["ref"],
                "latitude": latest["latitude"],
                "longitude": latest["longitude"],
                "ts": latest["ts"],
                "area": latest["area"],
            },
            "vector": vector,
            "projection": {
                "latitude": proj_lat,
                "longitude": proj_lon,
                "uncertainty_km": round(uncertainty_km, 2),
                "horizon_min": horizon,
            },
            "candidates": top,
            "route": route,
            "eta_min": eta_lo,
            "eta_max": eta_hi,
            "confidence": confidence,
            "samples": len(rows),
            "method": "recency-weighted dead reckoning + district prior",
        }
        return payload


def _route(latest, lead, vector, proj_lat, proj_lon):
    """Waypoints from the last sighting to the leading candidate.

    Bowed slightly toward the movement vector so the drawn path reads as travel
    rather than a ruler line between two dots.
    """
    start = (latest["latitude"], latest["longitude"])
    end = (lead["latitude"], lead["longitude"])
    points = [{"latitude": start[0], "longitude": start[1], "kind": "origin"}]

    steps = 5
    total = geo.haversine_km(start[0], start[1], end[0], end[1])
    for i in range(1, steps):
        t = i / float(steps)
        lat = start[0] + (end[0] - start[0]) * t
        lon = start[1] + (end[1] - start[1]) * t
        if vector and total > 0.2:
            bow = math.sin(t * math.pi) * min(0.35, total * 0.12)
            lat, lon = geo.destination(lat, lon, (vector["heading"] + 90) % 360, bow)
        points.append({"latitude": lat, "longitude": lon, "kind": "waypoint"})

    points.append({"latitude": proj_lat, "longitude": proj_lon, "kind": "projected"})
    points.append({"latitude": end[0], "longitude": end[1], "kind": "target"})
    return points


def _hour_bias(conn, city_id):
    """Per-district weight for the current hour, from 14 days of history."""
    hour = time.localtime().tm_hour
    rows = dbmod.query(
        conn,
        "SELECT location_id, ts FROM sightings WHERE city_id = ? AND ts >= ? "
        "AND location_id IS NOT NULL LIMIT 2000",
        (city_id, time.time() - 14 * 86400),
    )
    counts, totals = {}, {}
    for row in rows:
        lid = row["location_id"]
        totals[lid] = totals.get(lid, 0) + 1
        h = time.localtime(row["ts"]).tm_hour
        # Treat +/-1 hour as the same slot so the signal is not too sparse.
        if min(abs(h - hour), 24 - abs(h - hour)) <= 1:
            counts[lid] = counts.get(lid, 0) + 1
    return {
        lid: min(1.0, (counts.get(lid, 0) / float(totals[lid])) * 6.0)
        for lid in totals
    }


def _empty(city_id, reason):
    return {
        "city_id": city_id,
        "generated_at": time.time(),
        "sparse": True,
        "reason": reason,
        "origin": None,
        "vector": None,
        "projection": None,
        "candidates": [],
        "route": [],
        "eta_min": None,
        "eta_max": None,
        "confidence": 0.0,
        "samples": 0,
        "method": "insufficient data",
    }


def recompute(app, city_id, broadcast=True):
    """Compute, persist as the current prediction, and optionally broadcast."""
    payload = compute(app, city_id)
    with dbmod.connection(app.config["DATABASE"]) as conn:
        with dbmod.write_lock:
            conn.execute(
                "UPDATE predictions SET is_current = 0 WHERE city_id = ? AND is_current = 1",
                (city_id,),
            )
            conn.execute(
                "INSERT INTO predictions(city_id,created_at,origin_sighting,payload,"
                "confidence,eta_min,eta_max,is_current) VALUES(?,?,?,?,?,?,?,1)",
                (city_id, payload["generated_at"],
                 payload["origin"]["sighting_id"] if payload.get("origin") else None,
                 json.dumps(payload), payload["confidence"],
                 payload["eta_min"], payload["eta_max"]),
            )
            # Keep history bounded; the panel only ever shows the current one.
            conn.execute(
                "DELETE FROM predictions WHERE city_id = ? AND is_current = 0 AND id NOT IN "
                "(SELECT id FROM predictions WHERE city_id = ? ORDER BY created_at DESC LIMIT 50)",
                (city_id, city_id),
            )
            conn.commit()

    if broadcast:
        events.publish("prediction.updated", payload)
    return payload


def current(app, city_id):
    with dbmod.connection(app.config["DATABASE"]) as conn:
        row = dbmod.query(
            conn,
            "SELECT payload FROM predictions WHERE city_id = ? AND is_current = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (city_id,),
            one=True,
        )
    if row:
        try:
            return json.loads(row["payload"])
        except ValueError:
            pass
    return recompute(app, city_id, broadcast=False)
