from decimal import Decimal
from datetime import datetime, timezone

from sipd.db import connect
from sipd.providers import Quote


def test_refresh_notes_are_scoped_to_asset_and_currency(client, app, existing_session, monkeypatch):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'Gold; retail: one','gram','IDR','automatic','metalsdev','gold')")
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BTC','unit','USD','automatic','kraken','XBTUSD')")
        db.execute("INSERT INTO users(username,password_hash) VALUES('other','unused')")
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(2,1,'Private','unit','IDR','manual')")
    finally:
        db.close()

    def quote_for_asset(asset, **kwargs):
        if asset["id"] == 1:
            raise ValueError("Gold provider unavailable; retry later")
        return Quote(Decimal("90000"), "USD", "Kraken", datetime.now(timezone.utc))

    def unavailable_fx():
        raise ValueError("FX provider unavailable")

    monkeypatch.setattr("sipd.routes.quote_for_asset", quote_for_asset)
    monkeypatch.setattr("sipd.routes.usd_idr_quote", unavailable_fx)
    client.set_cookie("sipd_session", existing_session)
    assert client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "notes"}).status_code == 303
    gold = client.get("/assets/1").text
    assert "Detail penyegaran" in gold
    assert "Gold provider unavailable; retry later" in gold
    assert "error-card" in gold
    assert "FX provider unavailable" not in gold
    btc = client.get("/assets/2").text
    assert "Berhasil diperbarui" in btc
    assert "FX provider unavailable" in btc
    assert "Gold provider unavailable" not in btc
    assert client.get("/assets/3").status_code == 404
    dashboard = client.get("/").text
    assert "Status penyegaran" not in dashboard
    assert "Gold provider unavailable" not in dashboard
    assert "FX provider unavailable" not in dashboard

    monkeypatch.setattr("sipd.routes.quote_for_asset", lambda asset, **kwargs: Quote(Decimal("100"), asset["quote_currency"], "Provider", datetime.now(timezone.utc)))
    monkeypatch.setattr("sipd.routes.usd_idr_quote", lambda: Quote(Decimal("16000"), "IDR", "FX", datetime.now(timezone.utc)))
    assert client.post("/refresh", data={"csrf_token": "csrf", "refresh_key": "recovered"}).status_code == 303
    assert "Gold provider unavailable" not in client.get("/assets/1").text
    assert "FX provider unavailable" not in client.get("/assets/2").text
    assert "Berhasil diperbarui" in client.get("/assets/1").text


def test_asset_without_refresh_notes_still_renders(client, app, existing_session):
    db = connect(app.config["SIPD_DB"])
    db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Fund','unit','IDR','manual')")
    db.close()
    client.set_cookie("sipd_session", existing_session)
    page = client.get("/assets/1")
    assert page.status_code == 200
    assert "Detail penyegaran" not in page.text
    assert 'action="/assets/1/price"' in page.text
