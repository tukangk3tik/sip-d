import bcrypt
from decimal import Decimal, InvalidOperation

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

    @app.get("/assets")
    @require_user
    def assets():
        db = connect(app.config["SIPD_DB"])
        try:
            rows = db.execute("SELECT id,name FROM assets WHERE user_id=? ORDER BY name", (current_user().id,)).fetchall()
        finally:
            db.close()
        return render_template_string("<h1>Assets</h1>{% for asset in assets %}<a href='/assets/{{ asset.id }}'>{{ asset.name }}</a>{% endfor %}", assets=rows)

    @app.route("/assets", methods=["POST"])
    @require_user
    def asset_save():
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        user = current_user()
        name, unit = request.form.get("name", "").strip(), request.form.get("unit", "").strip()
        try:
            type_id, scale = int(request.form.get("type_id", "0")), int(request.form.get("scale", "8"))
        except ValueError:
            return "Invalid asset", 400
        if not name or not unit or not 0 <= scale <= 12 or request.form.get("quote_currency") not in {"IDR", "USD"} or request.form.get("pricing_mode") not in {"manual", "automatic", "fixed"}:
            return "Invalid asset", 400
        db = connect(app.config["SIPD_DB"])
        try:
            if not db.execute("SELECT 1 FROM investment_types WHERE id=? AND user_id=?", (type_id, user.id)).fetchone():
                return "Invalid investment type", 400
            db.execute("""INSERT INTO assets(user_id,investment_type_id,name,symbol,unit,quantity_scale,quote_currency,pricing_mode,provider,provider_symbol)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""", (user.id, type_id, name, request.form.get("symbol", "").strip(), unit, scale, request.form["quote_currency"], request.form["pricing_mode"], request.form.get("provider", ""), request.form.get("provider_symbol", "").strip()))
            asset_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            db.close()
        return redirect(f"/assets/{asset_id}", 303)

    @app.get("/assets/<int:asset_id>")
    @require_user
    def asset_detail(asset_id):
        db = connect(app.config["SIPD_DB"])
        try:
            asset = db.execute("SELECT id,name FROM assets WHERE id=? AND user_id=?", (asset_id, current_user().id)).fetchone()
        finally:
            db.close()
        return (render_template_string("<h1>{{ asset.name }}</h1>", asset=asset), 200) if asset else ("Not found", 404)

    @app.post("/transactions")
    @require_user
    def transaction_save():
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        user = current_user()
        try:
            asset_id = int(request.form.get("asset_id", "0"))
            quantity, price, fx = (Decimal(request.form[key]) for key in ("quantity", "price", "fx_rate"))
        except (ValueError, KeyError, InvalidOperation):
            return "Invalid transaction", 400
        if min(quantity, price, fx) <= 0 or request.form.get("kind") not in {"buy", "sell", "deposit", "withdrawal"} or not request.form.get("idempotency_key"):
            return "Invalid transaction", 400
        db = connect(app.config["SIPD_DB"])
        try:
            asset = db.execute("SELECT quote_currency FROM assets WHERE id=? AND user_id=?", (asset_id, user.id)).fetchone()
            if not asset:
                return "Not found", 404
            db.execute("""INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,notes,idempotency_key)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""", (user.id, asset_id, request.form["kind"], str(quantity), str(price), asset["quote_currency"], str(fx), request.form["occurred_at"].replace("T", " ") + ":00", request.form.get("notes", ""), request.form["idempotency_key"]))
            transaction_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                return redirect("/transactions", 303)
            raise
        finally:
            db.close()
        return redirect(f"/transactions/{transaction_id}", 303)

    @app.get("/transactions/<int:transaction_id>")
    @require_user
    def transaction_detail(transaction_id):
        db = connect(app.config["SIPD_DB"])
        try:
            row = db.execute("SELECT id,kind FROM transactions WHERE id=? AND user_id=?", (transaction_id, current_user().id)).fetchone()
        finally:
            db.close()
        return (render_template_string("<h1>{{ row.kind }}</h1>", row=row), 200) if row else ("Not found", 404)

    @app.get("/settings")
    @require_user
    def settings():
        return render_template_string("<h1>Settings</h1>")

    @app.post("/settings/currency")
    @require_user
    def currency_save():
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        currency = request.form.get("currency")
        if currency not in {"IDR", "USD"}:
            return "Invalid currency", 400
        db = connect(app.config["SIPD_DB"])
        try:
            db.execute("UPDATE user_settings SET display_currency=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (currency, current_user().id))
        finally:
            db.close()
        return redirect("/settings", 303)
