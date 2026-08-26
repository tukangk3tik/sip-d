import bcrypt

from flask import make_response, redirect, render_template, render_template_string, request

from sipd.auth import anon_token, create_session, current_user, delete_session, require_user, valid_anon_csrf, valid_user_csrf
from sipd.db import connect


def register_routes(app):
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        db = connect(app.config["SIPD_DB"])
        try:
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return redirect("/login")
            if request.method == "POST":
                if not valid_anon_csrf():
                    return "Invalid CSRF token", 403
                username, password = request.form.get("username", "").strip(), request.form.get("password", "")
                if not 3 <= len(username) <= 64 or len(password) < 12:
                    return "Username must be 3–64 characters and password at least 12 characters.", 400
                db.execute("INSERT INTO users(username,password_hash) VALUES(?,?)", (username, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()))
                user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()[0]
                db.execute("INSERT INTO user_settings(user_id) VALUES(?)", (user_id,))
                for name in ("Cash", "Gold", "Money Market", "Stocks", "BTC"):
                    db.execute("INSERT INTO investment_types(user_id,name) VALUES(?,?)", (user_id, name))
                token = create_session(user_id)
                response = redirect("/", 303)
                response.set_cookie("sipd_session", token, httponly=True, samesite="Lax", secure=app.config["SIPD_BASE_URL"].startswith("https://"))
                return response
        finally:
            db.close()
        response = make_response(render_template("page.html", view="setup", csrf=request.cookies.get("sipd_csrf") or ""))
        token = anon_token(response)
        if not request.cookies.get("sipd_csrf"):
            response.set_data(render_template("page.html", view="setup", csrf=token))
        return response

    @app.get("/")
    @require_user
    def dashboard():
        return render_template_string('<h1>SIP-D</h1><p data-csrf="{{ user.csrf }}">{{ user.username }}</p>', user=current_user())

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            if not valid_anon_csrf():
                return "Invalid CSRF token", 403
            db = connect(app.config["SIPD_DB"])
            try:
                row = db.execute("SELECT id,password_hash FROM users WHERE username=? COLLATE NOCASE", (request.form.get("username", "").strip(),)).fetchone()
            finally:
                db.close()
            if not row or not bcrypt.checkpw(request.form.get("password", "").encode(), row["password_hash"].encode()):
                return "Invalid username or password.", 401
            response = redirect("/", 303)
            response.set_cookie("sipd_session", create_session(row["id"]), httponly=True, samesite="Lax", secure=app.config["SIPD_BASE_URL"].startswith("https://"))
            return response
        response = make_response(render_template("page.html", view="login", csrf=request.cookies.get("sipd_csrf") or ""))
        token = anon_token(response)
        if not request.cookies.get("sipd_csrf"):
            response.set_data(render_template("page.html", view="login", csrf=token))
        return response

    @app.post("/logout")
    @require_user
    def logout():
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        delete_session()
        response = redirect("/login", 303)
        response.delete_cookie("sipd_session")
        return response
