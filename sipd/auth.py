import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, g, redirect, request

from sipd.db import connect


@dataclass(frozen=True)
class User:
    id: int
    username: str
    csrf: str
    currency: str
    language: str


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
            """SELECT u.id,u.username,s.csrf_token,us.display_currency,us.language,s.expires_at
               FROM sessions s JOIN users u ON u.id=s.user_id
               JOIN user_settings us ON us.user_id=u.id WHERE s.id_hash=?""",
            (session_hash(token),),
        ).fetchone()
    finally:
        db.close()
    if not row or row["expires_at"] <= datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"):
        g.user = None
    else:
        g.user = User(row["id"], row["username"], row["csrf_token"], row["display_currency"], row["language"])
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


def valid_user_csrf() -> bool:
    user = current_user()
    return bool(user and hmac.compare_digest(request.form.get("csrf_token", ""), user.csrf))


def delete_session():
    token = request.cookies.get("sipd_session")
    if token:
        db = connect(current_app.config["SIPD_DB"])
        try:
            db.execute("DELETE FROM sessions WHERE id_hash=?", (session_hash(token),))
        finally:
            db.close()


def create_session(user_id: int):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    db = connect(current_app.config["SIPD_DB"])
    try:
        db.execute("DELETE FROM sessions WHERE expires_at<?", (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),))
        db.execute("INSERT INTO sessions(id_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)", (session_hash(token), user_id, csrf, expires_at))
    finally:
        db.close()
    return token


def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
    return response
