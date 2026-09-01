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


def test_assets_list_renders_status_badges_and_actions(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,active) VALUES(1,1,'Cash','IDR','IDR','fixed',1)")
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,active) VALUES(1,1,'Old fund','unit','IDR','manual',0)")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/assets")

    assert "status-badge active" in page.text
    assert "status-badge inactive" in page.text
    assert "/assets/1/deactivate" in page.text
    assert "/assets/2/activate" in page.text
    assert "inactive-row" in page.text


def test_asset_can_be_deactivated_and_reactivated_from_list(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)

    assert client.post("/assets/1/deactivate", data={"csrf_token": "csrf"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT active FROM assets WHERE id=1").fetchone()[0] == 0
    finally:
        db.close()

    assert client.post("/assets/1/activate", data={"csrf_token": "csrf"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT active FROM assets WHERE id=1").fetchone()[0] == 1
    finally:
        db.close()


def test_asset_status_toggle_is_owned_and_csrf_protected(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO users(username,password_hash) VALUES('other','hash')")
        db.execute("INSERT INTO user_settings(user_id) VALUES(2)")
        db.execute("INSERT INTO investment_types(user_id,name) VALUES(2,'Cash')")
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(2,2,'Other','IDR','IDR','fixed')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)

    assert client.post("/assets/1/deactivate", data={"csrf_token": "wrong"}).status_code == 403
    assert client.post("/assets/1/deactivate", data={"csrf_token": "csrf"}).status_code == 404


def test_wallet_asset_cannot_be_deactivated(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    assert client.get("/wallet").status_code == 200
    db = connect(app.config["SIPD_DB"])
    try:
        wallet_id = db.execute("SELECT id FROM assets WHERE user_id=1 AND name='RDN'").fetchone()[0]
    finally:
        db.close()

    assert client.post(f"/assets/{wallet_id}/deactivate", data={"csrf_token": "csrf"}).status_code == 400


def test_inactive_asset_is_excluded_from_dashboard(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,active) VALUES(1,1,'Hidden','unit','IDR','manual',0)")
        db.execute("INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,idempotency_key) VALUES(1,1,'deposit','1','100','IDR','1','2026-08-26T12:00:00Z','hidden')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)

    page = client.get("/")
    assert "Hidden" not in page.text


def test_inactive_automatic_asset_is_excluded_from_refresh(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol,active) VALUES(1,1,'Hidden','share','IDR','automatic','yahoo','HIDE.JK',0)")
    finally:
        db.close()
    monkeypatch.setattr(routes, "yahoo_quotes", lambda symbols: (_ for _ in ()).throw(AssertionError("inactive asset should not refresh")))
    monkeypatch.setattr(routes, "usd_idr_quote", lambda: Quote(Decimal("16500"), "IDR", "Frankfurter", datetime.now(timezone.utc)))
    client.set_cookie("sipd_session", existing_session)

    assert client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "inactive"}).status_code == 303


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


def test_settings_language_update_is_owned(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/settings/language", data={"csrf_token": "csrf", "language": "EN"})
    assert response.status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT language FROM user_settings WHERE user_id=1").fetchone()[0] == "EN"
    finally:
        db.close()


def test_language_toggle_redirects_back_to_safe_page(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/settings/language", data={"csrf_token": "csrf", "language": "EN", "next": "/assets"})

    assert response.status_code == 303
    assert response.headers["Location"] == "/assets"
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT language FROM user_settings WHERE user_id=1").fetchone()[0] == "EN"
    finally:
        db.close()


def test_language_toggle_rejects_external_next_redirect(client, existing_session):
    client.set_cookie("sipd_session", existing_session)

    response = client.post("/settings/language", data={"csrf_token": "csrf", "language": "EN", "next": "https://example.com"})
    assert response.status_code == 303
    assert response.headers["Location"] == "/settings"

    response = client.post("/settings/language", data={"csrf_token": "csrf", "language": "ID", "next": "//example.com"})
    assert response.status_code == 303
    assert response.headers["Location"] == "/settings"


def test_sidebar_renders_language_toggle(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/assets")

    assert 'class="language-toggle"' in page.text
    assert 'name="language" value="ID"' in page.text
    assert 'name="language" value="EN"' in page.text
    assert 'name="next" value="/assets"' in page.text
    assert 'value="ID" class="secondary active"' in page.text

    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("UPDATE user_settings SET language='EN' WHERE user_id=1")
    finally:
        db.close()
    page = client.get("/assets")
    assert 'value="EN" class="secondary active"' in page.text


def test_dashboard_renders_jinja_navigation(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/transactions"' in page.text
    assert 'class="sidebar-nav"' in page.text
    assert "Portfolio" in page.text


def test_dashboard_renders_a_mobile_navigation_control(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/")

    assert 'id="nav-toggle"' in page.text
    assert 'aria-controls="sidebar"' in page.text
    assert 'id="nav-backdrop"' in page.text
    assert 'id="sidebar" tabindex="-1"' in page.text


def test_dashboard_renders_portfolio_summary(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
        db.execute("INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,idempotency_key) VALUES(1,1,'deposit','10','1','IDR','1','2026-08-26T12:00:00Z','dashboard')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/")
    assert "Total nilai" in page.text
    assert "Modal bersih" in page.text
    assert "Alokasi berdasarkan aset" in page.text
    assert "Cash" in page.text


def test_dashboard_uses_indonesian_by_default_with_card_lists(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Cash','IDR','IDR','fixed')")
        db.execute("INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,idempotency_key) VALUES(1,1,'deposit','10','1','IDR','1','2026-08-26T12:00:00Z','cards')")
        db.execute("INSERT INTO price_refreshes(user_id,refresh_key,status,error_summary,created_at) VALUES(1,'refresh','partial','BBCA failed; BTC failed','2026-08-27T12:00:00Z')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/")

    assert '<html lang="id">' in page.text
    assert "Aset terbesar" in page.text
    assert "Status penyegaran" in page.text
    assert "largest-card" in page.text
    assert "refresh-card" in page.text
    assert "BBCA failed" in page.text
    assert "BTC failed" in page.text


def test_dashboard_uses_english_when_selected(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("UPDATE user_settings SET language='EN' WHERE user_id=1")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/")

    assert '<html lang="en">' in page.text
    assert "Largest assets" in page.text
    assert "Aset terbesar" not in page.text


def test_wallet_page_provisions_fixed_idr_wallet(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    assert client.get("/wallet").status_code == 200
    db = connect(app.config["SIPD_DB"])
    try:
        row = db.execute("SELECT a.name,a.unit,a.quote_currency,a.pricing_mode,t.name FROM assets a JOIN investment_types t ON t.id=a.investment_type_id WHERE a.user_id=1").fetchone()
        assert tuple(row) == ("RDN", "IDR", "IDR", "fixed", "Wallet")
    finally:
        db.close()


def test_wallet_renames_legacy_rdn_wallet_type(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO investment_types(user_id,name) VALUES(1,'RDN Wallet')")
        legacy_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,?,'RDN','IDR','IDR','fixed')", (legacy_id,))
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    assert client.get("/wallet").status_code == 200
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT t.name FROM assets a JOIN investment_types t ON t.id=a.investment_type_id WHERE a.user_id=1 AND a.name='RDN'").fetchone()[0] == "Wallet"
    finally:
        db.close()


def test_wallet_top_up_and_withdraw_update_balance(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/wallet/top-up", data={"csrf_token": "csrf", "amount": "100000", "idempotency_key": "top"}).status_code == 303
    assert client.post("/wallet/withdraw", data={"csrf_token": "csrf", "amount": "25000", "idempotency_key": "withdraw"}).status_code == 303
    assert "75,000.00 IDR" in client.get("/wallet").text


def test_wallet_withdraw_rejects_insufficient_balance(client, existing_session):
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/wallet/withdraw", data={"csrf_token": "csrf", "amount": "1", "idempotency_key": "empty"})
    assert response.status_code == 400


def test_asset_buy_debits_wallet(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/wallet/top-up", data={"csrf_token": "csrf", "amount": "100", "occurred_at": "2026-08-26T11:59", "idempotency_key": "fund"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Fund','unit','IDR','manual')")
        asset_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        db.close()
    response = client.post("/transactions", data={"csrf_token": "csrf", "asset_id": asset_id, "kind": "buy", "quantity": "1", "price": "40", "fx_rate": "1", "occurred_at": "2026-08-26T12:00", "idempotency_key": "buy"})
    assert response.status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        rows = db.execute("SELECT kind,quantity FROM transactions WHERE user_id=1 AND asset_id=(SELECT id FROM assets WHERE user_id=1 AND name='RDN') ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [("deposit", "100"), ("withdrawal", "40")]
    finally:
        db.close()


def test_asset_buy_rejects_when_wallet_is_insufficient(client, existing_session, app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Fund','unit','IDR','manual')")
    finally:
        db.close()
    client.set_cookie("sipd_session", existing_session)
    response = client.post("/transactions", data={"csrf_token": "csrf", "asset_id": "1", "kind": "buy", "quantity": "1", "price": "40", "fx_rate": "1", "occurred_at": "2026-08-26T12:00", "idempotency_key": "unfunded"})
    assert response.status_code == 400
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT count(*) FROM transactions WHERE user_id=1").fetchone()[0] == 0
    finally:
        db.close()


def test_asset_sell_credits_wallet(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/wallet/top-up", data={"csrf_token": "csrf", "amount": "100", "occurred_at": "2026-08-26T11:59", "idempotency_key": "fund"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Fund','unit','IDR','manual')")
        asset_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        db.close()


def test_automatic_wallet_transaction_cannot_be_deleted_directly(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    client.post("/wallet/top-up", data={"csrf_token": "csrf", "amount": "100", "occurred_at": "2026-08-26T11:59", "idempotency_key": "fund"})
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Fund','unit','IDR','manual')")
        asset_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        db.close()
    client.post("/transactions", data={"csrf_token": "csrf", "asset_id": asset_id, "kind": "buy", "quantity": "1", "price": "40", "fx_rate": "1", "occurred_at": "2026-08-26T12:00", "idempotency_key": "buy"})
    db = connect(app.config["SIPD_DB"])
    try:
        pair_id = db.execute("SELECT id FROM transactions WHERE user_id=1 AND idempotency_key LIKE 'rdn:auto:%'").fetchone()[0]
    finally:
        db.close()
    assert client.post(f"/transactions/{pair_id}/delete", data={"csrf_token": "csrf"}).status_code == 400


def test_deleting_asset_buy_removes_wallet_pair(client, existing_session, app):
    client.set_cookie("sipd_session", existing_session)
    client.post("/wallet/top-up", data={"csrf_token": "csrf", "amount": "100", "occurred_at": "2026-08-26T11:59", "idempotency_key": "fund"})
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Fund','unit','IDR','manual')")
        asset_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        db.close()
    client.post("/transactions", data={"csrf_token": "csrf", "asset_id": asset_id, "kind": "buy", "quantity": "1", "price": "40", "fx_rate": "1", "occurred_at": "2026-08-26T12:00", "idempotency_key": "buy"})
    db = connect(app.config["SIPD_DB"])
    try:
        origin_id = db.execute("SELECT id FROM transactions WHERE user_id=1 AND idempotency_key='buy'").fetchone()[0]
    finally:
        db.close()
    assert client.post(f"/transactions/{origin_id}/delete", data={"csrf_token": "csrf"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT count(*) FROM transactions WHERE user_id=1 AND idempotency_key IN ('buy', ?)", (f"rdn:auto:{origin_id}",)).fetchone()[0] == 0
        rows = db.execute("SELECT kind,quantity FROM transactions WHERE user_id=1 AND asset_id=(SELECT id FROM assets WHERE user_id=1 AND name='RDN')").fetchall()
        assert [tuple(row) for row in rows] == [("deposit", "100")]
    finally:
        db.close()
    for kind, price, at, key in (("buy", "40", "2026-08-26T12:00", "buy"), ("sell", "50", "2026-08-26T12:01", "sell")):
        assert client.post("/transactions", data={"csrf_token": "csrf", "asset_id": asset_id, "kind": kind, "quantity": "1", "price": price, "fx_rate": "1", "occurred_at": at, "idempotency_key": key}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        rows = db.execute("SELECT kind,quantity FROM transactions WHERE user_id=1 AND asset_id=(SELECT id FROM assets WHERE user_id=1 AND name='RDN') ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [("deposit", "100"), ("withdrawal", "40"), ("deposit", "50")]
    finally:
        db.close()


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
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".sidebar{display:flex;flex-direction:column" in css.text
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


def test_refresh_uses_fresh_quote_cache_without_provider_call(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BBRI','share','IDR','automatic','yahoo','BBRI.JK')")
        db.execute("""INSERT INTO quote_cache(provider,symbol,price,currency,source,fetched_at)
                      VALUES('yahoo','BBRI.JK','4100','IDR','Yahoo Finance (yfinance)','2099-01-01T00:00:00Z')""")
    finally:
        db.close()
    monkeypatch.setattr(routes, "yahoo_quotes", lambda symbols: (_ for _ in ()).throw(AssertionError("provider should not be called")))
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "cache"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT price FROM asset_prices WHERE asset_id=1").fetchone()[0] == "4100"
        assert "Menggunakan harga cache" in db.execute("SELECT error_summary FROM price_refreshes WHERE refresh_key='cache'").fetchone()[0]
    finally:
        db.close()


def test_refresh_falls_back_to_direct_yahoo_chart(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BBRI','share','IDR','automatic','yahoo','BBRI.JK')")
    finally:
        db.close()
    monkeypatch.setattr(routes, "yahoo_quotes", lambda symbols: ({}, {"BBRI.JK": "Yahoo Finance returned no quote data"}))
    monkeypatch.setattr(routes, "yahoo_chart_quote", lambda symbol, timeout: Quote(Decimal("4300"), "IDR", "Yahoo Finance (chart)", datetime.now(timezone.utc)))
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "chart"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        row = db.execute("SELECT price,source FROM asset_prices WHERE asset_id=1").fetchone()
        assert tuple(row) == ("4300", "Yahoo Finance (chart)")
        assert db.execute("SELECT failure_count FROM quote_cache WHERE symbol='BBRI.JK'").fetchone()[0] == 0
    finally:
        db.close()


def test_refresh_skips_yahoo_symbol_during_backoff(client, existing_session, app, monkeypatch):
    from sipd import routes
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BBRI','share','IDR','automatic','yahoo','BBRI.JK')")
        db.execute("INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(1,1,'4100','IDR','Yahoo Finance (yfinance)','2026-09-01T06:00:00Z')")
        db.execute("""INSERT INTO quote_cache(provider,symbol,error,error_at,failure_count,backoff_until)
                      VALUES('yahoo','BBRI.JK','Yahoo Finance returned no quote data','2026-09-01T06:01:00Z',2,'2099-01-01T00:00:00Z')""")
    finally:
        db.close()
    monkeypatch.setattr(routes, "yahoo_quotes", lambda symbols: (_ for _ in ()).throw(AssertionError("provider should not be called")))
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "backoff"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        row = db.execute("SELECT status,error_summary FROM price_refreshes WHERE refresh_key='backoff'").fetchone()
        assert row["status"] == "partial"
        assert "Penyedia sementara tidak tersedia; Menggunakan harga terakhir" in row["error_summary"]
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
