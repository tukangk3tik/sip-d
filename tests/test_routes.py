import re

from sipd.db import connect


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


def test_asset_form_saves_owned_asset(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/assets", data={
        "csrf_token": "csrf", "name": "Cash", "type_id": "1", "unit": "IDR",
        "scale": "0", "quote_currency": "IDR", "pricing_mode": "fixed",
    })
    assert response.status_code == 303
    assert response.headers["Location"].startswith("/assets/")


def test_transaction_saves_for_owned_asset(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/transactions", data={
        "csrf_token": "csrf", "asset_id": "1", "kind": "deposit", "quantity": "1",
        "price": "1", "fx_rate": "1", "occurred_at": "2026-08-26T12:00", "idempotency_key": "one",
    })
    assert response.status_code == 303
    assert response.headers["Location"].startswith("/transactions/")
