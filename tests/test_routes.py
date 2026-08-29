import re
from datetime import datetime, timezone
from decimal import Decimal

from sipd.db import connect
from sipd.providers import Quote


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
    assert response.headers["Location"] == "/assets"


def test_asset_edit_and_archive_routes(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    assert client.get("/assets/1/edit").status_code == 200
    assert client.post("/assets/1/archive", data={"csrf_token": "csrf"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT active FROM assets WHERE id=1").fetchone()[0] == 0
    finally:
        db.close()


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


def test_second_transaction_replays_existing_utc_ledger(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
        db.execute("INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,idempotency_key) VALUES(1,1,'deposit','1','1','IDR','1','2026-08-26T12:00:00Z','first')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/transactions", data={"csrf_token": "csrf", "asset_id": "1", "kind": "deposit", "quantity": "1", "price": "1", "fx_rate": "1", "occurred_at": "2026-08-27T12:00", "idempotency_key": "second"})
    assert response.status_code == 303


def test_settings_currency_update_is_owned(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/settings/currency", data={"csrf_token": "csrf", "currency": "USD"})
    assert response.status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT display_currency FROM user_settings WHERE user_id=1").fetchone()[0] == "USD"
    finally:
        db.close()


def test_dashboard_renders_jinja_navigation(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/transactions"' in page.text


def test_transactions_list_renders_without_asset_detail_context(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
        db.execute("INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,idempotency_key) VALUES(1,1,'deposit','1','1','IDR','1','2026-08-26T12:00:00Z','list')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/transactions")
    assert page.status_code == 200
    assert "Cash" in page.text


def test_ticker_lookup_route_and_static_assets(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    assert client.get("/settings/tickers").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/webawesome/styles/webawesome.css").status_code == 200


def test_refresh_creates_idempotent_snapshot_for_fixed_asset(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
        db.execute("INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,idempotency_key) VALUES(1,1,'deposit','10','1','IDR','1','2026-08-26T12:00:00Z','cash')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "once"})
    assert response.status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT total_value_idr FROM portfolio_snapshots WHERE refresh_key='once'").fetchone()[0] == "10"
        assert db.execute("SELECT quantity,price FROM portfolio_snapshot_items").fetchone()[0] == "10"
    finally:
        db.close()


def test_price_lookup_uses_yfinance_quote(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BBRI','share','IDR','automatic','yahoo','BBRI.JK')")
    finally:
        db.close()
    monkeypatch.setattr(routes, "yahoo_quotes", lambda symbols: ({"BBRI.JK": Quote(Decimal("4200"), "IDR", "Yahoo Finance (yfinance)", datetime.now(timezone.utc))}, {}))
    client.set_cookie("sipd_session", existing_session)
    response = client.get("/api/assets/1/price")
    assert response.status_code == 200
    assert response.get_json()["price"] == "4200"


def test_price_lookup_uses_kraken_quote(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BTC','BTC','USD','automatic','kraken','BTC')")
    finally:
        db.close()
    monkeypatch.setattr(routes, "quote_for_asset", lambda asset, **kwargs: Quote(Decimal("68000"), "USD", "Kraken", datetime.now(timezone.utc)))
    client.set_cookie("sipd_session", existing_session)
    assert client.get("/api/assets/1/price").get_json()["price"] == "68000"


def test_price_lookup_uses_last_known_price_when_yfinance_fails(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BBRI','share','IDR','automatic','yahoo','BBRI.JK')")
        db.execute("INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(1,1,'4100','IDR','Yahoo Finance (yfinance)','2026-08-26T12:00:00Z')")
    finally:
        db.close()
    monkeypatch.setattr(routes, "yahoo_quotes", lambda symbols: ({}, {"BBRI.JK": "Provider unavailable"}))
    client.set_cookie("sipd_session", existing_session)
    response = client.get("/api/assets/1/price")
    assert response.status_code == 200
    assert response.get_json()["source"] == "Yahoo Finance (yfinance) (last known)"


def test_refresh_saves_batched_yahoo_price(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BBRI','share','IDR','automatic','yahoo','BBRI.JK')")
    finally:
        db.close()
    monkeypatch.setattr(routes, "yahoo_quotes", lambda symbols: ({"BBRI.JK": Quote(Decimal("4200"), "IDR", "Yahoo Finance (yfinance)", datetime.now(timezone.utc))}, {}))
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "yahoo"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT price FROM asset_prices WHERE asset_id=1").fetchone()[0] == "4200"
    finally:
        db.close()


def test_exchange_rate_uses_last_known_rate_when_provider_fails(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO exchange_rates(user_id,base_currency,quote_currency,rate,source,priced_at) VALUES(1,'USD','IDR','16500','Frankfurter','2026-08-26T12:00:00Z')")
    finally:
        db.close()
    monkeypatch.setattr(routes, "usd_idr_quote", lambda: (_ for _ in ()).throw(ValueError("provider HTTP 429")))
    client.set_cookie("sipd_session", existing_session)
    response = client.get("/api/exchange-rate")
    assert response.status_code == 200
    assert response.get_json()["source"] == "Frankfurter (last known)"
