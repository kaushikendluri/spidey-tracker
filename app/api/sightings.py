"""Sighting endpoints, including the multipart report form."""

import os
import time
import uuid

from flask import Blueprint, current_app, jsonify, request, send_file, abort

from .. import db as dbmod
from ..services import sightings as service
from ..services import ai_analysis

bp = Blueprint("sightings", __name__, url_prefix="/api/sightings")

ALLOWED_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ALLOWED_VIDEO = {".mp4", ".webm", ".mov", ".m4v", ".ogg"}


def _save_upload(file_storage, allowed):
    if not file_storage or not file_storage.filename:
        return None, None
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in allowed:
        return None, "Unsupported file type '%s'." % (ext or "unknown")
    upload_dir = current_app.config["UPLOAD_DIR"]
    os.makedirs(upload_dir, exist_ok=True)
    name = "%s%s" % (uuid.uuid4().hex, ext)
    path = os.path.join(upload_dir, name)
    file_storage.save(path)
    return path, None


@bp.get("")
def list_sightings():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 120))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400

    conn = dbmod.get_db()
    rows, total = service.list_sightings(conn, request.args, limit=limit, offset=offset)
    return jsonify({
        "sightings": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "server_time": time.time(),
    })


@bp.get("/<int:sighting_id>")
def get_sighting(sighting_id):
    conn = dbmod.get_db()
    record = service.get(conn, sighting_id)
    if not record:
        return jsonify({"error": "Sighting %d not found." % sighting_id}), 404

    # Neighbouring sightings give the detail panel its movement context.
    neighbours = dbmod.query(
        conn,
        "SELECT id, ref, area, latitude, longitude, ts, confidence FROM sightings "
        "WHERE city_id = ? AND id != ? AND ABS(ts - ?) < 7200 ORDER BY ABS(ts - ?) LIMIT 6",
        (record["city_id"], sighting_id, record["ts"], record["ts"]),
    )
    record["context"] = [dict(r) for r in neighbours]
    return jsonify(record)


@bp.post("")
def create_sighting():
    conn = dbmod.get_db()

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        payload = {k: v for k, v in request.form.items()}
        image_path, err = _save_upload(request.files.get("image"), ALLOWED_IMAGE)
        if err:
            return jsonify({"error": err, "field": "image"}), 400
        video_path, err = _save_upload(request.files.get("video"), ALLOWED_VIDEO)
        if err:
            return jsonify({"error": err, "field": "video"}), 400
        payload["image_path"] = image_path
        payload["video_path"] = video_path
    else:
        payload = request.get_json(silent=True) or {}

    # Reports submitted through the UI are operator-entered, never demo.
    payload.pop("is_demo", None)

    try:
        record = service.create(current_app._get_current_object(), conn, payload)
    except service.ValidationError as exc:
        return jsonify({"error": exc.message, "field": exc.field}), 400

    return jsonify(record), 201


@bp.patch("/<int:sighting_id>")
@bp.put("/<int:sighting_id>")
def update_sighting(sighting_id):
    conn = dbmod.get_db()
    payload = request.get_json(silent=True) or {}
    try:
        record = service.update(current_app._get_current_object(), conn, sighting_id, payload)
    except service.ValidationError as exc:
        return jsonify({"error": exc.message, "field": exc.field}), 400
    if not record:
        return jsonify({"error": "Sighting %d not found." % sighting_id}), 404
    return jsonify(record)


@bp.delete("/<int:sighting_id>")
def delete_sighting(sighting_id):
    conn = dbmod.get_db()
    ok = service.delete(current_app._get_current_object(), conn, sighting_id)
    if not ok:
        return jsonify({"error": "Sighting %d not found." % sighting_id}), 404
    return jsonify({"deleted": sighting_id})


@bp.post("/<int:sighting_id>/reanalyze")
def reanalyze(sighting_id):
    """Re-run analysis, e.g. after more history has accumulated around it."""
    conn = dbmod.get_db()
    raw = service.get_raw(conn, sighting_id)
    if not raw:
        return jsonify({"error": "Sighting %d not found." % sighting_id}), 404

    result = ai_analysis.analyze(current_app._get_current_object(), conn, dict(raw))
    ai_analysis.store(conn, sighting_id, result)
    record = service.update(
        current_app._get_current_object(), conn, sighting_id,
        {"confidence": result["probability"],
         "ai_verified": result["probability"] >= service.HIGH_CONFIDENCE
                        and result["engine"] != "demo"},
    )
    return jsonify(record)


def _media(sighting_id, column, mimetype_hint):
    conn = dbmod.get_db()
    row = dbmod.query(
        conn, "SELECT %s AS path FROM sightings WHERE id = ?" % column,
        (sighting_id,), one=True,
    )
    if not row or not row["path"]:
        abort(404)
    path = row["path"]
    # Never serve anything outside the configured upload directory.
    upload_dir = os.path.abspath(current_app.config["UPLOAD_DIR"])
    if not os.path.abspath(path).startswith(upload_dir + os.sep):
        abort(403)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


@bp.get("/<int:sighting_id>/image")
def sighting_image(sighting_id):
    return _media(sighting_id, "image_path", "image/*")


@bp.get("/<int:sighting_id>/video")
def sighting_video(sighting_id):
    return _media(sighting_id, "video_path", "video/*")
