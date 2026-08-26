import hashlib

import bcrypt
import pytest

from sipd import create_app
from sipd.db import connect


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "SIPD_DB": str(tmp_path / "sip-d.db")})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def existing_session(app):
    token = "existing-go-compatible-token"
    db = connect(app.config["SIPD_DB"])
    try:
        db.execute(
            "INSERT INTO users(username,password_hash) VALUES(?,?)",
            ("owner", bcrypt.hashpw(b"correct horse battery staple", bcrypt.gensalt()).decode()),
        )
        user_id = db.execute("SELECT id FROM users WHERE username='owner'").fetchone()[0]
        db.execute("INSERT INTO user_settings(user_id) VALUES(?)", (user_id,))
        db.execute(
            "INSERT INTO sessions(id_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id, "csrf", "2099-01-01T00:00:00Z"),
        )
    finally:
        db.close()
    return token
