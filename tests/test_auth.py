def test_existing_session_cookie_authenticates_dashboard(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    response = client.get("/")

    assert response.status_code == 200
    assert b"owner" in response.data


def test_dashboard_requires_session(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_login_limiter_blocks_eleventh_failed_attempt(client, existing_session):
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    for _ in range(10):
        assert client.post("/login", data={"csrf_token": token, "username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/login", data={"csrf_token": token, "username": "owner", "password": "wrong"}).status_code == 429
import re


def test_admin_required(app, client, existing_session):
    from sipd import auth
    @app.get("/admin-test")
    @auth.require_admin
    def admin_test():
        return "admin"
    assert client.get("/admin-test").status_code == 302
    client.set_cookie("sipd_session", existing_session)
    assert client.get("/admin-test").status_code == 403
    from sipd.db import connect
    db = connect(app.config["SIPD_DB"])
    db.execute("UPDATE users SET is_admin=1 WHERE username='owner'")
    db.close()
    assert client.get("/admin-test").status_code == 200


def test_fresh_setup_creates_admin(app, client):
    from sipd.db import connect
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/setup").text).group(1)
    assert client.post("/setup", data={"csrf_token": csrf, "username": "admin", "password": "a sufficiently long password"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    assert db.execute("SELECT is_admin FROM users").fetchone()[0] == 1
    db.close()
