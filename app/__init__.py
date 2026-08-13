"""Spidey Tracker application factory."""

import logging
import os
import time

from flask import Flask, jsonify, render_template, request

from .config import Config


def create_app(config_object=Config, seed_demo=True):
    app = Flask(
        __name__,
        static_folder=os.path.join(config_object.BASE_DIR, "static"),
        template_folder=os.path.join(config_object.BASE_DIR, "templates"),
    )
    app.config.from_object(config_object)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    from . import db as dbmod
    from . import seed as seedmod
    from . import events
    from .api import register as register_api
    from .services.simulator import Simulator

    app.teardown_appcontext(dbmod.close_db)

    dbmod.init_db(app)
    seedmod.seed_reference_data(app)

    if seed_demo and not seedmod.has_sightings(app):
        app.logger.info("No sightings found - seeding 24h of demo history.")
        seedmod.seed_demo_history(app)
    else:
        seedmod.refresh_network_stats(app)

    register_api(app)

    # Persisted demo-mode preference wins over the environment default.
    with dbmod.connection(app.config["DATABASE"]) as conn:
        stored = dbmod.get_setting(conn, "demo_mode")
    demo_enabled = (stored == "1") if stored is not None else app.config["DEMO_MODE_DEFAULT"]

    simulator = Simulator(app)
    app.extensions["simulator"] = simulator
    simulator.start(enabled=demo_enabled)

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            version=app.config["VERSION"],
            demo_mode=simulator.enabled,
        )

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "version": app.config["VERSION"],
                        "ts": time.time()})

    @app.errorhandler(404)
    def not_found(_err):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found", "path": request.path}), 404
        return render_template("index.html", version=app.config["VERSION"],
                               demo_mode=simulator.enabled), 200

    @app.errorhandler(413)
    def too_large(_err):
        limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return jsonify({"error": "Upload exceeds the %d MB limit." % limit}), 413

    @app.errorhandler(500)
    def server_error(err):
        app.logger.exception("unhandled error: %s", err)
        return jsonify({"error": "Internal error", "detail": str(err)[:200]}), 500

    @app.after_request
    def no_store_api(response):
        if request.path.startswith("/api/") and request.path != "/api/stream":
            response.headers["Cache-Control"] = "no-store"
        return response

    app.logger.info("Spidey Tracker v%s ready (demo mode: %s)",
                    app.config["VERSION"], "ON" if simulator.enabled else "OFF")
    return app
