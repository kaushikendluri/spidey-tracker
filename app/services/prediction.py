"""Movement prediction.

Everything here is computed from rows in the database - there are no fixed
probabilities anywhere in this module. The pipeline is:

    recent sightings
        -> recency-weighted movement vector (circular mean bearing + speed,
           plus the measured dispersion of both)
        -> Monte Carlo ensemble of forward tracks seeded from that dispersion
        -> dead-reckoned projection at the prediction horizon
        -> per-district scoring: analytic (proximity x alignment x prior)
           blended with the ensemble's own landing frequency
        -> normalisation into probabilities
        -> route polyline, cone of uncertainty, ETA band

When there is too little movement data the engine returns a low-confidence
result flagged `sparse`, rather than inventing a path.
"""

import json
import math
import random
import time

from .. import db as dbmod
from .. import events
from . import geo

# --- ensemble tuning ------------------------------------------------------
# Enough members for stable percentiles without making recompute expensive:
# MEMBERS x STEPS destination() calls per prediction.
ENSEMBLE_MEMBERS = 72
ENSEMBLE_STEPS = 8
# Percentile width of the drawn cone. 80% is the usual convention for a
# forecast cone - wide enough to be honest, narrow enough to be useful.
CONE_LOW_PCT = 10.0
CONE_HIGH_PCT = 90.0
# How much of the final probability comes from the simulation rather than the
# analytic score. The ensemble captures "can they physically get there in the
# time available"; the analytic score captures "is that somewhere they go".
ENSEMBLE_WEIGHT = 0.45


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

    # Circular standard deviation (Mardia): sqrt(-2 ln R). This is the real
    # spread of the observed headings and is what sets the cone's width - the
    # cone must not be a decorative fixed angle.
    resultant = max(1e-6, min(0.999999, coherence))
    heading_sigma = math.degrees(math.sqrt(-2.0 * math.log(resultant)))
    # A handful of legs cannot justify a needle-thin cone, and a wild spread
    # past a half-turn stops being informative.
    heading_sigma = max(6.0, min(75.0, heading_sigma))

    # Standard error of the mean heading. This - not the per-leg spread - is
    # how wrong our estimate of the direction of travel is likely to be, and it
    # is what the forecast cone should fan out from. Individual legs scatter
    # around the mean, but that scatter largely averages out over a journey.
    heading_se = max(4.0, min(45.0, heading_sigma / math.sqrt(len(bearings))))

    # Weighted speed dispersion, floored so a single leg still admits variation.
    if len(speeds) > 1:
        var = sum(w * (s - speed) ** 2 for s, w in zip(speeds, weights)) / wsum
        speed_sigma = math.sqrt(max(0.0, var))
    else:
        speed_sigma = speed * 0.35
    speed_sigma = max(2.0, min(speed * 0.9 + 8.0, speed_sigma))

    return {
        "heading": round(heading, 1),
        "compass": geo.compass_label(heading),
        "speed_kmh": round(speed, 1),
        "coherence": round(coherence, 3),
        "legs": len(bearings),
        "heading_sigma": round(heading_sigma, 1),
        "heading_se": round(heading_se, 1),
        "speed_sigma": round(speed_sigma, 1),
    }


def _pt(lat, lon):
    """Coordinate rounded to ~1 m.

    The whole prediction payload is rebroadcast over SSE on every new sighting,
    and raw float repr costs ~17 characters per number. Five decimal places is
    finer than anything the map can display and roughly halves the payload.
    """
    return {"latitude": round(lat, 5), "longitude": round(lon, 5)}


def _percentile(sorted_values, pct):
    """Linear-interpolated percentile over an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (rank - low)


def simulate_ensemble(origin_lat, origin_lon, vector, horizon_min, seed):
    """Monte Carlo forward tracks from the measured movement distribution.

    Each member draws its own initial heading and speed from the dispersion
    actually observed in the recent legs, then random-walks its heading as it
    advances. That turn-to-turn wander is why the spread grows with time rather
    than staying a fixed wedge.

    Seeded from the origin sighting so the same prediction redraws identically
    - a cone that jitters on every refresh would look like new information
    arriving when nothing changed.
    """
    if not vector:
        return None

    rng = random.Random(seed)
    steps = ENSEMBLE_STEPS
    dt_h = (horizon_min / 60.0) / steps

    # The fan comes from how uncertain the mean heading is; the per-step wander
    # only adds texture. Driving the walk with the full per-leg spread instead
    # makes members curl back on themselves, so the envelope ends up wider than
    # it is long - a blob rather than a forecast cone.
    heading_se = vector.get("heading_se", vector["heading_sigma"])
    speed_sigma = vector["speed_sigma"]
    turn_sigma = max(2.0, vector["heading_sigma"] * 0.18)

    tracks = []
    for _ in range(ENSEMBLE_MEMBERS):
        heading = vector["heading"] + rng.gauss(0.0, heading_se)
        speed = max(3.0, rng.gauss(vector["speed_kmh"], speed_sigma))
        lat, lon = origin_lat, origin_lon
        points = [(lat, lon)]
        for _step in range(steps):
            heading = (heading + rng.gauss(0.0, turn_sigma)) % 360.0
            lat, lon = geo.destination(lat, lon, heading, speed * dt_h)
            points.append((lat, lon))
        tracks.append(points)

    return {
        "tracks": tracks,
        "steps": steps,
        "dt_min": (horizon_min / steps),
        "members": len(tracks),
    }


def build_cone(origin_lat, origin_lon, vector, ensemble):
    """Cone polygon, centre line and time rings from the ensemble spread.

    Positions are resolved into along-track and cross-track components relative
    to the mean heading, so the boundary is a genuine percentile envelope of
    where the members went rather than a circle drawn around the mean.
    """
    if not ensemble or not vector:
        return None

    mean_heading = vector["heading"]
    steps = ensemble["steps"]
    left, right, centre, rings = [], [], [], []

    for step in range(1, steps + 1):
        alongs, crosses = [], []
        for track in ensemble["tracks"]:
            lat, lon = track[step]
            dist = geo.haversine_km(origin_lat, origin_lon, lat, lon)
            bearing = geo.bearing_deg(origin_lat, origin_lon, lat, lon)
            rel = math.radians(bearing - mean_heading)
            alongs.append(dist * math.cos(rel))
            crosses.append(dist * math.sin(rel))

        alongs.sort()
        crosses.sort()
        along_mid = _percentile(alongs, 50.0)
        cross_low = _percentile(crosses, CONE_LOW_PCT)
        cross_high = _percentile(crosses, CONE_HIGH_PCT)

        # Walk out along the mean heading, then offset perpendicular to it.
        spine_lat, spine_lon = geo.destination(
            origin_lat, origin_lon, mean_heading, max(0.0, along_mid))
        left_lat, left_lon = geo.destination(
            spine_lat, spine_lon, (mean_heading - 90.0) % 360.0, abs(cross_low))
        right_lat, right_lon = geo.destination(
            spine_lat, spine_lon, (mean_heading + 90.0) % 360.0, abs(cross_high))

        left.append(_pt(left_lat, left_lon))
        right.append(_pt(right_lat, right_lon))
        centre.append(_pt(spine_lat, spine_lon))
        rings.append({
            "step": step,
            "minutes": round(step * ensemble["dt_min"], 1),
            "latitude": round(spine_lat, 5),
            "longitude": round(spine_lon, 5),
            "half_width_km": round((abs(cross_low) + abs(cross_high)) / 2.0, 2),
            "along_km": round(max(0.0, along_mid), 2),
        })

    # Closed ring: up one edge, back down the other.
    polygon = [_pt(origin_lat, origin_lon)] + right + list(reversed(left))

    return {
        "polygon": polygon,
        "centre": centre,
        "rings": rings,
        "confidence_pct": round(CONE_HIGH_PCT - CONE_LOW_PCT, 0),
        "members": ensemble["members"],
    }


def ensemble_landing_probabilities(districts, ensemble):
    """Fraction of members whose final position falls in each district.

    A purely simulation-derived estimate, independent of the analytic score,
    and reported alongside it so the two can be compared rather than conflated.
    """
    if not ensemble:
        return {}

    counts = {}
    total = 0
    for track in ensemble["tracks"]:
        lat, lon = track[-1]
        best_id, best_gap = None, None
        for d in districts:
            gap = geo.haversine_km(lat, lon, d["latitude"], d["longitude"])
            # Only counts as a landing if inside the district's own footprint.
            if gap <= d["radius_km"] and (best_gap is None or gap < best_gap):
                best_id, best_gap = d["id"], gap
        if best_id is not None:
            counts[best_id] = counts.get(best_id, 0) + 1
            total += 1

    if not total:
        return {}
    return {lid: (n / float(ensemble["members"])) for lid, n in counts.items()}


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

        # Forward simulation from the measured dispersion. Seeded on the origin
        # sighting so a redraw of the same prediction is pixel-identical.
        ensemble = simulate_ensemble(
            latest["latitude"], latest["longitude"], vector, horizon,
            seed=latest["id"],
        )
        cone = build_cone(latest["latitude"], latest["longitude"], vector, ensemble)
        landings = ensemble_landing_probabilities(districts, ensemble)

        # Which district the subject is already in. The ensemble will often
        # rank it highly simply because members have not left it yet within the
        # horizon; that is a real outcome ("still here"), but the UI has to
        # distinguish it from a genuine onward destination.
        origin_district = None
        origin_gap = None
        for d in districts:
            gap = geo.haversine_km(latest["latitude"], latest["longitude"],
                                   d["latitude"], d["longitude"])
            if gap <= d["radius_km"] and (origin_gap is None or gap < origin_gap):
                origin_district, origin_gap = d["id"], gap

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
                "ensemble_share": landings.get(d["id"], 0.0),
                "is_origin": d["id"] == origin_district,
                "distance_km": round(dist_from_last, 2),
                "eta_min": round(eta, 1),
            })

        total = sum(s["score"] for s in scored)
        if total <= 0:
            return _empty(city_id, "no directional signal")

        ensemble_total = sum(s["ensemble_share"] for s in scored)

        for s in scored:
            analytic = s["score"] / total
            s["analytic_probability"] = round(analytic * 100.0, 1)

            if ensemble_total > 0:
                simulated = s["ensemble_share"] / ensemble_total
                s["ensemble_probability"] = round(simulated * 100.0, 1)
                # Blend: the simulation knows what is reachable in the time
                # available, the analytic score knows where activity happens.
                blended = ((1.0 - ENSEMBLE_WEIGHT) * analytic
                           + ENSEMBLE_WEIGHT * simulated)
            else:
                # No member landed inside any district - fall back rather than
                # zeroing every candidate.
                s["ensemble_probability"] = None
                blended = analytic

            s["probability"] = round(blended * 100.0, 1)
            s.pop("score")
            s.pop("ensemble_share")

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
            "cone": cone,
            "ensemble": {
                "members": ensemble["members"],
                "steps": ensemble["steps"],
                "dt_min": round(ensemble["dt_min"], 2),
                "heading_sigma": vector["heading_sigma"],
                "speed_sigma": vector["speed_sigma"],
                "landed": round(sum(landings.values()) * 100.0, 1),
                # A handful of full tracks for the map to draw as spaghetti.
                "sample_tracks": [
                    [_pt(lat, lon) for lat, lon in track]
                    for track in ensemble["tracks"][:10]
                ],
            } if ensemble else None,
            "eta_min": eta_lo,
            "eta_max": eta_hi,
            "confidence": confidence,
            "samples": len(rows),
            "method": ("recency-weighted dead reckoning + district prior, "
                       "blended with a %d-member Monte Carlo ensemble"
                       % ENSEMBLE_MEMBERS) if ensemble else
                      "recency-weighted dead reckoning + district prior",
        }
        return payload


def _route(latest, lead, vector, proj_lat, proj_lon):
    """Waypoints from the last sighting to the leading candidate.

    Bowed slightly toward the movement vector so the drawn path reads as travel
    rather than a ruler line between two dots.
    """
    start = (latest["latitude"], latest["longitude"])
    end = (lead["latitude"], lead["longitude"])
    points = [dict(_pt(start[0], start[1]), kind="origin")]

    steps = 5
    total = geo.haversine_km(start[0], start[1], end[0], end[1])
    for i in range(1, steps):
        t = i / float(steps)
        lat = start[0] + (end[0] - start[0]) * t
        lon = start[1] + (end[1] - start[1]) * t
        if vector and total > 0.2:
            bow = math.sin(t * math.pi) * min(0.35, total * 0.12)
            lat, lon = geo.destination(lat, lon, (vector["heading"] + 90) % 360, bow)
        points.append(dict(_pt(lat, lon), kind="waypoint"))

    points.append(dict(_pt(proj_lat, proj_lon), kind="projected"))
    points.append(dict(_pt(end[0], end[1]), kind="target"))
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
        "cone": None,
        "ensemble": None,
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
