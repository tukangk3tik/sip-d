from pathlib import Path

from sipd.db import connect, init_db


def test_ai_migration_promotes_original_owner_once(tmp_path):
    path = tmp_path / "legacy.db"
    db = connect(path)
    db.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO users VALUES(8,'original','hash','2020-01-01'),(2,'later','hash','2021-01-01');
    """)
    db.close()
    init_db(path)
    db = connect(path)
    assert [(r["id"], r["is_admin"]) for r in db.execute("SELECT * FROM users ORDER BY id")] == [(2, 0), (8, 1)]
    db.execute("UPDATE users SET is_admin=0")
    db.close()
    init_db(path)
    db = connect(path)
    assert db.execute("SELECT SUM(is_admin) FROM users").fetchone()[0] == 0
    assert db.execute("SELECT revision FROM ai_settings WHERE id=1").fetchone()[0] == 0
    db.close()


def test_ai_migration_resumes_partial_columns(tmp_path):
    path = tmp_path / "partial.db"
    init_db(path)
    db = connect(path)
    db.execute("DELETE FROM schema_migrations WHERE version=2")
    db.close()
    init_db(path)
    db = connect(path)
    assert "source_url" in {r["name"] for r in db.execute("PRAGMA table_info(asset_prices)")}
    assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=2").fetchone()[0] == 1
    db.close()


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
