"""Analytics endpoints. Time window comes from the client's 24H/7D/30D tabs."""

from flask import Blueprint, jsonify, request

from .. import db as dbmod
from ..services import analytics as service

bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")

PRESETS = {"1h": 60, "24h": 1440, "7d": 10080, "30d": 43200}


def _window():
    raw = (request.args.get("range") or request.args.get("window") or "24h").lower()
    if raw in PRESETS:
        return PRESETS[raw]
    try:
        return max(5.0, min(129600.0, float(raw)))
    except ValueError:
        return 1440


@bp.get("")
def get_analytics():
    conn = dbmod.get_db()
    city = request.args.get("city")
    return jsonify(service.full(conn, city, _window()))


@bp.get("/summary")
def get_summary():
    conn = dbmod.get_db()
    return jsonify(service.summary(conn, request.args.get("city"), _window()))


@bp.get("/timeline")
def get_timeline():
    conn = dbmod.get_db()
    try:
        buckets = max(6, min(96, int(request.args.get("buckets", 24))))
    except ValueError:
        buckets = 24
    return jsonify(service.by_bucket(conn, request.args.get("city"), _window(), buckets))


@bp.get("/areas")
def get_areas():
    conn = dbmod.get_db()
    return jsonify({"areas": service.by_area(conn, request.args.get("city"), _window())})
