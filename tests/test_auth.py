def test_existing_session_cookie_authenticates_dashboard(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    response = client.get("/")

    assert response.status_code == 200
    assert b"owner" in response.data


def test_dashboard_requires_session(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
