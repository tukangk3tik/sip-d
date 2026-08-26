from sipd import repositories
from sipd.db import connect


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
