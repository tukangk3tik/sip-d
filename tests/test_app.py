from sipd import create_app


def test_healthz_returns_json():
    app = create_app({"TESTING": True})
    response = app.test_client().get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
