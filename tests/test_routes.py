import re


def test_first_user_setup_creates_session(client):
    page = client.get("/setup")
    assert page.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    response = client.post("/setup", data={
        "csrf_token": token,
        "username": "owner",
        "password": "correct horse battery staple",
    })

    assert response.status_code == 303
    assert response.headers["Location"] == "/"
    assert response.headers.get("Set-Cookie", "").startswith("sipd_session=")


def test_login_and_logout_use_existing_session_store(client, existing_session):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post("/login", data={
        "csrf_token": token,
        "username": "owner",
        "password": "correct horse battery staple",
    })
    assert response.status_code == 303
    assert response.headers["Location"] == "/"

    token = client.get("/").text.split('data-csrf="')[1].split('"')[0]
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 303
    assert response.headers["Location"] == "/login"
