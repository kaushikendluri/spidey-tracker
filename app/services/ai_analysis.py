"""Sighting analysis.

HONESTY CONTRACT
---------------
There is no trained Spider-Man detector in this project. Three engines exist and
each one labels itself so the UI can never present guesswork as model output:

    "model"     - a real vision model, only used when SPIDEY_VISION_MODEL points
                  at a loadable model. Not shipped; `load_model` is the hook.
    "heuristic" - genuine image measurement (colour-palette match against the
                  suit's red/blue, saturation, contrast, edge density) plus real
                  motion and location correlation computed from the database.
                  Honest signal, but it is not object detection.
    "demo"      - no image supplied; numbers are synthesised for simulated
                  sightings only. Always surfaced as "DEMO ANALYSIS".

`is_real_model` is stored per analysis and drives the badge in the UI.
"""

import colorsys
import math
import random
import time

from .. import db as dbmod
from . import geo

# Suit palette in HSV. Hue is 0..1. Reds wrap the origin, so two red bands.
SUIT_HUES = [
    ("red", 0.000, 0.045),
    ("red", 0.955, 1.000),
    ("blue", 0.560, 0.690),
]

_model = None
_model_loaded = False


def load_model(app):
    """Hook for a real detector.

    Wire an actual model here (torch/onnx/tflite) and return it. While this
    returns None the analyser stays in heuristic mode and says so.
    """
    global _model, _model_loaded
    if _model_loaded:
        return _model
    _model_loaded = True
    path = app.config.get("VISION_MODEL_PATH")
    if not path:
        _model = None
        return None
    try:  # pragma: no cover - depends on an operator-supplied model
        raise NotImplementedError(
            "No inference backend is bundled. Implement load_model() to enable "
            "real detection; the app stays in heuristic mode until then."
        )
    except Exception:
        _model = None
    return _model


def image_model_available(app):
    return load_model(app) is not None


# --- image measurement ---------------------------------------------------


def measure_image(path, max_side=256):
    """Measure suit-palette presence, saturation, contrast and edge density.

    Returns None when the file cannot be read. These are real measurements of
    real pixels; they are not a claim that the subject was identified.
    """
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError:
        return None

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            pixels = list(img.getdata())
            if not pixels:
                return None

            red = blue = vivid = 0
            sat_total = 0.0
            val_total = 0.0
            for r, g, b in pixels:
                h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                sat_total += s
                val_total += v
                if s < 0.35 or v < 0.15:
                    continue
                vivid += 1
                for name, lo, hi in SUIT_HUES:
                    if lo <= h <= hi:
                        if name == "red":
                            red += 1
                        else:
                            blue += 1
                        break

            total = len(pixels)
            grey = img.convert("L")
            edges = grey.filter(ImageFilter.FIND_EDGES)
            edge_mean = ImageStat.Stat(edges).mean[0] / 255.0
            contrast = ImageStat.Stat(grey).stddev[0] / 128.0

            return {
                "pixels": total,
                "red_ratio": red / total,
                "blue_ratio": blue / total,
                "vivid_ratio": vivid / total,
                "saturation": sat_total / total,
                "brightness": val_total / total,
                "edge_density": min(1.0, edge_mean * 3.2),
                "contrast": min(1.0, contrast),
                "width": img.width,
                "height": img.height,
            }
    except Exception:
        return None


def _visual_score(m):
    """Map image measurements onto a 0..100 'looks like the suit' score.

    Both colours must be present: a purely red image scores far lower than a
    balanced red+blue one, which is what actually distinguishes the suit.
    """
    if not m:
        return None
    red, blue = m["red_ratio"], m["blue_ratio"]
    presence = min(1.0, (red + blue) * 6.0)
    if red + blue > 0:
        balance = 1.0 - abs(red - blue) / (red + blue)
    else:
        balance = 0.0
    # Weight presence most, then how two-tone it is, then image quality signals.
    score = (
        presence * 52.0
        + balance * 24.0
        + min(1.0, m["saturation"] * 1.9) * 12.0
        + m["edge_density"] * 7.0
        + m["contrast"] * 5.0
    )
    return max(2.0, min(98.0, score))


# --- motion / location correlation --------------------------------------


def motion_score(conn, city_id, lat, lon, ts, direction, speed_kmh, window_min=180):
    """How consistent this sighting is with recent movement in the same city.

    Rewards a plausible implied speed between the previous sighting and this
    one, and rewards heading agreement. Returns (score, detail).
    """
    prev = dbmod.query(
        conn,
        "SELECT latitude, longitude, ts, direction, speed_kmh FROM sightings "
        "WHERE city_id = ? AND ts < ? AND ts >= ? ORDER BY ts DESC LIMIT 1",
        (city_id, ts, ts - window_min * 60),
        one=True,
    )
    if not prev:
        return None, {"reason": "no prior sighting in window"}

    dt_h = max(1e-4, (ts - prev["ts"]) / 3600.0)
    dist = geo.haversine_km(prev["latitude"], prev["longitude"], lat, lon)
    implied = dist / dt_h
    leg_bearing = geo.bearing_deg(prev["latitude"], prev["longitude"], lat, lon)

    # Plausibility peaks around 30 km/h and tails off; >120 km/h is implausible
    # for rooftop travel and drags the score down hard.
    if implied <= 0.5:
        speed_fit = 0.55           # stationary: neither supports nor refutes
    else:
        speed_fit = math.exp(-((math.log(implied / 30.0)) ** 2) / 1.1)
    speed_fit = max(0.05, min(1.0, speed_fit))

    if direction is not None:
        heading_fit = 1.0 - geo.angular_delta(direction, leg_bearing) / 180.0
    else:
        heading_fit = 0.6

    score = (speed_fit * 0.68 + heading_fit * 0.32) * 100.0
    return max(3.0, min(98.0, score)), {
        "implied_speed_kmh": round(implied, 1),
        "leg_km": round(dist, 3),
        "leg_bearing": round(leg_bearing, 1),
        "gap_min": round((ts - prev["ts"]) / 60.0, 1),
    }


def location_score(conn, city_id, lat, lon, ts, radius_km=2.0, window_h=72):
    """How well the coordinates correlate with historical activity nearby."""
    min_lat, max_lat, min_lon, max_lon = geo.bbox_around(lat, lon, radius_km)
    rows = dbmod.query(
        conn,
        "SELECT latitude, longitude, confidence FROM sightings "
        "WHERE city_id = ? AND ts >= ? AND ts <= ? "
        "AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? LIMIT 400",
        (city_id, ts - window_h * 3600, ts, min_lat, max_lat, min_lon, max_lon),
    )
    nearby = 0.0
    for row in rows:
        d = geo.haversine_km(lat, lon, row["latitude"], row["longitude"])
        if d <= radius_km:
            nearby += (1.0 - d / radius_km) * (row["confidence"] / 100.0)

    density = 1.0 - math.exp(-nearby / 3.0)

    loc = dbmod.query(
        conn,
        "SELECT name, latitude, longitude, radius_km, activity_bias FROM locations "
        "WHERE city_id = ?",
        (city_id,),
    )
    bias = 0.35
    matched = None
    best = None
    for row in loc:
        d = geo.haversine_km(lat, lon, row["latitude"], row["longitude"])
        if best is None or d < best[0]:
            best = (d, row)
        if d <= row["radius_km"]:
            if bias < row["activity_bias"]:
                bias = row["activity_bias"]
                matched = row["name"]
    if matched is None and best is not None:
        matched = best[1]["name"]

    score = (density * 62.0 + bias * 38.0)
    return max(5.0, min(99.0, score)), {"nearby_weight": round(nearby, 2),
                                        "area_bias": round(bias, 2),
                                        "matched_area": matched}


def pattern_score(conn, city_id, ts, direction):
    """Time-of-day and heading regularity against this city's history."""
    hour = time.localtime(ts).tm_hour
    rows = dbmod.query(
        conn,
        "SELECT direction, ts FROM sightings WHERE city_id = ? AND ts >= ? LIMIT 800",
        (city_id, ts - 14 * 86400),
    )
    if not rows:
        return None, {"reason": "insufficient history"}

    same_hour = sum(1 for r in rows if time.localtime(r["ts"]).tm_hour == hour)
    hour_fit = min(1.0, (same_hour / max(1.0, len(rows))) * 12.0)

    headings = [r["direction"] for r in rows if r["direction"] is not None]
    if direction is not None and headings:
        mean = geo.mean_bearing(headings)
        heading_fit = 1.0 - geo.angular_delta(direction, mean) / 180.0 if mean is not None else 0.5
    else:
        heading_fit = 0.5

    score = (hour_fit * 0.55 + heading_fit * 0.45) * 100.0
    return max(5.0, min(97.0, score)), {"same_hour_samples": same_hour,
                                        "history_samples": len(rows)}


# --- orchestration -------------------------------------------------------

WEIGHTS = {"visual": 0.42, "motion": 0.22, "pattern": 0.14, "location": 0.22}


def analyze(app, conn, sighting):
    """Run every available signal and fuse them into a confidence score.

    `sighting` is a dict with city_id, latitude, longitude, ts, direction,
    speed_kmh, image_path, source and optional is_demo.
    """
    image_path = sighting.get("image_path")
    measurements = measure_image(image_path) if image_path else None

    real_model = image_model_available(app)
    visual = _visual_score(measurements)

    motion, motion_detail = motion_score(
        conn, sighting["city_id"], sighting["latitude"], sighting["longitude"],
        sighting["ts"], sighting.get("direction"), sighting.get("speed_kmh"),
    )
    loc, loc_detail = location_score(
        conn, sighting["city_id"], sighting["latitude"], sighting["longitude"],
        sighting["ts"],
    )
    pattern, pattern_detail = pattern_score(
        conn, sighting["city_id"], sighting["ts"], sighting.get("direction")
    )

    # Redistribute the weight of any signal we could not compute, so a missing
    # image does not silently halve the score.
    parts = {"visual": visual, "motion": motion, "pattern": pattern, "location": loc}
    available = {k: v for k, v in parts.items() if v is not None}
    if available:
        total_w = sum(WEIGHTS[k] for k in available)
        probability = sum(WEIGHTS[k] * v for k, v in available.items()) / total_w
    else:
        probability = 0.0

    if sighting.get("source") == "camera":
        probability = min(99.0, probability * 1.06)   # fixed optics, known position

    if real_model:
        engine, label, is_real = "model", "MODEL ANALYSIS", 1
    elif measurements:
        engine, label, is_real = "heuristic", "HEURISTIC ANALYSIS", 0
    elif available:
        engine, label, is_real = "heuristic", "HEURISTIC (NO IMAGE)", 0
    else:
        engine, label, is_real = "demo", "DEMO ANALYSIS", 0

    notes = _compose_notes(measurements, motion_detail, loc_detail, pattern_detail, engine)

    return {
        "engine": engine,
        "engine_label": label,
        "is_real_model": is_real,
        "probability": round(max(0.0, min(99.0, probability)), 1),
        "visual_match": round(visual, 1) if visual is not None else 0.0,
        "motion_match": round(motion, 1) if motion is not None else 0.0,
        "pattern_match": round(pattern, 1) if pattern is not None else 0.0,
        "location_match": round(loc, 1) if loc is not None else 0.0,
        "notes": notes,
        "measurements": measurements,
        "computed": sorted(available.keys()),
    }


def _compose_notes(measurements, motion_detail, loc_detail, pattern_detail, engine):
    bits = []
    if measurements:
        bits.append("Suit-palette pixels: %.1f%% red, %.1f%% blue across %d sampled px."
                    % (measurements["red_ratio"] * 100, measurements["blue_ratio"] * 100,
                       measurements["pixels"]))
        bits.append("Edge density %.2f, contrast %.2f."
                    % (measurements["edge_density"], measurements["contrast"]))
    else:
        bits.append("No image supplied; visual channel excluded from fusion.")

    if motion_detail and "implied_speed_kmh" in motion_detail:
        bits.append("Implied travel %.1f km/h over %.1f min from previous sighting."
                    % (motion_detail["implied_speed_kmh"], motion_detail["gap_min"]))
    if loc_detail and loc_detail.get("matched_area"):
        bits.append("Correlated with %s (activity bias %.2f)."
                    % (loc_detail["matched_area"], loc_detail.get("area_bias", 0)))
    if pattern_detail and "history_samples" in pattern_detail:
        bits.append("Compared against %d historical records."
                    % pattern_detail["history_samples"])
    if engine != "model":
        bits.append("Heuristic scoring - not a trained detector.")
    return " ".join(bits)


def store(conn, sighting_id, result):
    dbmod.execute(
        conn,
        "INSERT INTO sighting_analysis(sighting_id,engine,engine_label,is_real_model,"
        "probability,visual_match,motion_match,pattern_match,location_match,notes,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sighting_id) DO UPDATE SET "
        "engine=excluded.engine, engine_label=excluded.engine_label, "
        "is_real_model=excluded.is_real_model, probability=excluded.probability, "
        "visual_match=excluded.visual_match, motion_match=excluded.motion_match, "
        "pattern_match=excluded.pattern_match, location_match=excluded.location_match, "
        "notes=excluded.notes, created_at=excluded.created_at",
        (sighting_id, result["engine"], result["engine_label"], result["is_real_model"],
         result["probability"], result["visual_match"], result["motion_match"],
         result["pattern_match"], result["location_match"], result["notes"], time.time()),
    )


def demo_analysis(conn, sighting, rng=None):
    """Analysis for simulated sightings.

    A demo sighting has no image, so running the heuristic fusion over it would
    produce a meaninglessly low score and a dashboard full of 17% records. The
    demo network instead reports the confidence it is pretending its own
    sensors produced. This is fiction and is labelled DEMO ANALYSIS everywhere
    it surfaces; `is_real_model` stays 0.
    """
    rng = rng or random

    bias = 0.5
    area_name = None
    if sighting.get("location_id"):
        row = dbmod.query(
            conn, "SELECT name, activity_bias FROM locations WHERE id = ?",
            (sighting["location_id"],), one=True,
        )
        if row:
            bias = row["activity_bias"]
            area_name = row["name"]

    base = 52.0 + bias * 26.0 + rng.gauss(0, 11)
    if sighting.get("source") == "camera":
        base += 9.0        # fixed optics, known position
    elif sighting.get("source") == "network":
        base += 3.0
    confidence = max(21.0, min(98.0, base))

    breakdown = synthesize_breakdown(confidence, bias, sighting.get("speed_kmh") or 30.0, rng)
    return {
        "engine": "demo",
        "engine_label": "DEMO ANALYSIS",
        "is_real_model": 0,
        "probability": round(confidence, 1),
        "visual_match": breakdown["visual_match"],
        "motion_match": breakdown["motion_match"],
        "pattern_match": breakdown["pattern_match"],
        "location_match": breakdown["location_match"],
        "notes": ("Simulated record from the demo network%s. No image was analysed "
                  "and no detector was run - these figures are generated for "
                  "demonstration." % (" in %s" % area_name if area_name else "")),
        "measurements": None,
        "computed": [],
    }


def synthesize_breakdown(confidence, activity_bias, speed_kmh, rng=None):
    """Plausible sub-scores for simulated sightings. Demo use only."""
    rng = rng or random
    def around(base, spread):
        return round(max(4.0, min(99.0, base + rng.gauss(0, spread))), 1)

    return {
        "visual_match": around(confidence, 6),
        "motion_match": around(confidence - 2 + (35 - abs(speed_kmh - 32)) * 0.12, 7),
        "pattern_match": around(confidence + 2, 6),
        "location_match": around(45 + activity_bias * 50, 5),
        "notes": "Simulated record from the demo network. No image was analysed.",
    }
