"""API blueprint registration."""

from .sightings import bp as sightings_bp
from .cameras import bp as cameras_bp
from .analytics import bp as analytics_bp
from .network import bp as network_bp
from .predictions import bp as predictions_bp
from .ai import bp as ai_bp
from .system import bp as system_bp
from .search import bp as search_bp
from .stream import bp as stream_bp

BLUEPRINTS = (
    sightings_bp, cameras_bp, analytics_bp, network_bp,
    predictions_bp, ai_bp, system_bp, search_bp, stream_bp,
)


def register(app):
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
