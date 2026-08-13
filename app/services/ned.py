"""NED AI - the in-app assistant.

Two engines, always labelled to the user:

  "claude" - real Claude API with tool calling, active only when
             ANTHROPIC_API_KEY is set. NED can call the same tools the local
             engine uses, so both engines answer from live database state.
  "local"  - a deterministic intent engine. No language model: it matches
             intent, runs a real query, and renders a templated answer. The UI
             shows "LOCAL INTENT ENGINE" so nobody mistakes it for a model.

Both return the same envelope: {reply, engine, engine_label, actions, data}.
`actions` are executed by the frontend, which is how NED can drive the map,
open panels and change filters.
"""

import json
import re
import time
import urllib.error
import urllib.request

from .. import db as dbmod
from . import geo, prediction
from . import sightings as sightings_service

SYSTEM_PROMPT = """You are NED, the assistant inside Spidey Tracker - a FICTIONAL \
Spider-Man sighting-tracking dashboard built as a demo.

Rules you must follow:
- Answer only from data returned by your tools. Never invent sighting counts, \
confidence values, locations or probabilities.
- Much of the data is simulated by a demo network and marked is_demo. When you \
report on simulated records, say they are demo data.
- This system has no access to real CCTV, satellites, law enforcement or any \
real person's location. Never imply otherwise.
- Keep replies short and punchy, in the voice of an enthusiastic teenage \
operator running a command console. Two or three sentences maximum.
- Use the ui_action tool when the user asks you to show, open, zoom, focus or \
filter something.
"""


# --- tool implementations (shared by both engines) -----------------------

def tool_recent_sightings(app, conn, city_id=None, limit=8, min_confidence=None):
    args = {"city": city_id or "all", "min_confidence": min_confidence}
    rows, total = sightings_service.list_sightings(conn, args, limit=int(limit))
    return {
        "total_matching": total,
        "returned": len(rows),
        "sightings": [
            {
                "id": r["id"], "ref": r["ref"], "area": r["area"], "city": r["city_name"],
                "confidence": r["confidence"], "status": r["status"], "source": r["source"],
                "minutes_ago": round(r["age_sec"] / 60.0, 1),
                "direction": r["direction_label"], "speed_kmh": r["speed_kmh"],
                "is_demo": r["is_demo"],
            }
            for r in rows
        ],
    }


def tool_prediction(app, conn, city_id):
    payload = prediction.current(app, city_id)
    return {
        "city_id": city_id,
        "confidence": payload["confidence"],
        "eta_min": payload["eta_min"],
        "eta_max": payload["eta_max"],
        "samples": payload.get("samples", 0),
        "sparse": payload.get("sparse", False),
        "method": payload.get("method"),
        "vector": payload.get("vector"),
        "candidates": [
            {"name": c["name"], "probability": c["probability"],
             "distance_km": c["distance_km"], "eta_min": c["eta_min"]}
            for c in payload.get("candidates", [])[:5]
        ],
    }


def tool_statistics(app, conn, city_id=None, window_hours=24):
    from . import analytics
    return analytics.summary(conn, city_id, float(window_hours) * 60)


def tool_cameras(app, conn, city_id=None, status=None):
    clauses, params = [], []
    if city_id and city_id != "all":
        clauses.append("c.city_id = ?")
        params.append(city_id)
    if status:
        clauses.append("c.status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = dbmod.query(
        conn,
        "SELECT c.id, c.label, c.status, c.is_mock, ci.name AS city "
        "FROM cameras c JOIN cities ci ON ci.id = c.city_id" + where +
        " ORDER BY c.id LIMIT 40",
        params,
    )
    return {"cameras": [dict(r) for r in rows], "count": len(rows)}


def tool_busiest_areas(app, conn, city_id=None, window_hours=24, limit=5):
    clauses = ["ts >= ?"]
    params = [time.time() - float(window_hours) * 3600]
    if city_id and city_id != "all":
        clauses.append("city_id = ?")
        params.append(city_id)
    rows = dbmod.query(
        conn,
        "SELECT area, COUNT(*) AS count, AVG(confidence) AS avg_conf FROM sightings "
        "WHERE " + " AND ".join(clauses) +
        " GROUP BY area ORDER BY count DESC LIMIT ?",
        params + [int(limit)],
    )
    return {"areas": [{"area": r["area"], "count": r["count"],
                       "avg_confidence": round(r["avg_conf"], 1)} for r in rows]}


def tool_explain_sighting(app, conn, sighting_id):
    record = sightings_service.get(conn, int(sighting_id))
    if not record:
        return {"error": "No sighting with id %s." % sighting_id}
    analysis = record.get("analysis") or {}
    return {
        "ref": record["ref"], "area": record["area"], "confidence": record["confidence"],
        "status": record["status"], "source": record["source"],
        "is_demo": record["is_demo"],
        "analysis_engine": analysis.get("label"),
        "is_real_model": analysis.get("is_real_model", False),
        "visual_match": analysis.get("visual_match"),
        "motion_match": analysis.get("motion_match"),
        "pattern_match": analysis.get("pattern_match"),
        "location_match": analysis.get("location_match"),
        "notes": analysis.get("notes"),
    }


TOOLS = {
    "recent_sightings": tool_recent_sightings,
    "get_prediction": tool_prediction,
    "get_statistics": tool_statistics,
    "list_cameras": tool_cameras,
    "busiest_areas": tool_busiest_areas,
    "explain_sighting": tool_explain_sighting,
}

TOOL_SCHEMAS = [
    {
        "name": "recent_sightings",
        "description": "List the most recent sightings, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city_id": {"type": "string", "description": "City id, or 'all'."},
                "limit": {"type": "integer"},
                "min_confidence": {"type": "number"},
            },
        },
    },
    {
        "name": "get_prediction",
        "description": "Current movement prediction for a city: candidate districts with probabilities, ETA and confidence.",
        "input_schema": {
            "type": "object",
            "properties": {"city_id": {"type": "string"}},
            "required": ["city_id"],
        },
    },
    {
        "name": "get_statistics",
        "description": "Aggregate counters and distributions for a time window.",
        "input_schema": {
            "type": "object",
            "properties": {"city_id": {"type": "string"},
                           "window_hours": {"type": "number"}},
        },
    },
    {
        "name": "list_cameras",
        "description": "Camera network inventory and live status.",
        "input_schema": {
            "type": "object",
            "properties": {"city_id": {"type": "string"},
                           "status": {"type": "string"}},
        },
    },
    {
        "name": "busiest_areas",
        "description": "Areas ranked by sighting count in a window.",
        "input_schema": {
            "type": "object",
            "properties": {"city_id": {"type": "string"},
                           "window_hours": {"type": "number"},
                           "limit": {"type": "integer"}},
        },
    },
    {
        "name": "explain_sighting",
        "description": "Why a given sighting scored the confidence it did.",
        "input_schema": {
            "type": "object",
            "properties": {"sighting_id": {"type": "integer"}},
            "required": ["sighting_id"],
        },
    },
    {
        "name": "ui_action",
        "description": (
            "Drive the dashboard UI. Use when the user asks to show, open, focus, "
            "zoom or filter. Types: map.focus (latitude, longitude, zoom), "
            "sighting.open (sighting_id), panel.open (panel: feed|cameras|analytics|"
            "prediction|network|report), map.mode (mode: map|satellite|terrain|heatmap|"
            "prediction|radar), filter.set (min_confidence, window_minutes, source), "
            "city.set (city_id)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "zoom": {"type": "number"},
                "sighting_id": {"type": "integer"},
                "panel": {"type": "string"},
                "mode": {"type": "string"},
                "city_id": {"type": "string"},
                "min_confidence": {"type": "number"},
                "window_minutes": {"type": "number"},
                "source": {"type": "string"},
            },
            "required": ["type"],
        },
    },
]


# --- Claude engine -------------------------------------------------------

def _claude_request(app, body):
    req = urllib.request.Request(
        app.config["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": app.config["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=app.config["AI_TIMEOUT"]) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ask_claude(app, conn, message, history, context):
    messages = []
    for item in history[-10:]:
        messages.append({"role": item["role"], "content": item["content"]})
    messages.append({
        "role": "user",
        "content": "%s\n\n[console context: %s]" % (message, json.dumps(context)),
    })

    actions, used_tools = [], []

    for _ in range(5):   # bounded tool-use loop
        body = {
            "model": app.config["ANTHROPIC_MODEL"],
            "max_tokens": 700,
            "system": SYSTEM_PROMPT,
            "tools": TOOL_SCHEMAS,
            "messages": messages,
        }
        response = _claude_request(app, body)
        content = response.get("content", [])
        messages.append({"role": "assistant", "content": content})

        tool_results = []
        text_parts = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                name = block.get("name")
                args = block.get("input") or {}
                used_tools.append(name)
                if name == "ui_action":
                    actions.append(args)
                    result = {"ok": True, "queued": args.get("type")}
                else:
                    fn = TOOLS.get(name)
                    try:
                        result = fn(app, conn, **args) if fn else {"error": "unknown tool"}
                    except Exception as exc:
                        result = {"error": str(exc)}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("id"),
                    "content": json.dumps(result, default=str)[:6000],
                })

        if response.get("stop_reason") == "tool_use" and tool_results:
            messages.append({"role": "user", "content": tool_results})
            continue

        return {
            "reply": "\n".join(p for p in text_parts if p).strip() or "...",
            "engine": "claude",
            "engine_label": "CLAUDE %s" % app.config["ANTHROPIC_MODEL"].upper(),
            "is_real_model": True,
            "actions": actions,
            "tools_used": used_tools,
        }

    return {
        "reply": "Tool loop ran long - try asking something more specific.",
        "engine": "claude", "engine_label": "CLAUDE (TRUNCATED)",
        "is_real_model": True, "actions": actions, "tools_used": used_tools,
    }


# --- local intent engine -------------------------------------------------

# Order matters: the first pattern that matches wins, so more specific
# questions ("why is this high confidence") must be listed before the broader
# ones they contain ("high confidence").
INTENTS = [
    ("greeting",   r"^\s*(hi|hey|hello|yo|sup)\b"),
    ("explain",    r"\b(why|explain|reason|how come|what makes)\b"),
    ("prediction", r"\b(predict\w*|heading|where.*(go|head|next)|next location|route|forecast)\b"),
    ("busiest",    r"\b(busiest|most activity|hot ?spot|most sightings|where.*most)\b"),
    ("recent_hour", r"\b(last|past)\s+(hour|30\s*min|60\s*min)\b"),
    ("high_conf",  r"\b(high[- ]?confidence|confirmed|verified|best)\b"),
    ("latest",     r"\b(latest|last|most recent|newest)\b"),
    ("cameras",    r"\b(cameras?|cams?|cctv|feeds?)\b"),
    ("network",    r"\b(network|global|worldwide|other cit\w*)\b"),
    ("stats",      r"\b(stats?|statistics?|summary|totals?|how many|counts?|numbers|today'?s?)\b"),
    ("help",       r"\b(help|what can you|commands|how do i)\b"),
    ("zoom",       r"\b(zoom|focus|cent(er|re)|go to|show me)\b"),
]


def _detect_intent(text):
    low = text.lower()
    for name, pattern in INTENTS:
        if re.search(pattern, low):
            return name
    return "unknown"


def _find_area(conn, text, city_id):
    """Match a district name mentioned in free text."""
    low = text.lower()
    rows = dbmod.query(
        conn, "SELECT id, name, latitude, longitude FROM locations WHERE city_id = ?",
        (city_id,),
    )
    best = None
    for row in rows:
        name = row["name"].lower()
        if name in low:
            if best is None or len(name) > len(best["name"]):
                best = dict(row)
        else:
            head = name.split()[0]
            if len(head) > 4 and head in low and best is None:
                best = dict(row)
    return best


def _find_city(conn, text):
    low = text.lower()
    for row in dbmod.query(conn, "SELECT id, name, latitude, longitude, default_zoom FROM cities"):
        if row["name"].lower() in low or row["id"] in low.split():
            return dict(row)
    return None


def _fmt_ago(seconds):
    minutes = seconds / 60.0
    if minutes < 1:
        return "%d sec ago" % int(seconds)
    if minutes < 60:
        return "%d min ago" % int(minutes)
    return "%.1f hr ago" % (minutes / 60.0)


def ask_local(app, conn, message, history, context):
    city_id = context.get("city_id") or "nyc"
    intent = _detect_intent(message)
    actions, data = [], {}

    def reply(text):
        return {
            "reply": text, "engine": "local",
            "engine_label": "LOCAL INTENT ENGINE",
            "is_real_model": False, "actions": actions,
            "data": data, "intent": intent,
        }

    # An explicit camera id anywhere in the message wins over intent matching.
    cam_match = re.search(r"\bcam(?:era)?[\s\-]?0*(\d{1,3})\b", message, re.I)
    if cam_match:
        cam_id = "CAM-%02d" % int(cam_match.group(1))
        row = dbmod.query(
            conn, "SELECT * FROM cameras WHERE id = ?", (cam_id,), one=True
        )
        if row:
            det = dbmod.query(
                conn,
                "SELECT ts, confidence FROM camera_detections WHERE camera_id = ? "
                "ORDER BY ts DESC LIMIT 1", (cam_id,), one=True
            )
            actions.append({"type": "panel.open", "panel": "cameras"})
            actions.append({"type": "camera.open", "camera_id": cam_id})
            actions.append({"type": "map.focus", "latitude": row["latitude"],
                            "longitude": row["longitude"], "zoom": 15})
            data["camera"] = dict(row)
            last = ("last detection %s at %.0f%%" % (_fmt_ago(time.time() - det["ts"]),
                                                     det["confidence"])) if det else \
                   "no detections logged yet"
            return reply("%s (%s) is %s - %s. Opening the feed."
                         % (cam_id, row["label"], row["status"].upper(), last))
        return reply("I don't have a camera with id %s on the network." % cam_id)

    city = _find_city(conn, message)
    if city and city["id"] != city_id:
        city_id = city["id"]
        actions.append({"type": "city.set", "city_id": city_id})
        actions.append({"type": "map.focus", "latitude": city["latitude"],
                        "longitude": city["longitude"], "zoom": city["default_zoom"]})

    area = _find_area(conn, message, city_id)
    if area and intent in ("zoom", "unknown", "stats"):
        actions.append({"type": "map.focus", "latitude": area["latitude"],
                        "longitude": area["longitude"], "zoom": 14})
        rows = dbmod.query(
            conn,
            "SELECT COUNT(*) AS c, AVG(confidence) AS avg FROM sightings "
            "WHERE location_id = ? AND ts >= ?",
            (area["id"], time.time() - 86400),
            one=True,
        )
        data["area"] = {"name": area["name"], "count": rows["c"],
                        "avg_confidence": round(rows["avg"] or 0, 1)}
        return reply("Zooming to %s. %d sightings there in the last 24 hours, "
                     "averaging %.0f%% confidence."
                     % (area["name"], rows["c"], rows["avg"] or 0))

    if intent == "prediction":
        pred = tool_prediction(app, conn, city_id)
        data["prediction"] = pred
        actions.append({"type": "panel.open", "panel": "prediction"})
        actions.append({"type": "map.mode", "mode": "prediction"})
        if not pred["candidates"]:
            return reply("Not enough recent movement in that city to project a route yet.")
        lead = pred["candidates"][0]
        rest = ", ".join("%s %.0f%%" % (c["name"], c["probability"])
                         for c in pred["candidates"][1:3])
        vector = pred.get("vector")
        heading = (" Movement vector reads %s at %.0f km/h."
                   % (vector["compass"], vector["speed_kmh"])) if vector else ""
        return reply("Based on the last %d sightings, %s leads at %.0f%%.%s "
                     "Runners-up: %s. ETA window %s-%s min."
                     % (pred.get("samples", 0) or len(pred["candidates"]),
                        lead["name"], lead["probability"], heading, rest or "none",
                        pred["eta_min"], pred["eta_max"]))

    if intent in ("latest", "recent_hour", "high_conf"):
        min_conf = 85 if intent == "high_conf" else None
        window = 60 if intent == "recent_hour" else None
        args = {"city": city_id, "min_confidence": min_conf}
        if window:
            args["window"] = window
        rows, total = sightings_service.list_sightings(conn, args, limit=5)
        data["sightings"] = rows
        if not rows:
            return reply("Nothing matches that in %s right now." % city_id.upper())
        top = rows[0]
        actions.append({"type": "sighting.open", "sighting_id": top["id"]})
        actions.append({"type": "map.focus", "latitude": top["latitude"],
                        "longitude": top["longitude"], "zoom": 15})
        if min_conf:
            actions.append({"type": "filter.set", "min_confidence": 85})
        if window:
            actions.append({"type": "filter.set", "window_minutes": window})
        demo_note = " (demo network data)" if top["is_demo"] else ""
        return reply("Latest is %s in %s, %.0f%% confidence, %s%s. "
                     "%d total match your query - opening it now."
                     % (top["ref"], top["area"], top["confidence"],
                        _fmt_ago(top["age_sec"]), demo_note, total))

    if intent == "busiest":
        result = tool_busiest_areas(app, conn, city_id, 24, 5)
        data["areas"] = result["areas"]
        actions.append({"type": "map.mode", "mode": "heatmap"})
        if not result["areas"]:
            return reply("No activity logged in the last 24 hours there.")
        top = result["areas"][0]
        rest = ", ".join("%s (%d)" % (a["area"], a["count"]) for a in result["areas"][1:4])
        return reply("%s is hottest with %d sightings in 24h at %.0f%% average confidence. "
                     "Then %s. Switching to heatmap."
                     % (top["area"], top["count"], top["avg_confidence"], rest or "nothing else"))

    if intent == "stats":
        from . import analytics
        summary = analytics.summary(conn, city_id, 24 * 60)
        data["stats"] = summary
        actions.append({"type": "panel.open", "panel": "analytics"})
        return reply("Last 24h in %s: %d sightings, %d high-confidence, %d active, "
                     "%d AI-verified. Average confidence %.0f%%."
                     % (summary["city_name"], summary["total"], summary["high_confidence"],
                        summary["active"], summary["ai_verified"],
                        summary["avg_confidence"]))

    if intent == "cameras":
        result = tool_cameras(app, conn, city_id)
        data["cameras"] = result["cameras"]
        actions.append({"type": "panel.open", "panel": "cameras"})
        by_status = {}
        for cam in result["cameras"]:
            by_status[cam["status"]] = by_status.get(cam["status"], 0) + 1
        breakdown = ", ".join("%d %s" % (v, k.upper()) for k, v in sorted(by_status.items()))
        return reply("%d cameras on the %s grid: %s. All feeds are mock streams - "
                     "this system has no access to real CCTV."
                     % (len(result["cameras"]), city_id.upper(), breakdown))

    if intent == "network":
        rows = dbmod.query(
            conn,
            "SELECT c.name, n.total, n.last_24h FROM network_stats n "
            "JOIN cities c ON c.id = n.city_id ORDER BY n.total DESC",
        )
        data["network"] = [dict(r) for r in rows]
        actions.append({"type": "panel.open", "panel": "network"})
        listing = ", ".join("%s %d" % (r["name"], r["total"]) for r in rows[:4])
        return reply("Global tallies: %s. Opening the network panel." % listing)

    if intent == "explain":
        sid = context.get("selected_sighting_id")
        match = re.search(r"#?(\d{2,6})", message)
        if match:
            row = dbmod.query(conn, "SELECT id FROM sightings WHERE ref LIKE ?",
                              ("%" + match.group(1),), one=True)
            if row:
                sid = row["id"]
        if not sid:
            return reply("Select a sighting first, then ask me why it scored that way.")
        info = tool_explain_sighting(app, conn, sid)
        data["explain"] = info
        actions.append({"type": "sighting.open", "sighting_id": sid})
        if info.get("error"):
            return reply(info["error"])
        return reply("%s scored %.0f%% via %s. Visual %.0f, motion %.0f, pattern %.0f, "
                     "location %.0f. %s"
                     % (info["ref"], info["confidence"], info["analysis_engine"],
                        info["visual_match"] or 0, info["motion_match"] or 0,
                        info["pattern_match"] or 0, info["location_match"] or 0,
                        info["notes"] or ""))

    if intent == "greeting":
        return reply("NED online. Ask me for the latest sighting, where the subject is "
                     "heading, the busiest area, camera status, or today's numbers.")

    if intent == "help" or intent == "unknown":
        return reply("I'm running the local intent engine (no language model configured), "
                     "so keep it literal: \"latest sighting\", \"where is he heading\", "
                     "\"busiest area\", \"show camera 7\", \"zoom to Queens\", "
                     "\"today's stats\", \"why is this high confidence\".")

    if intent == "zoom":
        row = dbmod.query(conn, "SELECT latitude, longitude, default_zoom, name FROM cities "
                                "WHERE id = ?", (city_id,), one=True)
        if row:
            actions.append({"type": "map.focus", "latitude": row["latitude"],
                            "longitude": row["longitude"], "zoom": row["default_zoom"]})
            return reply("Centred on %s." % row["name"])

    return reply("Not sure what you mean - try \"latest sighting\" or \"where is he heading\".")


# --- entry point ---------------------------------------------------------

def ask(app, conn, session_id, message, context=None):
    context = context or {}
    history = _load_history(conn, session_id)

    if app.config["ANTHROPIC_API_KEY"]:
        try:
            result = ask_claude(app, conn, message, history, context)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as exc:
            result = ask_local(app, conn, message, history, context)
            result["reply"] = ("Claude is unreachable (%s), so I'm on the local intent "
                               "engine. %s" % (type(exc).__name__, result["reply"]))
            result["degraded"] = True
    else:
        result = ask_local(app, conn, message, history, context)

    now = time.time()
    dbmod.execute(
        conn,
        "INSERT INTO ai_messages(session_id, role, content, engine, payload, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (session_id, "user", message, None, None, now),
    )
    dbmod.execute(
        conn,
        "INSERT INTO ai_messages(session_id, role, content, engine, payload, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (session_id, "assistant", result["reply"], result["engine"],
         json.dumps({"actions": result.get("actions", []),
                     "data": result.get("data", {})}, default=str), now + 0.001),
    )
    result["ts"] = now
    return result


def _load_history(conn, session_id, limit=12):
    rows = dbmod.query(
        conn,
        "SELECT role, content FROM ai_messages WHERE session_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (session_id, limit),
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def history(conn, session_id, limit=40):
    rows = dbmod.query(
        conn,
        "SELECT role, content, engine, payload, created_at FROM ai_messages "
        "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit),
    )
    out = []
    for row in reversed(rows):
        item = {"role": row["role"], "content": row["content"],
                "engine": row["engine"], "ts": row["created_at"]}
        if row["payload"]:
            try:
                item.update(json.loads(row["payload"]))
            except ValueError:
                pass
        out.append(item)
    return out


def engine_status(app):
    if app.config["ANTHROPIC_API_KEY"]:
        return {"engine": "claude", "label": "CLAUDE %s" % app.config["ANTHROPIC_MODEL"].upper(),
                "is_real_model": True, "ready": True}
    return {"engine": "local", "label": "LOCAL INTENT ENGINE",
            "is_real_model": False, "ready": True,
            "note": "Set ANTHROPIC_API_KEY to enable the language model."}
