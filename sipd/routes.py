import bcrypt
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from flask import jsonify, make_response, redirect, render_template, render_template_string, request

from sipd.auth import anon_token, create_session, current_user, delete_session, require_user, valid_anon_csrf, valid_user_csrf
from sipd.db import connect
from sipd.domain import LedgerEntry, calculate_position
from sipd.providers import yahoo_quotes


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

    @app.post("/refresh")
    @require_user
    def refresh():
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        user, key = current_user(), request.form.get("refresh_key", "")
        if not key:
            return "Missing refresh key", 400
        db = connect(app.config["SIPD_DB"])
        try:
            if db.execute("SELECT 1 FROM price_refreshes WHERE user_id=? AND refresh_key=?", (user.id, key)).fetchone():
                return redirect("/", 303)
            assets = db.execute("SELECT id,name,pricing_mode,quote_currency,provider,provider_symbol FROM assets WHERE user_id=? AND active=1", (user.id,)).fetchall()
            yahoo_assets = [asset for asset in assets if asset["pricing_mode"] == "automatic" and asset["provider"] == "yahoo"]
            quotes, provider_errors = yahoo_quotes(tuple(asset["provider_symbol"] for asset in yahoo_assets)) if yahoo_assets else ({}, {})
            errors = []
            for asset in yahoo_assets:
                quote = quotes.get(asset["provider_symbol"])
                if not quote:
                    errors.append(f"{asset['name']}: {provider_errors.get(asset['provider_symbol'], 'provider unavailable')}")
                    continue
                if quote.price <= 0 or quote.currency != asset["quote_currency"]:
                    errors.append(f"{asset['name']}: provider returned invalid price")
                    continue
                db.execute("INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(?,?,?,?,?,?)", (user.id, asset["id"], str(quote.price), quote.currency, quote.source, quote.at.isoformat().replace("+00:00", "Z")))
            for asset in assets:
                if asset["pricing_mode"] == "automatic" and asset["provider"] != "yahoo":
                    errors.append(f"{asset['name']}: automatic provider unavailable")

            total = net_invested = realized = unrealized = Decimal()
            db.execute("BEGIN IMMEDIATE")
            for asset in assets:
                rows = db.execute("SELECT id,kind,quantity,unit_price,fx_rate_to_idr,occurred_at FROM transactions WHERE user_id=? AND asset_id=? ORDER BY occurred_at,id", (user.id, asset["id"])).fetchall()
                entries = [LedgerEntry(row["id"], row["kind"], Decimal(row["quantity"]), Decimal(row["unit_price"]), Decimal(row["fx_rate_to_idr"]), datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))) for row in rows]
                if not entries:
                    continue
                position = calculate_position(entries)
                latest = db.execute("SELECT price FROM asset_prices WHERE user_id=? AND asset_id=? ORDER BY priced_at DESC LIMIT 1", (user.id, asset["id"])).fetchone()
                price = Decimal("1") if asset["pricing_mode"] == "fixed" else Decimal(latest["price"]) if latest else Decimal()
                value = position.quantity * price
                total += value
                net_invested += position.net_invested
                realized += position.realized
                unrealized += value - position.cost_basis
            status = "partial" if errors else "success"
            db.execute("INSERT INTO price_refreshes(user_id,refresh_key,status,error_summary) VALUES(?,?,?,?)", (user.id, key, status, "; ".join(errors)))
            db.execute("INSERT INTO portfolio_snapshots(user_id,refresh_key,total_value_idr,net_invested_idr,realized_pl_idr,unrealized_pl_idr) VALUES(?,?,?,?,?,?)", (user.id, key, str(total), str(net_invested), str(realized), str(unrealized)))
            db.commit()
        finally:
            db.close()
        return redirect("/", 303)

    @app.get("/api/assets/<int:asset_id>/price")
    @require_user
    def price_lookup(asset_id):
        db = connect(app.config["SIPD_DB"])
        try:
            asset = db.execute("SELECT pricing_mode,provider,provider_symbol,quote_currency FROM assets WHERE id=? AND user_id=?", (asset_id, current_user().id)).fetchone()
        finally:
            db.close()
        if not asset:
            return "Not found", 404
        if asset["pricing_mode"] == "fixed":
            return jsonify(price="1", currency=asset["quote_currency"], source="Fixed", timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        if asset["provider"] == "yahoo":
            quotes, errors = yahoo_quotes((asset["provider_symbol"],))
            quote = quotes.get(asset["provider_symbol"])
            if quote:
                return jsonify(price=str(quote.price), currency=quote.currency, source=quote.source, timestamp=quote.at.isoformat())
        db = connect(app.config["SIPD_DB"])
        try:
            last = db.execute("SELECT price,currency,source,priced_at FROM asset_prices WHERE user_id=? AND asset_id=? ORDER BY priced_at DESC LIMIT 1", (current_user().id, asset_id)).fetchone()
        finally:
            db.close()
        if last:
            return jsonify(price=last["price"], currency=last["currency"], source=last["source"] + " (last known)", timestamp=last["priced_at"])
        return "No automatic or manual price available", 503
