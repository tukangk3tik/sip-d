import bcrypt
import secrets
import time
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from flask import jsonify, make_response, redirect, render_template, render_template_string, request

from sipd.auth import anon_token, create_session, current_user, delete_session, require_user, valid_anon_csrf, valid_user_csrf
from sipd.db import connect
from sipd.domain import LedgerEntry, calculate_position
from sipd.providers import quote_for_asset, usd_idr_quote, yahoo_quotes


def user_page(view, title, **context):
    user = current_user()
    return render_template("page.html", view=view, title=title, user=user, csrf=user.csrf, currency=user.currency, **context)


def register_routes(app):
    def allow_login():
        attempts = app.extensions.setdefault("sipd_login_attempts", {})
        ip, cutoff = request.remote_addr or "", time.monotonic() - 900
        recent = [at for at in attempts.get(ip, []) if at > cutoff]
        if len(recent) >= 10:
            attempts[ip] = recent
            return False
        attempts[ip] = recent + [time.monotonic()]
        return True

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
        user = current_user()
        db = connect(app.config["SIPD_DB"])
        try:
            rate_row = db.execute("SELECT rate FROM exchange_rates WHERE user_id=? AND base_currency='USD' AND quote_currency='IDR' ORDER BY priced_at DESC LIMIT 1", (user.id,)).fetchone()
            rate = Decimal(rate_row["rate"]) if rate_row else Decimal()
            assets = db.execute("SELECT a.id,a.name,a.unit,a.quote_currency,a.pricing_mode,t.name type_name,COALESCE((SELECT price FROM asset_prices p WHERE p.user_id=a.user_id AND p.asset_id=a.id ORDER BY priced_at DESC LIMIT 1),'') price FROM assets a JOIN investment_types t ON t.id=a.investment_type_id WHERE a.user_id=? AND a.active=1 ORDER BY a.name", (user.id,)).fetchall()
            holdings, total, net_invested, realized, unrealized, type_totals = [], Decimal(), Decimal(), Decimal(), Decimal(), {}
            for asset in assets:
                rows = db.execute("SELECT id,kind,quantity,unit_price,fx_rate_to_idr,occurred_at FROM transactions WHERE user_id=? AND asset_id=? ORDER BY occurred_at,id", (user.id, asset["id"])).fetchall()
                position = calculate_position([LedgerEntry(row["id"], row["kind"], Decimal(row["quantity"]), Decimal(row["unit_price"]), Decimal(row["fx_rate_to_idr"]), datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))) for row in rows])
                price = Decimal("1") if asset["pricing_mode"] == "fixed" else Decimal(asset["price"] or "0")
                value = position.quantity * price * (rate if asset["quote_currency"] == "USD" else Decimal("1"))
                holding = dict(asset, quantity=position.quantity, market_value=value, unrealized=value - position.cost_basis, net_invested=position.net_invested, realized=position.realized, has_price=bool(asset["price"]) or asset["pricing_mode"] == "fixed")
                holdings.append(holding)
                total += value
                net_invested += position.net_invested
                realized += position.realized
                unrealized += holding["unrealized"]
                type_totals[asset["type_name"]] = type_totals.get(asset["type_name"], Decimal()) + value
            snapshots = db.execute("SELECT created_at,total_value_idr FROM portfolio_snapshots WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (user.id,)).fetchall()
            refresh = db.execute("SELECT status,error_summary,created_at FROM price_refreshes WHERE user_id=? ORDER BY id DESC LIMIT 1", (user.id,)).fetchone()
        finally:
            db.close()
        display_currency = "USD" if user.currency == "USD" and rate > 0 else "IDR"

        def money(value):
            if display_currency == "USD":
                value /= rate
            return f"{value:,.2f} {display_currency}"

        def allocation(name, value):
            return {"name": name, "value": money(value), "percent": f"{(value / total * 100) if total else Decimal():.1f}"}

        top = [dict(holding, value=money(holding["market_value"]), percent=f"{(holding['market_value'] / total * 100) if total else Decimal():.1f}") for holding in sorted(holdings, key=lambda holding: holding["market_value"], reverse=True)[:3]]
        for holding in holdings:
            holding.update(value=money(holding["market_value"]), unrealized_display=money(holding["unrealized"]), performance="gain" if holding["unrealized"] > 0 else "loss" if holding["unrealized"] < 0 else "")
        return user_page("dashboard", "Dashboard", holdings=holdings, top=top, total=money(total), net_invested=money(net_invested), realized=money(realized), unrealized=money(unrealized), total_return=f"{((realized + unrealized) / net_invested * 100) if net_invested else Decimal():.1f}", type_alloc=[allocation(name, value) for name, value in sorted(type_totals.items(), key=lambda item: item[1], reverse=True)], asset_alloc=[allocation(holding["name"], holding["market_value"]) for holding in sorted(holdings, key=lambda holding: holding["market_value"], reverse=True)], snapshots=[{"created_at": row["created_at"], "value": money(Decimal(row["total_value_idr"]))} for row in snapshots], refresh=refresh, refresh_key=secrets.token_urlsafe(18))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            if not valid_anon_csrf():
                return "Invalid CSRF token", 403
            if not allow_login():
                return "Too many login attempts. Try again later.", 429
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
            rows = db.execute("SELECT a.*,t.name type_name FROM assets a JOIN investment_types t ON t.id=a.investment_type_id WHERE a.user_id=? ORDER BY a.active DESC,t.name,a.name", (current_user().id,)).fetchall()
        finally:
            db.close()
        return user_page("assets", "Assets", assets=rows)

    @app.get("/assets/new")
    @app.get("/assets/<int:asset_id>/edit")
    @require_user
    def asset_form(asset_id=None):
        db = connect(app.config["SIPD_DB"])
        try:
            types = db.execute("SELECT id,name FROM investment_types WHERE user_id=? AND active=1 ORDER BY name", (current_user().id,)).fetchall()
            asset = db.execute("SELECT * FROM assets WHERE id=? AND user_id=?", (asset_id, current_user().id)).fetchone() if asset_id else None
        finally:
            db.close()
        if asset_id and not asset:
            return "Not found", 404
        return user_page("asset_form", "Edit asset" if asset else "New asset", asset=asset, types=types)

    @app.post("/assets")
    @app.post("/assets/<int:asset_id>")
    @require_user
    def asset_save(asset_id=None):
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        user = current_user()
        name, unit = request.form.get("name", "").strip(), request.form.get("unit", "").strip()
        try:
            type_id, scale = int(request.form.get("type_id", "0")), int(request.form.get("scale", "8"))
        except ValueError:
            return "Invalid asset", 400
        mode, provider, provider_symbol = request.form.get("pricing_mode"), request.form.get("provider", "").strip(), request.form.get("provider_symbol", "").strip()
        if not name or not unit or not 0 <= scale <= 12 or request.form.get("quote_currency") not in {"IDR", "USD"} or mode not in {"manual", "automatic", "fixed"} or (mode == "automatic" and (provider not in {"yahoo", "finnhub", "kraken", "metalsdev"} or not provider_symbol)):
            return "Invalid asset", 400
        db = connect(app.config["SIPD_DB"])
        try:
            if not db.execute("SELECT 1 FROM investment_types WHERE id=? AND user_id=?", (type_id, user.id)).fetchone():
                return "Invalid investment type", 400
            if asset_id:
                if not db.execute("SELECT 1 FROM assets WHERE id=? AND user_id=?", (asset_id, user.id)).fetchone():
                    return "Not found", 404
                db.execute("UPDATE assets SET investment_type_id=?,name=?,symbol=?,unit=?,quantity_scale=?,quote_currency=?,pricing_mode=?,provider=?,provider_symbol=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (type_id, name, request.form.get("symbol", "").strip(), unit, scale, request.form["quote_currency"], mode, provider, provider_symbol, asset_id, user.id))
            else:
                db.execute("INSERT INTO assets(user_id,investment_type_id,name,symbol,unit,quantity_scale,quote_currency,pricing_mode,provider,provider_symbol) VALUES(?,?,?,?,?,?,?,?,?,?)", (user.id, type_id, name, request.form.get("symbol", "").strip(), unit, scale, request.form["quote_currency"], mode, provider, provider_symbol))
                asset_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            db.close()
        return redirect("/assets", 303)

    @app.post("/assets/<int:asset_id>/archive")
    @require_user
    def asset_archive(asset_id):
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        db = connect(app.config["SIPD_DB"])
        try:
            result = db.execute("UPDATE assets SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (asset_id, current_user().id))
        finally:
            db.close()
        return redirect("/assets", 303) if result.rowcount else ("Not found", 404)

    @app.get("/assets/<int:asset_id>")
    @require_user
    def asset_detail(asset_id):
        db = connect(app.config["SIPD_DB"])
        try:
            asset = db.execute("SELECT a.*,t.name type_name FROM assets a JOIN investment_types t ON t.id=a.investment_type_id WHERE a.id=? AND a.user_id=?", (asset_id, current_user().id)).fetchone()
            transactions = db.execute("SELECT * FROM transactions WHERE user_id=? AND asset_id=? ORDER BY occurred_at DESC,id DESC", (current_user().id, asset_id)).fetchall() if asset else []
        finally:
            db.close()
        return user_page("asset_detail", asset["name"], asset=asset, transactions=transactions) if asset else ("Not found", 404)

    @app.post("/transactions")
    @require_user
    def transaction_save():
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        user = current_user()
        try:
            asset_id = int(request.form.get("asset_id", "0"))
            quantity, price, fx = (Decimal(request.form[key]) for key in ("quantity", "price", "fx_rate"))
            occurred_at = datetime.fromisoformat(request.form["occurred_at"]).replace(tzinfo=timezone.utc)
        except (ValueError, KeyError, InvalidOperation):
            return "Invalid transaction", 400
        if min(quantity, price, fx) <= 0 or request.form.get("kind") not in {"buy", "sell", "deposit", "withdrawal"} or not request.form.get("idempotency_key"):
            return "Invalid transaction", 400
        db = connect(app.config["SIPD_DB"])
        try:
            asset = db.execute("SELECT quote_currency,pricing_mode FROM assets WHERE id=? AND user_id=?", (asset_id, user.id)).fetchone()
            if not asset:
                return "Not found", 404
            if asset["pricing_mode"] == "fixed" and price != 1:
                return "Cash fixed price must be 1", 400
            existing = db.execute("SELECT id,kind,quantity,unit_price,fx_rate_to_idr,occurred_at FROM transactions WHERE user_id=? AND asset_id=? ORDER BY occurred_at,id", (user.id, asset_id)).fetchall()
            entries = [LedgerEntry(row["id"], row["kind"], Decimal(row["quantity"]), Decimal(row["unit_price"]), Decimal(row["fx_rate_to_idr"]), datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))) for row in existing]
            try:
                calculate_position(entries + [LedgerEntry(0, request.form["kind"], quantity, price, fx, occurred_at)])
            except ValueError as error:
                return str(error), 400
            db.execute("""INSERT INTO transactions(user_id,asset_id,kind,quantity,unit_price,quote_currency,fx_rate_to_idr,occurred_at,notes,idempotency_key)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""", (user.id, asset_id, request.form["kind"], str(quantity), str(price), asset["quote_currency"], str(fx), occurred_at.isoformat().replace("+00:00", "Z"), request.form.get("notes", "").strip(), request.form["idempotency_key"]))
            transaction_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(?,?,?,?,?,?)", (user.id, asset_id, str(price), asset["quote_currency"], "Transaction", occurred_at.isoformat().replace("+00:00", "Z")))
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                return redirect("/transactions", 303)
            raise
        finally:
            db.close()
        return redirect(f"/transactions/{transaction_id}", 303)

    @app.get("/transactions")
    @require_user
    def transactions():
        db = connect(app.config["SIPD_DB"])
        try:
            transactions = db.execute("SELECT t.*,a.name asset_name FROM transactions t JOIN assets a ON a.id=t.asset_id WHERE t.user_id=? ORDER BY t.occurred_at DESC,t.id DESC", (current_user().id,)).fetchall()
            assets = db.execute("SELECT id,name FROM assets WHERE user_id=? AND active=1 ORDER BY name", (current_user().id,)).fetchall()
        finally:
            db.close()
        return user_page("transactions", "Transactions", transactions=transactions, assets=assets)

    @app.get("/transactions/new")
    @require_user
    def transaction_form():
        db = connect(app.config["SIPD_DB"])
        try:
            assets = db.execute("SELECT id,name,quote_currency,pricing_mode FROM assets WHERE user_id=? AND active=1 ORDER BY name", (current_user().id,)).fetchall()
        finally:
            db.close()
        return user_page("transaction_form", "New transaction", assets=assets, idempotency_key=f"tx-{datetime.now(timezone.utc).timestamp():.6f}")

    @app.get("/transactions/<int:transaction_id>")
    @require_user
    def transaction_detail(transaction_id):
        db = connect(app.config["SIPD_DB"])
        try:
            row = db.execute("SELECT t.*,a.name asset_name FROM transactions t JOIN assets a ON a.id=t.asset_id WHERE t.id=? AND t.user_id=?", (transaction_id, current_user().id)).fetchone()
        finally:
            db.close()
        return user_page("transaction_detail", "Transaction", transaction=row) if row else ("Not found", 404)

    @app.post("/transactions/<int:transaction_id>/delete")
    @require_user
    def transaction_delete(transaction_id):
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        db = connect(app.config["SIPD_DB"])
        try:
            row = db.execute("SELECT asset_id FROM transactions WHERE id=? AND user_id=?", (transaction_id, current_user().id)).fetchone()
            if not row:
                return "Not found", 404
            db.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (transaction_id, current_user().id))
            db.execute("DELETE FROM asset_prices WHERE user_id=? AND asset_id=? AND source='Transaction'", (current_user().id, row["asset_id"]))
            db.execute("INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) SELECT user_id,asset_id,unit_price,quote_currency,'Transaction',occurred_at FROM transactions WHERE user_id=? AND asset_id=?", (current_user().id, row["asset_id"]))
        finally:
            db.close()
        return redirect("/transactions", 303)

    @app.get("/settings")
    @require_user
    def settings():
        db = connect(app.config["SIPD_DB"])
        try:
            types = db.execute("SELECT id,name,active FROM investment_types WHERE user_id=? ORDER BY active DESC,name", (current_user().id,)).fetchall()
        finally:
            db.close()
        return user_page("settings", "Settings", types=types)

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

    @app.post("/settings/types")
    @app.post("/settings/types/<int:type_id>")
    @require_user
    def type_save(type_id=None):
        if not valid_user_csrf() or not request.form.get("name", "").strip():
            return "Name required", 400
        db = connect(app.config["SIPD_DB"])
        try:
            if type_id:
                db.execute("UPDATE investment_types SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (request.form["name"].strip(), type_id, current_user().id))
            else:
                db.execute("INSERT INTO investment_types(user_id,name) VALUES(?,?)", (current_user().id, request.form["name"].strip()))
        finally:
            db.close()
        return redirect("/settings", 303)

    @app.post("/settings/types/<int:type_id>/archive")
    @require_user
    def type_archive(type_id):
        if not valid_user_csrf():
            return "Invalid CSRF token", 403
        db = connect(app.config["SIPD_DB"])
        try:
            in_use = db.execute("SELECT 1 FROM assets WHERE user_id=? AND investment_type_id=? LIMIT 1", (current_user().id, type_id)).fetchone()
            if in_use:
                db.execute("UPDATE investment_types SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (type_id, current_user().id))
            else:
                db.execute("DELETE FROM investment_types WHERE id=? AND user_id=?", (type_id, current_user().id))
        finally:
            db.close()
        return redirect("/settings", 303)

    @app.get("/settings/tickers")
    @require_user
    def ticker_check():
        provider, query = request.args.get("provider", "yahoo"), request.args.get("q", "").strip()
        if provider not in {"yahoo", "finnhub"}:
            return user_page("ticker_check", "Ticker lookup", provider=provider, query=query, error="Unsupported ticker provider")
        if len(query) > 64 or any(ord(char) < 32 for char in query):
            return user_page("ticker_check", "Ticker lookup", provider=provider, query=query, error="Search must be 1–64 printable characters")
        result = None
        if query and provider == "yahoo":
            quotes, errors = yahoo_quotes((query,))
            if query in quotes:
                result = quotes[query]
            else:
                return user_page("ticker_check", "Ticker lookup", provider=provider, query=query, error=errors.get(query, "No matching symbol"))
        return user_page("ticker_check", "Ticker lookup", provider=provider, query=query, result=result)

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
            assets = db.execute("SELECT id,name,unit,pricing_mode,quote_currency,provider,provider_symbol FROM assets WHERE user_id=? AND active=1", (user.id,)).fetchall()
            yahoo_assets = [asset for asset in assets if asset["pricing_mode"] == "automatic" and asset["provider"] == "yahoo"]
            quotes, provider_errors = yahoo_quotes(tuple(asset["provider_symbol"] for asset in yahoo_assets)) if yahoo_assets else ({}, {})
            errors = []
            try:
                rate = usd_idr_quote()
                db.execute("INSERT INTO exchange_rates(user_id,base_currency,quote_currency,rate,source,priced_at) VALUES(?,'USD','IDR',?,?,?)", (user.id, str(rate.price), rate.source, rate.at.isoformat().replace("+00:00", "Z")))
            except ValueError as error:
                errors.append(f"USD/IDR: {error}")
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
                if asset["pricing_mode"] != "automatic" or asset["provider"] == "yahoo":
                    continue
                try:
                    quote = quote_for_asset(asset, metals_key=app.config["SIPD_METALS_API_KEY"], finnhub_key=app.config["SIPD_FINNHUB_API_KEY"])
                    if quote.price <= 0 or quote.currency != asset["quote_currency"]:
                        raise ValueError("provider returned invalid price")
                    db.execute("INSERT INTO asset_prices(user_id,asset_id,price,currency,source,priced_at) VALUES(?,?,?,?,?,?)", (user.id, asset["id"], str(quote.price), quote.currency, quote.source, quote.at.isoformat().replace("+00:00", "Z")))
                except ValueError as error:
                    errors.append(f"{asset['name']}: {error}")

            total = net_invested = realized = unrealized = Decimal()
            snapshot_items = []
            db.execute("BEGIN IMMEDIATE")
            for asset in assets:
                rows = db.execute("SELECT id,kind,quantity,unit_price,fx_rate_to_idr,occurred_at FROM transactions WHERE user_id=? AND asset_id=? ORDER BY occurred_at,id", (user.id, asset["id"])).fetchall()
                entries = [LedgerEntry(row["id"], row["kind"], Decimal(row["quantity"]), Decimal(row["unit_price"]), Decimal(row["fx_rate_to_idr"]), datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))) for row in rows]
                if not entries:
                    continue
                position = calculate_position(entries)
                latest = db.execute("SELECT price FROM asset_prices WHERE user_id=? AND asset_id=? ORDER BY priced_at DESC LIMIT 1", (user.id, asset["id"])).fetchone()
                price = Decimal("1") if asset["pricing_mode"] == "fixed" else Decimal(latest["price"]) if latest else Decimal()
                fx_rate = Decimal("1")
                if asset["quote_currency"] == "USD":
                    latest_rate = db.execute("SELECT rate FROM exchange_rates WHERE user_id=? AND base_currency='USD' AND quote_currency='IDR' ORDER BY priced_at DESC LIMIT 1", (user.id,)).fetchone()
                    fx_rate = Decimal(latest_rate["rate"]) if latest_rate else Decimal()
                value = position.quantity * price * fx_rate
                total += value
                net_invested += position.net_invested
                realized += position.realized
                unrealized += value - position.cost_basis
                snapshot_items.append((asset, position, price, fx_rate, value))
            status = "partial" if errors else "success"
            db.execute("INSERT INTO price_refreshes(user_id,refresh_key,status,error_summary) VALUES(?,?,?,?)", (user.id, key, status, "; ".join(errors)))
            db.execute("INSERT INTO portfolio_snapshots(user_id,refresh_key,total_value_idr,net_invested_idr,realized_pl_idr,unrealized_pl_idr) VALUES(?,?,?,?,?,?)", (user.id, key, str(total), str(net_invested), str(realized), str(unrealized)))
            snapshot_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            for asset, position, price, fx_rate, value in snapshot_items:
                db.execute("INSERT INTO portfolio_snapshot_items(snapshot_id,user_id,asset_id,quantity,average_cost,price,quote_currency,fx_rate_to_idr,market_value_idr,cost_basis_idr,realized_pl_idr) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (snapshot_id, user.id, asset["id"], str(position.quantity), str(position.average_cost), str(price), asset["quote_currency"], str(fx_rate), str(value), str(position.cost_basis), str(position.realized)))
            db.commit()
        finally:
            db.close()
        return redirect("/", 303)

    @app.get("/api/assets/<int:asset_id>/price")
    @require_user
    def price_lookup(asset_id):
        db = connect(app.config["SIPD_DB"])
        try:
            asset = db.execute("SELECT pricing_mode,provider,provider_symbol,quote_currency,unit FROM assets WHERE id=? AND user_id=?", (asset_id, current_user().id)).fetchone()
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
        else:
            try:
                quote = quote_for_asset(asset, metals_key=app.config["SIPD_METALS_API_KEY"], finnhub_key=app.config["SIPD_FINNHUB_API_KEY"])
                if quote.price > 0 and quote.currency == asset["quote_currency"]:
                    return jsonify(price=str(quote.price), currency=quote.currency, source=quote.source, timestamp=quote.at.isoformat().replace("+00:00", "Z"))
            except ValueError:
                pass
        db = connect(app.config["SIPD_DB"])
        try:
            last = db.execute("SELECT price,currency,source,priced_at FROM asset_prices WHERE user_id=? AND asset_id=? ORDER BY priced_at DESC LIMIT 1", (current_user().id, asset_id)).fetchone()
        finally:
            db.close()
        if last:
            return jsonify(price=last["price"], currency=last["currency"], source=last["source"] + " (last known)", timestamp=last["priced_at"])
        return "No automatic or manual price available", 503

    @app.get("/api/exchange-rate")
    @require_user
    def exchange_rate_lookup():
        try:
            quote = usd_idr_quote()
        except ValueError:
            db = connect(app.config["SIPD_DB"])
            try:
                last = db.execute("SELECT rate,source,priced_at FROM exchange_rates WHERE user_id=? AND base_currency='USD' AND quote_currency='IDR' ORDER BY priced_at DESC LIMIT 1", (current_user().id,)).fetchone()
            finally:
                db.close()
            if last:
                return jsonify(rate=last["rate"], source=last["source"] + " (last known)", timestamp=last["priced_at"])
            return "USD/IDR rate unavailable", 503
        return jsonify(rate=str(quote.price), source=quote.source, timestamp=quote.at.isoformat().replace("+00:00", "Z"))
