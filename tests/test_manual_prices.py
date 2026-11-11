from datetime import datetime, timezone

import pytest

from sipd.db import connect


@pytest.fixture
def manual_asset(app, existing_session):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Fund','unit','IDR','manual')")
        db.execute("INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,idempotency_key) VALUES(1,1,'buy','2','100','IDR','1','2020-01-01T00:00:00Z','buy')")
        db.execute("INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(1,1,'100','IDR','Transaction','2020-01-01T00:00:00Z')")
    finally:
        db.close()
    return 1


def test_manual_price_updates_valuation_without_changing_ledger(client, app, existing_session, manual_asset):
    client.set_cookie("sipd_session", existing_session)
    assert 'action="/assets/1/price"' in client.get("/assets/1").text
    before = datetime.now(timezone.utc)
    response = client.post("/assets/1/price", data={"csrf_token": "csrf", "price": "130.25"})
    assert response.status_code == 303
    assert response.headers["Location"] == "/assets/1"
    db = connect(app.config["SIPD_DB"])
    try:
        prices = db.execute("SELECT * FROM asset_prices ORDER BY id").fetchall()
        assert len(prices) == 2
        assert (prices[-1]["price"], prices[-1]["currency"], prices[-1]["source"]) == ("130.25", "IDR", "Manual")
        assert before <= datetime.fromisoformat(prices[-1]["priced_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        assert [tuple(row) for row in db.execute("SELECT quantity,unit_price FROM transactions")] == [("2", "100")]
        assert db.execute("SELECT count(*) FROM portfolio_snapshots").fetchone()[0] == 0
    finally:
        db.close()
    page = client.get("/assets/1").text
    assert "130.25 IDR" in page
    assert "Manual" in page
    dashboard = client.get("/").text
    assert "260.50" in dashboard
    assert "60.50" in dashboard
    assert client.get("/api/assets/1/price").json["price"] == "130.25"


@pytest.mark.parametrize("price", ["", "abc", "0", "-1", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_manual_price_rejects_invalid_values(client, app, existing_session, manual_asset, price):
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/assets/1/price", data={"csrf_token": "csrf", "price": price}).status_code == 400
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT count(*) FROM asset_prices").fetchone()[0] == 1
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["automatic", "fixed"])
def test_manual_price_rejects_other_pricing_modes(client, app, existing_session, manual_asset, mode):
    db = connect(app.config["SIPD_DB"])
    db.execute("UPDATE assets SET pricing_mode=? WHERE id=1", (mode,))
    db.close()
    client.set_cookie("sipd_session", existing_session)
    assert 'action="/assets/1/price"' not in client.get("/assets/1").text
    assert client.post("/assets/1/price", data={"csrf_token": "csrf", "price": "130"}).status_code == 400


def test_manual_price_requires_session_csrf_and_ownership(client, app, existing_session, manual_asset):
    assert client.post("/assets/1/price", data={"price": "130"}).status_code == 302
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/assets/1/price", data={"price": "130"}).status_code == 403
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO users(username,password_hash) VALUES('other','unused')")
        db.execute("UPDATE assets SET user_id=2 WHERE id=1")
    finally:
        db.close()
    assert client.post("/assets/1/price", data={"csrf_token": "csrf", "price": "130"}).status_code == 404
    assert client.post("/assets/999/price", data={"csrf_token": "csrf", "price": "130"}).status_code == 404
    db = connect(app.config["SIPD_DB"])
    try:
        assert db.execute("SELECT count(*) FROM asset_prices").fetchone()[0] == 1
    finally:
        db.close()


def test_manual_price_uses_asset_currency(client, app, existing_session, manual_asset):
    db = connect(app.config["SIPD_DB"])
    db.execute("UPDATE assets SET quote_currency='USD' WHERE id=1")
    db.close()
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/assets/1/price", data={"csrf_token": "csrf", "price": "1.23456789", "currency": "IDR"}).status_code == 303
    db = connect(app.config["SIPD_DB"])
    try:
        latest = db.execute("SELECT price,currency FROM asset_prices ORDER BY priced_at DESC LIMIT 1").fetchone()
        assert tuple(latest) == ("1.23456789", "USD")
    finally:
        db.close()


def test_manual_price_wins_over_older_price_in_same_second(client, app, existing_session, manual_asset, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 11, 12, 0, 0, 100000, tzinfo=timezone.utc)

    monkeypatch.setattr("sipd.routes.datetime", FrozenDatetime)
    db = connect(app.config["SIPD_DB"])
    db.execute("UPDATE asset_prices SET priced_at='2026-11-11T12:00:00Z'")
    db.close()
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/assets/1/price", data={"csrf_token": "csrf", "price": "130.25"}).status_code == 303
    assert "130.25 IDR" in client.get("/assets/1").text
    assert "130.25 IDR" in client.get("/assets").text
    assert "260.50" in client.get("/").text
    assert client.get("/api/assets/1/price").json["price"] == "130.25"
