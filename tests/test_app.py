from sipd import create_app
from sipd.db import connect


def test_healthz_returns_json():
    app = create_app({"TESTING": True})
    response = app.test_client().get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_factory_initializes_configured_database(tmp_path):
    app = create_app({"TESTING": True, "SIPD_DB": str(tmp_path / "sip-d.db")})
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
    finally:
        db.close()
