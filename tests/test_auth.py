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
