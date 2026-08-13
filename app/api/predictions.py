"""Prediction endpoints."""

from flask import Blueprint, current_app, jsonify, request

from ..services import prediction as service

bp = Blueprint("predictions", __name__, url_prefix="/api/predictions")


@bp.get("")
def get_prediction():
    city = request.args.get("city") or "nyc"
    if request.args.get("refresh") in ("1", "true"):
        payload = service.recompute(current_app._get_current_object(), city)
    else:
        payload = service.current(current_app._get_current_object(), city)
    return jsonify(payload)


@bp.post("/recompute")
def recompute():
    payload = request.get_json(silent=True) or {}
    city = payload.get("city") or request.args.get("city") or "nyc"
    return jsonify(service.recompute(current_app._get_current_object(), city))
