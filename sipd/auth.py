import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps

from flask import current_app, g, redirect, request

from sipd.db import connect


@dataclass(frozen=True)
class User:
    id: int
    username: str
    csrf: str
    currency: str


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user() -> User | None:
    if "user" in g:
        return g.user
    token = request.cookies.get("sipd_session")
    if not token:
        g.user = None
        return None
    db = connect(current_app.config["SIPD_DB"])
    try:
        row = db.execute(
            """SELECT u.id,u.username,s.csrf_token,us.display_currency,s.expires_at
               FROM sessions s JOIN users u ON u.id=s.user_id
               JOIN user_settings us ON us.user_id=u.id WHERE s.id_hash=?""",
            (session_hash(token),),
        ).fetchone()
    finally:
        db.close()
    if not row or row["expires_at"] <= datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"):
        g.user = None
    else:
        g.user = User(row["id"], row["username"], row["csrf_token"], row["display_currency"])
    return g.user


def require_user(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect("/login")
        return view(*args, **kwargs)
    return wrapped


def anon_token(response):
    token = request.cookies.get("sipd_csrf") or secrets.token_urlsafe(24)
    if not request.cookies.get("sipd_csrf"):
        response.set_cookie("sipd_csrf", token, httponly=True, samesite="Lax", secure=current_app.config["SIPD_BASE_URL"].startswith("https://"))
    return token


def valid_anon_csrf() -> bool:
    return hmac.compare_digest(request.form.get("csrf_token", ""), request.cookies.get("sipd_csrf", ""))


def create_session(user_id: int):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    db = connect(current_app.config["SIPD_DB"])
    try:
        db.execute("INSERT INTO sessions(id_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)", (session_hash(token), user_id, csrf, "2099-01-01T00:00:00Z"))
    finally:
        db.close()
    return token


def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
    return response
