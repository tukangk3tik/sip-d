import os

from flask import Flask, jsonify

from sipd.db import init_db


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SIPD_DB=os.environ.get("SIPD_DB", "data/sip-d.db"),
        SIPD_BASE_URL=os.environ.get("SIPD_BASE_URL", ""),
    )
    app.config.from_mapping(config or {})
    init_db(app.config["SIPD_DB"])

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app
