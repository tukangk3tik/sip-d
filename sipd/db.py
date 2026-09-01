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
