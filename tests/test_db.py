from pathlib import Path

from sipd.db import connect, init_db


def test_existing_migration_creates_current_schema(tmp_path):
    path = tmp_path / "sip-d.db"
    init_db(path)
    db = connect(path)
    try:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        assert {"id", "username", "password_hash"} <= columns
        settings_columns = {row["name"] for row in db.execute("PRAGMA table_info(user_settings)")}
        assert "language" in settings_columns
    finally:
        db.close()
