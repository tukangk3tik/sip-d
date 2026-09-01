from decimal import Decimal

from sipd import repositories
from sipd.db import connect
from sipd.providers import Quote


def test_get_asset_does_not_cross_owner_boundary(app):
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO users(username,password_hash) VALUES('one','hash'),('two','hash')")
        db.execute("INSERT INTO user_settings(user_id) VALUES(1),(2)")
        db.execute("INSERT INTO investment_types(user_id,name) VALUES(1,'Cash')")
        db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode) VALUES(1,1,'Private','IDR','IDR','manual')")
    finally:
        db.close()

    assert repositories.get_asset(app.config["SIPD_DB"], 2, 1) is None


def test_quote_cache_stores_success_and_failure_state(app):
    fetched_at = repositories.parse_db_time("2026-09-01T07:00:00Z")
    failed_at = repositories.parse_db_time("2026-09-01T07:01:00Z")
    db = connect(app.config["SIPD_DB"])
    try:
        repositories.save_cached_quote(db, "yahoo", "BBRI.JK", Quote(Decimal("4200"), "IDR", "Yahoo Finance (chart)", fetched_at))
        row = repositories.get_cached_quote(db, "yahoo", "BBRI.JK")
        assert row["price"] == "4200"
        assert row["failure_count"] == 0

        repositories.record_quote_failure(db, "yahoo", "BBRI.JK", "Yahoo Finance returned no quote data", now=failed_at, initial_seconds=60, max_seconds=1800)
        row = repositories.get_cached_quote(db, "yahoo", "BBRI.JK")
        assert row["price"] == "4200"
        assert row["error"] == "Yahoo Finance returned no quote data"
        assert row["failure_count"] == 1
        assert row["backoff_until"] == "2026-09-01T07:02:00Z"
    finally:
        db.close()
