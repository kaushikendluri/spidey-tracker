"""Sighting lifecycle: create, serialise, update, delete.

Both the HTTP API and the demo simulator go through here so that a simulated
sighting and a reported one travel exactly the same pipeline:

    validate -> analyse -> score -> persist -> broadcast -> re-predict -> alert
"""

import time
import uuid

from .. import db as dbmod
from .. import events
from . import ai_analysis, geo, prediction

HIGH_CONFIDENCE = 85.0
ALERT_THRESHOLD = 82.0

VALID_STATUS = ("active", "confirmed", "unverified", "expired", "dismissed")
VALID_SOURCE = ("citizen", "camera", "network", "demo")


class ValidationError(ValueError):
    def __init__(self, field, message):
        super(ValidationError, self).__init__(message)
        self.field = field
        self.message = message


# --- serialisation -------------------------------------------------------

SELECT_SIGHTING = """
SELECT s.*,
       c.name  AS city_name,
       l.name  AS location_name,
       cam.label AS camera_label,
       a.engine, a.engine_label, a.is_real_model, a.probability,
       a.visual_match, a.motion_match, a.pattern_match, a.location_match, a.notes
FROM sightings s
JOIN cities c ON c.id = s.city_id
LEFT JOIN locations l ON l.id = s.location_id
LEFT JOIN cameras cam ON cam.id = s.camera_id
LEFT JOIN sighting_analysis a ON a.sighting_id = s.id
"""


def serialize(row):
    d = dict(row)
    analysis = None
    if d.get("engine"):
        analysis = {
            "engine": d["engine"],
            "label": d["engine_label"],
            "is_real_model": bool(d["is_real_model"]),
            "probability": d["probability"],
            "visual_match": d["visual_match"],
            "motion_match": d["motion_match"],
            "pattern_match": d["pattern_match"],
            "location_match": d["location_match"],
            "notes": d["notes"],
        }
    for key in ("engine", "engine_label", "is_real_model", "probability",
                "visual_match", "motion_match", "pattern_match",
                "location_match", "notes"):
        d.pop(key, None)

    d["is_demo"] = bool(d.get("is_demo"))
    d["ai_verified"] = bool(d.get("ai_verified"))
    d["direction_label"] = geo.compass_label(d.get("direction"))
    d["analysis"] = analysis
    d["age_sec"] = max(0.0, time.time() - d["ts"])
    d["image_url"] = "/api/sightings/%d/image" % d["id"] if d.get("image_path") else None
    d["video_url"] = "/api/sightings/%d/video" % d["id"] if d.get("video_path") else None
    d.pop("image_path", None)
    d.pop("video_path", None)
    return d


def get(conn, sighting_id):
    row = dbmod.query(conn, SELECT_SIGHTING + " WHERE s.id = ?", (sighting_id,), one=True)
    return serialize(row) if row else None


def get_raw(conn, sighting_id):
    return dbmod.query(conn, "SELECT * FROM sightings WHERE id = ?", (sighting_id,), one=True)


# --- querying ------------------------------------------------------------

def build_filters(args):
    """Translate query-string filters into SQL. Returns (where_sql, params)."""
    clauses, params = [], []

    city = args.get("city")
    if city and city != "all":
        clauses.append("s.city_id = ?")
        params.append(city)

    status = args.get("status")
    if status and status != "all":
        wanted = [s for s in status.split(",") if s in VALID_STATUS]
        if wanted:
            clauses.append("s.status IN (%s)" % ",".join("?" * len(wanted)))
            params.extend(wanted)

    source = args.get("source")
    if source and source != "all":
        wanted = [s for s in source.split(",") if s in VALID_SOURCE]
        if wanted:
            clauses.append("s.source IN (%s)" % ",".join("?" * len(wanted)))
            params.extend(wanted)

    camera = args.get("camera")
    if camera:
        clauses.append("s.camera_id = ?")
        params.append(camera)

    location = args.get("location")
    if location:
        clauses.append("s.location_id = ?")
        params.append(location)

    min_conf = args.get("min_confidence")
    if min_conf not in (None, "", "0"):
        try:
            clauses.append("s.confidence >= ?")
            params.append(float(min_conf))
        except ValueError:
            pass

    since = args.get("since")
    if since:
        try:
            clauses.append("s.ts >= ?")
            params.append(float(since))
        except ValueError:
            pass

    window = args.get("window")   # minutes
    if window:
        try:
            clauses.append("s.ts >= ?")
            params.append(time.time() - float(window) * 60)
        except ValueError:
            pass

    if args.get("ai_verified") in ("1", "true"):
        clauses.append("s.ai_verified = 1")

    if args.get("include_demo") in ("0", "false"):
        clauses.append("s.is_demo = 0")

    search = (args.get("q") or "").strip()
    if search:
        clauses.append("(s.area LIKE ? OR s.ref LIKE ? OR s.description LIKE ? "
                       "OR s.camera_id LIKE ?)")
        like = "%" + search + "%"
        params.extend([like, like, like, like])

    bbox = args.get("bbox")
    if bbox:
        try:
            south, west, north, east = [float(v) for v in bbox.split(",")]
            clauses.append("s.latitude BETWEEN ? AND ? AND s.longitude BETWEEN ? AND ?")
            params.extend([min(south, north), max(south, north),
                           min(west, east), max(west, east)])
        except (ValueError, TypeError):
            pass

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_sightings(conn, args, limit=200, offset=0):
    where, params = build_filters(args)
    total = dbmod.query(
        conn, "SELECT COUNT(*) AS c FROM sightings s" + where.replace("s.", "s."),
        params, one=True
    )["c"]
    rows = dbmod.query(
        conn,
        SELECT_SIGHTING + where + " ORDER BY s.ts DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    return [serialize(r) for r in rows], total


# --- creation ------------------------------------------------------------

REF_BASE = 4800


def format_ref(row_id):
    """Human reference derived from the row id.

    Deriving it rather than reading "the last ref and adding one" is what makes
    it safe when the simulator thread and a request thread insert at the same
    moment - the previous approach raced and hit the UNIQUE constraint.
    """
    return "SGT-%04d" % (REF_BASE + row_id)


def resolve_area(conn, city_id, lat, lon):
    """Nearest district, used when the reporter did not pick one."""
    rows = dbmod.query(
        conn,
        "SELECT id, name, latitude, longitude, radius_km FROM locations WHERE city_id = ?",
        (city_id,),
    )
    best = None
    for row in rows:
        d = geo.haversine_km(lat, lon, row["latitude"], row["longitude"])
        if best is None or d < best[0]:
            best = (d, row)
    if best is None:
        return None, "UNMAPPED AREA"
    return best[1]["id"], best[1]["name"]


def validate(payload, conn):
    """Normalise and check an inbound sighting. Raises ValidationError."""
    try:
        lat = float(payload.get("latitude"))
        lon = float(payload.get("longitude"))
    except (TypeError, ValueError):
        raise ValidationError("latitude", "Latitude and longitude are required numbers.")
    if not (-90 <= lat <= 90):
        raise ValidationError("latitude", "Latitude must be between -90 and 90.")
    if not (-180 <= lon <= 180):
        raise ValidationError("longitude", "Longitude must be between -180 and 180.")

    city_id = (payload.get("city_id") or "").strip()
    if not city_id:
        raise ValidationError("city_id", "A city is required.")
    if not dbmod.query(conn, "SELECT id FROM cities WHERE id = ?", (city_id,), one=True):
        raise ValidationError("city_id", "Unknown city '%s'." % city_id)

    ts = payload.get("ts")
    if ts in (None, ""):
        ts = time.time()
    else:
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            raise ValidationError("ts", "Observation time must be a unix timestamp.")
    if ts > time.time() + 300:
        raise ValidationError("ts", "Observation time cannot be in the future.")

    source = (payload.get("source") or "citizen").strip()
    if source not in VALID_SOURCE:
        raise ValidationError("source", "Source must be one of %s." % ", ".join(VALID_SOURCE))

    camera_id = (payload.get("camera_id") or "").strip() or None
    if camera_id and not dbmod.query(
        conn, "SELECT id FROM cameras WHERE id = ?", (camera_id,), one=True
    ):
        raise ValidationError("camera_id", "Unknown camera '%s'." % camera_id)

    direction = payload.get("direction")
    if direction in ("", None):
        direction = None
    else:
        try:
            direction = float(direction) % 360.0
        except (TypeError, ValueError):
            raise ValidationError("direction", "Direction must be a bearing in degrees.")

    speed = payload.get("speed_kmh")
    if speed in ("", None):
        speed = None
    else:
        try:
            speed = max(0.0, float(speed))
        except (TypeError, ValueError):
            raise ValidationError("speed_kmh", "Speed must be a number.")

    location_id = (payload.get("location_id") or "").strip() or None
    area = (payload.get("area") or "").strip()
    if not location_id or not area:
        resolved_id, resolved_area = resolve_area(conn, city_id, lat, lon)
        location_id = location_id or resolved_id
        area = area or resolved_area

    return {
        "city_id": city_id,
        "location_id": location_id,
        "area": area.upper()[:80],
        "latitude": lat,
        "longitude": lon,
        "ts": ts,
        "source": source,
        "camera_id": camera_id,
        "reporter": (payload.get("reporter") or "").strip()[:80] or None,
        "description": (payload.get("description") or "").strip()[:1000] or None,
        "image_path": payload.get("image_path"),
        "video_path": payload.get("video_path"),
        "direction": direction,
        "speed_kmh": speed,
        "is_demo": 1 if payload.get("is_demo") else 0,
    }


def infer_motion(conn, data):
    """Fill in direction/speed from the previous sighting when not supplied."""
    if data.get("direction") is not None and data.get("speed_kmh") is not None:
        return data
    prev = dbmod.query(
        conn,
        "SELECT latitude, longitude, ts FROM sightings WHERE city_id = ? AND ts < ? "
        "ORDER BY ts DESC LIMIT 1",
        (data["city_id"], data["ts"]),
        one=True,
    )
    if not prev:
        return data
    dist = geo.haversine_km(prev["latitude"], prev["longitude"],
                            data["latitude"], data["longitude"])
    dt_h = max(1e-4, (data["ts"] - prev["ts"]) / 3600.0)
    if dist >= 0.04:
        if data.get("direction") is None:
            data["direction"] = round(geo.bearing_deg(
                prev["latitude"], prev["longitude"],
                data["latitude"], data["longitude"]), 1)
        if data.get("speed_kmh") is None:
            data["speed_kmh"] = round(min(140.0, dist / dt_h), 1)
    return data


def create(app, conn, payload, broadcast=True):
    """Full create pipeline. Returns the serialised sighting."""
    data = validate(payload, conn)
    data = infer_motion(conn, data)

    # Simulated records get simulated (clearly labelled) analysis; anything an
    # operator or camera actually reported goes through the real scorer.
    if data["is_demo"] and not data.get("image_path"):
        analysis = ai_analysis.demo_analysis(conn, data)
    else:
        analysis = ai_analysis.analyze(app, conn, data)
    confidence = analysis["probability"]

    status = (payload.get("status") or "").strip()
    if status not in VALID_STATUS:
        status = ("confirmed" if confidence >= HIGH_CONFIDENCE
                  else "unverified" if confidence < 55 else "active")

    now = time.time()
    alert = None

    # One writer at a time: the sighting, its analysis, the camera detection and
    # the alert must land together.
    with dbmod.transaction(conn):
        placeholder = "PENDING-%s" % uuid.uuid4().hex[:12]
        sighting_id, _ = dbmod.execute(
            conn,
            "INSERT INTO sightings(ref,city_id,location_id,area,latitude,longitude,ts,"
            "created_at,updated_at,confidence,status,source,camera_id,reporter,description,"
            "image_path,video_path,direction,speed_kmh,ai_verified,is_demo) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (placeholder, data["city_id"], data["location_id"], data["area"],
             data["latitude"], data["longitude"], data["ts"], now, now, confidence,
             status, data["source"], data["camera_id"], data["reporter"],
             data["description"], data["image_path"], data["video_path"],
             data["direction"], data["speed_kmh"],
             1 if (confidence >= HIGH_CONFIDENCE and analysis["engine"] != "demo") else 0,
             data["is_demo"]),
        )
        dbmod.execute(conn, "UPDATE sightings SET ref = ? WHERE id = ?",
                      (format_ref(sighting_id), sighting_id))

        ai_analysis.store(conn, sighting_id, analysis)

        if data["camera_id"]:
            dbmod.execute(
                conn,
                "INSERT INTO camera_detections(camera_id,sighting_id,ts,confidence,"
                "summary,is_demo) VALUES(?,?,?,?,?,?)",
                (data["camera_id"], sighting_id, data["ts"], confidence,
                 "POSSIBLE SUBJECT DETECTED", data["is_demo"]),
            )
            dbmod.execute(
                conn,
                "UPDATE cameras SET status = 'detected', last_status_at = ? WHERE id = ?",
                (now, data["camera_id"]),
            )

        if confidence >= ALERT_THRESHOLD:
            alert = create_alert(
                conn,
                kind="sighting",
                severity="critical" if confidence >= 92 else "warning",
                title="NEW SIGHTING DETECTED",
                body="%s - %.0f%% CONFIDENCE" % (data["area"], confidence),
                sighting_id=sighting_id,
                city_id=data["city_id"],
                is_demo=data["is_demo"],
            )

        record = get(conn, sighting_id)

    # Broadcast outside the write lock so subscribers never block a writer.
    if data["camera_id"]:
        events.publish("camera.detected", {
            "camera_id": data["camera_id"],
            "sighting_id": sighting_id,
            "confidence": confidence,
            "ts": data["ts"],
            "is_demo": bool(data["is_demo"]),
        })

    if broadcast:
        events.publish("sighting.created", record)
        if alert:
            events.publish("alert.created", dict(alert, sighting=record))

    if broadcast:
        prediction.recompute(app, data["city_id"])
        events.publish("network.updated", {"city_id": data["city_id"]})

    return record


def create_alert(conn, kind, severity, title, body, sighting_id=None,
                 city_id=None, is_demo=0):
    now = time.time()
    alert_id, _ = dbmod.execute(
        conn,
        "INSERT INTO alerts(kind,severity,title,body,sighting_id,city_id,created_at,"
        "acknowledged,is_demo) VALUES(?,?,?,?,?,?,?,0,?)",
        (kind, severity, title, body, sighting_id, city_id, now, is_demo),
    )
    return {
        "id": alert_id, "kind": kind, "severity": severity, "title": title,
        "body": body, "sighting_id": sighting_id, "city_id": city_id,
        "created_at": now, "acknowledged": False, "is_demo": bool(is_demo),
    }


# --- mutation ------------------------------------------------------------

MUTABLE = ("status", "confidence", "area", "description", "direction",
           "speed_kmh", "location_id", "ai_verified")


def update(app, conn, sighting_id, payload, broadcast=True):
    existing = get_raw(conn, sighting_id)
    if not existing:
        return None

    sets, params = [], []
    for field in MUTABLE:
        if field not in payload:
            continue
        value = payload[field]
        if field == "status":
            if value not in VALID_STATUS:
                raise ValidationError("status", "Invalid status '%s'." % value)
        elif field == "confidence":
            try:
                value = max(0.0, min(100.0, float(value)))
            except (TypeError, ValueError):
                raise ValidationError("confidence", "Confidence must be 0-100.")
        elif field == "direction" and value is not None:
            try:
                value = float(value) % 360.0
            except (TypeError, ValueError):
                raise ValidationError("direction", "Direction must be numeric.")
        elif field == "speed_kmh" and value is not None:
            try:
                value = max(0.0, float(value))
            except (TypeError, ValueError):
                raise ValidationError("speed_kmh", "Speed must be numeric.")
        elif field == "ai_verified":
            value = 1 if value else 0
        sets.append("%s = ?" % field)
        params.append(value)

    if not sets:
        return get(conn, sighting_id)

    sets.append("updated_at = ?")
    params.append(time.time())
    params.append(sighting_id)
    dbmod.execute(conn, "UPDATE sightings SET %s WHERE id = ?" % ", ".join(sets), params)

    record = get(conn, sighting_id)
    if broadcast:
        events.publish("sighting.updated", record)
        if "confidence" in payload or "status" in payload:
            prediction.recompute(app, record["city_id"])
        events.publish("network.updated", {"city_id": record["city_id"]})
    return record


def delete(app, conn, sighting_id, broadcast=True):
    existing = get_raw(conn, sighting_id)
    if not existing:
        return False
    city_id = existing["city_id"]
    dbmod.execute(conn, "DELETE FROM sightings WHERE id = ?", (sighting_id,))
    if broadcast:
        events.publish("sighting.deleted", {"id": sighting_id, "city_id": city_id})
        prediction.recompute(app, city_id)
        events.publish("network.updated", {"city_id": city_id})
    return True


def expire_stale(app, conn, broadcast=True):
    """Move 'active' sightings past the active window into 'expired'."""
    cutoff = time.time() - app.config["ACTIVE_WINDOW_MIN"] * 60
    rows = dbmod.query(
        conn, "SELECT id, city_id FROM sightings WHERE status = 'active' AND ts < ?",
        (cutoff,),
    )
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    dbmod.execute(
        conn,
        "UPDATE sightings SET status = 'expired', updated_at = ? WHERE id IN (%s)"
        % ",".join("?" * len(ids)),
        [time.time()] + ids,
    )
    if broadcast:
        for row in rows:
            record = get(conn, row["id"])
            if record:
                events.publish("sighting.updated", record)
    return ids
