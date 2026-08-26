from flask import Flask, jsonify


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(config or {})

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app
