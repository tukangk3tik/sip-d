import os

from flask import Flask, jsonify

from sipd.db import init_db
from sipd.auth import security_headers
from sipd.routes import register_routes


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder="../static")
    app.config.from_mapping(
        SIPD_DB=os.environ.get("SIPD_DB", "data/sip-d.db"),
        SIPD_BASE_URL=os.environ.get("SIPD_BASE_URL", ""),
        SIPD_METALS_API_KEY=os.environ.get("SIPD_METALS_API_KEY", ""),
        SIPD_FINNHUB_API_KEY=os.environ.get("SIPD_FINNHUB_API_KEY", ""),
        SIPD_QUOTE_CACHE_TTL_SECONDS=int(os.environ.get("SIPD_QUOTE_CACHE_TTL_SECONDS", "900")),
        SIPD_QUOTE_BACKOFF_INITIAL_SECONDS=int(os.environ.get("SIPD_QUOTE_BACKOFF_INITIAL_SECONDS", "60")),
        SIPD_QUOTE_BACKOFF_MAX_SECONDS=int(os.environ.get("SIPD_QUOTE_BACKOFF_MAX_SECONDS", "1800")),
        SIPD_YAHOO_TIMEOUT_SECONDS=int(os.environ.get("SIPD_YAHOO_TIMEOUT_SECONDS", "6")),
        SIPD_YAHOO_BATCH_SIZE=int(os.environ.get("SIPD_YAHOO_BATCH_SIZE", "64")),
    )
    app.config.from_mapping(config or {})
    init_db(app.config["SIPD_DB"])
    app.after_request(security_headers)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    register_routes(app)
    return app
