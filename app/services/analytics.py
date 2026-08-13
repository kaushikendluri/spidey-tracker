"""Aggregations behind the analytics panel.

Every number the charts render comes from one of these queries. Nothing here
returns a constant.
"""

import time

from .. import db as dbmod


def _scope(city_id, window_min, alias=""):
    prefix = (alias + ".") if alias else ""
    clauses = ["%sts >= ?" % prefix]
    params = [time.time() - window_min * 60]
    if city_id and city_id != "all":
        clauses.append("%scity_id = ?" % prefix)
        params.append(city_id)
    return " AND ".join(clauses), params


def summary(conn, city_id=None, window_min=1440):
    where, params = _scope(city_id, window_min)
    row = dbmod.query(
        conn,
        "SELECT COUNT(*) AS total, "
        "  SUM(CASE WHEN confidence >= 85 THEN 1 ELSE 0 END) AS high_confidence, "
        "  SUM(CASE WHEN status IN ('active','confirmed') THEN 1 ELSE 0 END) AS active, "
        "  SUM(CASE WHEN ai_verified = 1 THEN 1 ELSE 0 END) AS ai_verified, "
        "  SUM(CASE WHEN source = 'camera' THEN 1 ELSE 0 END) AS camera_detections, "
        "  SUM(CASE WHEN is_demo = 1 THEN 1 ELSE 0 END) AS demo_records, "
        "  AVG(confidence) AS avg_confidence, MAX(ts) AS last_ts "
        "FROM sightings WHERE " + where,
        params,
        one=True,
    )
    city_name = "ALL CITIES"
    if city_id and city_id != "all":
        crow = dbmod.query(conn, "SELECT name FROM cities WHERE id = ?", (city_id,), one=True)
        if crow:
            city_name = crow["name"]

    return {
        "city_id": city_id or "all",
        "city_name": city_name,
        "window_min": window_min,
        "total": row["total"] or 0,
        "high_confidence": row["high_confidence"] or 0,
        "active": row["active"] or 0,
        "ai_verified": row["ai_verified"] or 0,
        "camera_detections": row["camera_detections"] or 0,
        "demo_records": row["demo_records"] or 0,
        "avg_confidence": round(row["avg_confidence"] or 0, 1),
        "last_ts": row["last_ts"],
    }


def by_bucket(conn, city_id=None, window_min=1440, buckets=24):
    """Sighting counts over evenly spaced time buckets covering the window."""
    where, params = _scope(city_id, window_min)
    rows = dbmod.query(
        conn, "SELECT ts, confidence FROM sightings WHERE " + where, params
    )
    now = time.time()
    start = now - window_min * 60
    span = max(1.0, (now - start) / buckets)

    series = [{"index": i,
               "start": start + i * span,
               "end": start + (i + 1) * span,
               "count": 0,
               "high": 0}
              for i in range(buckets)]

    for row in rows:
        idx = int((row["ts"] - start) / span)
        idx = max(0, min(buckets - 1, idx))
        series[idx]["count"] += 1
        if row["confidence"] >= 85:
            series[idx]["high"] += 1

    peak = max((b["count"] for b in series), default=0)
    return {"buckets": series, "peak": peak, "span_sec": span}


def by_hour(conn, city_id=None, window_min=10080):
    """Sightings grouped by local hour-of-day, for the activity clock."""
    where, params = _scope(city_id, window_min)
    rows = dbmod.query(conn, "SELECT ts FROM sightings WHERE " + where, params)
    hours = [0] * 24
    for row in rows:
        hours[time.localtime(row["ts"]).tm_hour] += 1
    peak = max(hours) if hours else 0
    return {"hours": [{"hour": h, "count": c} for h, c in enumerate(hours)], "peak": peak}


def by_area(conn, city_id=None, window_min=1440, limit=8):
    where, params = _scope(city_id, window_min)
    rows = dbmod.query(
        conn,
        "SELECT area, COUNT(*) AS count, AVG(confidence) AS avg_conf, MAX(ts) AS last_ts "
        "FROM sightings WHERE " + where + " GROUP BY area ORDER BY count DESC LIMIT ?",
        params + [limit],
    )
    total = sum(r["count"] for r in rows) or 1
    return [{"area": r["area"], "count": r["count"],
             "avg_confidence": round(r["avg_conf"] or 0, 1),
             "share": round(r["count"] * 100.0 / total, 1),
             "last_ts": r["last_ts"]}
            for r in rows]


def confidence_distribution(conn, city_id=None, window_min=1440):
    where, params = _scope(city_id, window_min)
    rows = dbmod.query(conn, "SELECT confidence FROM sightings WHERE " + where, params)
    bands = [
        ("0-39", 0, 40, "muted"),
        ("40-59", 40, 60, "white"),
        ("60-74", 60, 75, "orange"),
        ("75-84", 75, 85, "cyan"),
        ("85-100", 85, 101, "green"),
    ]
    counts = []
    for label, lo, hi, tone in bands:
        n = sum(1 for r in rows if lo <= r["confidence"] < hi)
        counts.append({"band": label, "count": n, "tone": tone})
    total = sum(c["count"] for c in counts) or 1
    for c in counts:
        c["share"] = round(c["count"] * 100.0 / total, 1)
    return counts


def by_source(conn, city_id=None, window_min=1440):
    where, params = _scope(city_id, window_min)
    rows = dbmod.query(
        conn,
        "SELECT source, COUNT(*) AS count FROM sightings WHERE " + where +
        " GROUP BY source ORDER BY count DESC",
        params,
    )
    total = sum(r["count"] for r in rows) or 1
    return [{"source": r["source"], "count": r["count"],
             "share": round(r["count"] * 100.0 / total, 1)} for r in rows]


def camera_activity(conn, city_id=None, window_min=1440, limit=6):
    clauses = ["d.ts >= ?"]
    params = [time.time() - window_min * 60]
    if city_id and city_id != "all":
        clauses.append("c.city_id = ?")
        params.append(city_id)
    rows = dbmod.query(
        conn,
        "SELECT d.camera_id, c.label, COUNT(*) AS count, MAX(d.ts) AS last_ts, "
        "AVG(d.confidence) AS avg_conf FROM camera_detections d "
        "JOIN cameras c ON c.id = d.camera_id WHERE " + " AND ".join(clauses) +
        " GROUP BY d.camera_id ORDER BY count DESC LIMIT ?",
        params + [limit],
    )
    return [{"camera_id": r["camera_id"], "label": r["label"], "count": r["count"],
             "avg_confidence": round(r["avg_conf"] or 0, 1), "last_ts": r["last_ts"]}
            for r in rows]


def full(conn, city_id=None, window_min=1440):
    return {
        "summary": summary(conn, city_id, window_min),
        "timeline": by_bucket(conn, city_id, window_min),
        "hours": by_hour(conn, city_id, max(window_min, 1440)),
        "areas": by_area(conn, city_id, window_min),
        "confidence": confidence_distribution(conn, city_id, window_min),
        "sources": by_source(conn, city_id, window_min),
        "cameras": camera_activity(conn, city_id, window_min),
        "generated_at": time.time(),
    }
