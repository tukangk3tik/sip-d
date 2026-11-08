import sqlite3
from contextlib import contextmanager
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def init_db(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    try:
        db.executescript((Path(__file__).parents[1] / "migrations.sql").read_text())
        columns = {row["name"] for row in db.execute("PRAGMA table_info(user_settings)")}
        if "language" not in columns:
            db.execute("ALTER TABLE user_settings ADD COLUMN language TEXT NOT NULL DEFAULT 'ID' CHECK(language IN ('ID','EN'))")
        with transaction(db):
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
                additions = {
                    "users": {"is_admin": "INTEGER NOT NULL DEFAULT 0 CHECK(is_admin IN (0,1))"},
                    "asset_prices": {"source_url": "TEXT NOT NULL DEFAULT ''", "price_kind": "TEXT NOT NULL DEFAULT ''"},
                    "quote_cache": {"source_url": "TEXT NOT NULL DEFAULT ''", "price_kind": "TEXT NOT NULL DEFAULT ''", "quoted_at": "TEXT"},
                    "price_refreshes": {"claim_token": "TEXT", "claim_expires_at": "TEXT"},
                }
                for table, fields in additions.items():
                    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                    for name, declaration in fields.items():
                        if name not in existing:
                            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                db.execute("UPDATE users SET is_admin=1 WHERE id=(SELECT id FROM users ORDER BY created_at,id LIMIT 1)")
                db.execute("INSERT INTO schema_migrations VALUES(2,CURRENT_TIMESTAMP)")
    finally:
        db.close()


@contextmanager
def transaction(db: sqlite3.Connection):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()
