# SIP-D Python/Flask Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Go HTTP server with a Flask/Jinja application that runs against the existing SIP-D SQLite database and uses yfinance for Yahoo `.JK` quotes.

**Architecture:** A Flask app factory serves the existing routes, Jinja page, and static files. Small standard-library `sqlite3` repositories retain the current schema and `Decimal` accounting; auth retains the opaque database-backed session cookie. The refresh route batches Yahoo assets through yfinance while preserving the current partial-failure and snapshot semantics.

**Tech Stack:** Python 3.12, Flask, Jinja, Gunicorn, sqlite3, Decimal, bcrypt, requests, yfinance, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-python-flask-rewrite-design.md`

## Global Constraints

- Preserve the existing `migrations.sql` schema and all current HTTP routes, form fields, redirects, cookie name, static URLs, and environment variable names.
- Use `Decimal` for every quantity, price, FX rate, and portfolio value; persist decimal strings exactly as the current app does.
- Use `sqlite3`; do not introduce an ORM, SPA, task queue, cache server, or database migration.
- Preserve explicit `user_id` filters on every user-owned SQL query and the existing security headers, CSRF checks, and opaque `sipd_session` session format.
- `provider='yahoo'` means yfinance in Python; batched refreshes and a 30-second cache must reduce Yahoo requests.
- Keep the Go binary and service configuration available until Flask cutover checks pass. Do not modify the production database schema.

---

## File structure

```text
sipd/
  __init__.py        # Flask app factory and configuration
  auth.py            # DB-backed sessions, CSRF, login limiter
  db.py              # SQLite connection and transactions
  domain.py          # Decimal accounting calculations
  providers.py       # yfinance and HTTP quote providers
  repositories.py    # SQL reads/writes with ownership filters
  routes.py          # Existing browser and JSON route contract
  wsgi.py            # Gunicorn entry point
  templates/page.html
  static/            # Existing static assets copied from Go embeds
tests/
  conftest.py
  test_auth.py
  test_domain.py
  test_providers.py
  test_routes.py
requirements.txt
deploy/sip-d-python.service
```

### Task 1: Establish the Python application and test harness

**Files:**
- Create: `requirements.txt`, `sipd/__init__.py`, `sipd/wsgi.py`, `tests/conftest.py`, `tests/test_app.py`
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Produces `create_app(config: dict | None = None) -> Flask` and `sipd.wsgi:app`.
- All later tests obtain a Flask test client from `tests/conftest.py`.

- [ ] **Step 1: Write the failing app-factory test**

```python
def test_healthz_returns_json(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_app.py::test_healthz_returns_json -v`

Expected: FAIL because `sipd.create_app` does not exist.

- [ ] **Step 3: Add the smallest runnable Flask app**

```python
# sipd/__init__.py
from flask import Flask, jsonify

def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(config or {})
    app.get("/healthz")(lambda: jsonify(status="ok"))
    return app
```

Create `sipd/wsgi.py` with `app = create_app()`. Pin Flask, Gunicorn, bcrypt, requests, yfinance, and pytest in `requirements.txt`. Configure the test fixture with a temporary `SIPD_DB` path and `TESTING=True`.

- [ ] **Step 4: Verify the test passes**

Run: `python -m pytest tests/test_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the bootstrap**

```bash
git add requirements.txt sipd tests .gitignore README.md
git commit -m "feat: bootstrap Flask application"
```

### Task 2: Port exact-decimal accounting

**Files:**
- Create: `sipd/domain.py`, `tests/test_domain.py`

**Interfaces:**
- Produces `LedgerEntry`, `Position`, `calculate_position(entries: list[LedgerEntry]) -> Position`, and `convert(amount: Decimal, usd_to_idr: Decimal, source: str, target: str) -> Decimal`.
- Consumed by holding and refresh calculations.

- [ ] **Step 1: Write ledger parity tests**

```python
def test_weighted_average_sale_matches_current_ledger():
    at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    position = calculate_position([
        LedgerEntry(1, "buy", Decimal("2"), Decimal("100"), Decimal("1"), at),
        LedgerEntry(2, "buy", Decimal("2"), Decimal("200"), Decimal("1"), at + timedelta(seconds=1)),
        LedgerEntry(3, "sell", Decimal("1"), Decimal("180"), Decimal("1"), at + timedelta(seconds=2)),
    ])
    assert position.quantity == Decimal("3")
    assert position.average_cost == Decimal("150")
    assert position.realized == Decimal("30")
```

Also test zero/negative inputs, oversells, unknown transaction kinds, ID tie ordering, USD-to-IDR conversion, IDR-to-USD conversion, and unsupported currencies.

- [ ] **Step 2: Run the domain tests to verify they fail**

Run: `python -m pytest tests/test_domain.py -v`

Expected: FAIL because the domain module is absent.

- [ ] **Step 3: Implement the direct Decimal port**

```python
def calculate_position(entries):
    position = Position()
    for entry in sorted(entries, key=lambda e: (e.at, e.id)):
        if entry.quantity <= 0 or entry.price <= 0 or entry.fx_to_idr <= 0:
            raise ValueError("quantity, price, and exchange rate must be positive")
        value = entry.quantity * entry.price * entry.fx_to_idr
        if entry.kind in {"buy", "deposit"}:
            position.cost_basis += value
            position.quantity += entry.quantity
            position.average_cost = position.cost_basis / position.quantity
            position.net_invested += value
        elif entry.kind in {"sell", "withdrawal"}:
            if entry.quantity > position.quantity:
                raise ValueError("quantity exceeds available holding")
            cost = position.average_cost * entry.quantity
            position.realized += value - cost
            position.cost_basis -= cost
            position.quantity -= entry.quantity
            position.net_invested -= value
            if not position.quantity:
                position.average_cost = position.cost_basis = Decimal("0")
        else:
            raise ValueError("invalid transaction type")
    return position
```

Raise `ValueError` for invalid values, unavailable holdings, and unsupported conversions. Do not round during calculations.

- [ ] **Step 4: Verify the domain suite passes**

Run: `python -m pytest tests/test_domain.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the accounting port**

```bash
git add sipd/domain.py tests/test_domain.py
git commit -m "feat: port decimal portfolio accounting"
```

### Task 3: Add SQLite initialization and owned repositories

**Files:**
- Create: `sipd/db.py`, `sipd/repositories.py`, `tests/test_repositories.py`
- Modify: `sipd/__init__.py`, `tests/conftest.py`

**Interfaces:**
- Produces `get_db() -> sqlite3.Connection`, `transaction()`, `init_db(path: str) -> None`, `get_asset(user_id: int, asset_id: int)`, `list_assets(user_id: int, active_only: bool)`, and repository functions for current tables.
- Consumed by auth, routes, and refresh logic.

- [ ] **Step 1: Write schema and ownership tests**

```python
def test_existing_migration_creates_current_schema(app):
    with app.app_context() as _:
        assert get_db().execute("SELECT 1 FROM users").fetchone()[0] == 1

def test_get_asset_does_not_cross_owner_boundary(app, two_users):
    assert get_asset(two_users.second_id, two_users.first_asset_id) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_repositories.py -v`

Expected: FAIL because database helpers and repositories are absent.

- [ ] **Step 3: Implement the SQLite layer without changing SQL semantics**

```python
def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db
```

Run `migrations.sql` with `executescript` at application startup. Keep all ownership predicates in SQL, preserve current ordering, and use `BEGIN IMMEDIATE` for snapshot transactions.

- [ ] **Step 4: Verify the repository tests pass**

Run: `python -m pytest tests/test_repositories.py -v`

Expected: PASS.

- [ ] **Step 5: Commit database compatibility**

```bash
git add sipd/db.py sipd/repositories.py sipd/__init__.py tests
git commit -m "feat: add SQLite compatibility repositories"
```

### Task 4: Preserve sessions, CSRF, and request security

**Files:**
- Create: `sipd/auth.py`, `sipd/routes.py`, `tests/test_auth.py`
- Modify: `sipd/__init__.py`

**Interfaces:**
- Produces `require_user`, `require_csrf`, `create_session`, `current_user`, and `set_security_headers`.
- Route functions receive the authenticated user from `flask.g.user`.

- [ ] **Step 1: Write compatibility and boundary tests**

```python
def test_database_backed_session_cookie_authenticates(client, existing_session):
    client.set_cookie("sipd_session", existing_session.token)
    assert client.get("/").status_code == 200

def test_mutation_without_matching_csrf_is_forbidden(client, authenticated):
    response = client.post("/logout", data={"csrf_token": "wrong"})
    assert response.status_code == 403
```

Also assert expired sessions redirect to `/login`, logout deletes the session, login accepts an existing bcrypt hash, and every configured security header is present.

- [ ] **Step 2: Run authentication tests to verify they fail**

Run: `python -m pytest tests/test_auth.py -v`

Expected: FAIL because auth decorators are absent.

- [ ] **Step 3: Implement the opaque-session contract**

```python
def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def valid_csrf(submitted: str, stored: str) -> bool:
    return hmac.compare_digest(submitted, stored)
```

Create tokens with `secrets.token_urlsafe`, set the `sipd_session` cookie with the existing secure and same-site behavior, query the `sessions` table by hash, and use `bcrypt.checkpw` for login. Keep the per-IP in-memory login window.

- [ ] **Step 4: Verify authentication tests pass**

Run: `python -m pytest tests/test_auth.py -v`

Expected: PASS.

- [ ] **Step 5: Commit security compatibility**

```bash
git add sipd/auth.py sipd/__init__.py sipd/routes.py tests/test_auth.py
git commit -m "feat: preserve database-backed authentication"
```

### Task 5: Port providers and batch Yahoo quotes through yfinance

**Files:**
- Create: `sipd/providers.py`, `tests/test_providers.py`

**Interfaces:**
- Produces `Quote`, `yahoo_quotes(symbols: tuple[str, ...]) -> tuple[dict[str, Quote], dict[str, str]]`, `quote_for_asset(asset) -> Quote`, and `usd_idr_quote() -> Quote`.
- Consumed by ticker lookup, price lookup, and refresh.

- [ ] **Step 1: Write provider tests at the HTTP/yfinance boundary**

```python
def test_yahoo_batch_maps_each_last_close(monkeypatch):
    def fake_download(*args, **kwargs):
        return pandas.DataFrame({
            ("BBRI.JK", "Close"): [4100, 4200],
            ("BMRI.JK", "Close"): [5000, 5100],
        })
    monkeypatch.setattr(yf, "download", fake_download)
    quotes, errors = yahoo_quotes(("BBRI.JK", "BMRI.JK"))
    assert quotes["BBRI.JK"].price == Decimal("4200")
    assert quotes["BMRI.JK"].currency == "IDR"
    assert errors == {}
```

Add cases for missing ticker columns, empty close series, zero price, yfinance exceptions, Kraken alias normalization, Metals.dev unit conversion, Finnhub validation, Frankfurter response validation, and HTTP 429.

- [ ] **Step 2: Run provider tests to verify they fail**

Run: `python -m pytest tests/test_providers.py -v`

Expected: FAIL because the provider module is absent.

- [ ] **Step 3: Implement only the current provider set**

```python
@lru_cache(maxsize=16)
def _cached_yahoo_quotes(symbols, time_bucket):
    frame = yf.download(list(symbols), period="5d", interval="1d",
                        group_by="ticker", auto_adjust=False,
                        progress=False, threads=False)
    return map_final_closes(frame, symbols)

def yahoo_quotes(symbols):
    return _cached_yahoo_quotes(tuple(sorted(symbols)), int(time.time() // 30))
```

Return one error per invalid symbol instead of raising away valid symbols. Use `requests.Session` with a six-second timeout for non-Yahoo providers. Treat a 429 as a non-retryable provider error and do not expose secrets in messages.

- [ ] **Step 4: Verify provider tests pass**

Run: `python -m pytest tests/test_providers.py -v`

Expected: PASS.

- [ ] **Step 5: Commit provider behavior**

```bash
git add sipd/providers.py tests/test_providers.py
git commit -m "feat: add yfinance quote provider"
```

### Task 6: Port page, forms, and browser routes

**Files:**
- Create: `sipd/routes.py`, `sipd/templates/page.html`, `tests/test_routes.py`
- Create: `sipd/static/app.css`, `sipd/static/app.js`, `sipd/static/webawesome/`

**Interfaces:**
- Produces all current browser routes and page context keys.
- Consumes repository, auth, provider, and domain interfaces from Tasks 2–5.

- [ ] **Step 1: Write route-contract tests before route code**

```python
def test_existing_asset_url_redirects_after_save(client, authenticated, type_id):
    response = client.post("/assets", data={
        "csrf_token": authenticated.csrf,
        "name": "Cash", "type_id": type_id, "unit": "IDR",
        "scale": "0", "quote_currency": "IDR", "pricing_mode": "fixed",
    })
    assert response.status_code == 303
    assert response.headers["Location"].startswith("/assets/")
```

Cover setup, login, dashboard, asset create/edit/archive/detail, transaction create/delete/detail/list, settings, ticker lookup, static URLs, ownership 404s, invalid form 400s, and current navigation/template markers.

- [ ] **Step 2: Run route tests to verify they fail**

Run: `python -m pytest tests/test_routes.py -v`

Expected: FAIL because route and template modules are absent.

- [ ] **Step 3: Translate the route and template contract**

Convert Go template actions to Jinja expressions and filters, preserving form fields, element IDs, CSS classes, JavaScript hooks, and navigation order. Copy the existing embedded `static/webawesome` tree plus `app.css` and `app.js` into Flask static paths. Implement routes with `render_template`, `redirect(..., 303)`, `abort`, and explicit JSON responses; do not add APIs or alter URLs.

- [ ] **Step 4: Verify route and static tests pass**

Run: `python -m pytest tests/test_routes.py -v`

Expected: PASS.

- [ ] **Step 5: Commit route/UI parity**

```bash
git add sipd/routes.py sipd/templates sipd/static tests/test_routes.py
git commit -m "feat: port server-rendered portfolio routes"
```

### Task 7: Implement refresh, lookup fallbacks, and snapshots

**Files:**
- Modify: `sipd/routes.py`, `sipd/repositories.py`, `tests/test_routes.py`

**Interfaces:**
- Produces `POST /refresh`, `GET /api/assets/<id>/price`, and `GET /api/exchange-rate` with current response shapes.
- Consumes `yahoo_quotes`, provider quotes, repository price writes, and `calculate_position`.

- [ ] **Step 1: Write refresh behavior tests**

```python
def test_refresh_batches_yahoo_and_preserves_last_price(client, authenticated, monkeypatch):
    monkeypatch.setattr(providers, "yahoo_quotes", failing_one_valid_one)
    response = client.post("/refresh", data={
        "csrf_token": authenticated.csrf, "refresh_key": "once",
    })
    assert response.status_code == 303
    row = get_db().execute(
        "SELECT price FROM asset_prices WHERE asset_id=? ORDER BY id DESC LIMIT 1",
        (authenticated.failed_asset_id,),
    ).fetchone()
    assert Decimal(row["price"]) == Decimal("50000")
    status = get_db().execute(
        "SELECT status FROM price_refreshes WHERE refresh_key=?", ("once",)
    ).fetchone()
    assert status["status"] == "partial"
```

Also test duplicate refresh key produces one snapshot, a fully valid refresh is `success`, lookup returns live data then last-known data, no usable data returns 503, and snapshot values match decimal holding calculations.

- [ ] **Step 2: Run refresh tests to verify they fail**

Run: `python -m pytest tests/test_routes.py -k 'refresh or price_lookup or exchange_rate' -v`

Expected: FAIL because refresh and lookup behavior is incomplete.

- [ ] **Step 3: Implement provider partitioning and atomic snapshot writes**

```python
yahoo_assets = [asset for asset in automatic_assets if asset.provider == "yahoo"]
quotes, yahoo_errors = yahoo_quotes(tuple(asset.provider_symbol for asset in yahoo_assets))
for asset in yahoo_assets:
    save_quote_or_record_error(asset, quotes, yahoo_errors, errors)
```

Fetch other providers once per asset, insert valid prices and rates, then use `BEGIN IMMEDIATE` to insert `price_refreshes`, calculate holdings, and insert `portfolio_snapshots` plus `portfolio_snapshot_items`. Keep the existing last valid price untouched for every error.

- [ ] **Step 4: Verify the focused and full suites pass**

Run: `python -m pytest tests/test_routes.py -v && python -m pytest -v`

Expected: PASS.

- [ ] **Step 5: Commit refresh parity**

```bash
git add sipd/routes.py sipd/repositories.py tests/test_routes.py
git commit -m "feat: port refresh snapshots and price fallbacks"
```

### Task 8: Package, deploy, and prove cutover compatibility

**Files:**
- Create: `deploy/sip-d-python.service`, `tests/test_cutover.py`
- Modify: `README.md`, `.env.example`, `deploy/nginx.conf.example`

**Interfaces:**
- Produces a Gunicorn systemd unit that binds `SIPD_ADDR` and reads the current environment file and database path.
- The previous `/opt/sip-d/sip-d` Go binary remains a rollback artifact.

- [ ] **Step 1: Write a production-format database smoke test**

```python
def test_app_opens_copy_of_existing_schema(tmp_path):
    db_path = tmp_path / "sip-d.db"
    init_db(str(db_path))
    app = create_app({"SIPD_DB": str(db_path)})
    assert app.test_client().get("/healthz").status_code == 200
```

Add a test that an inserted legacy session row authenticates after `create_app` opens the database.

- [ ] **Step 2: Run it to verify it fails if deployment wiring is absent**

Run: `python -m pytest tests/test_cutover.py -v`

Expected: FAIL until the final app configuration and WSGI path are complete.

- [ ] **Step 3: Write the production unit and runbook**

```ini
[Service]
User=sip-d
Group=sip-d
EnvironmentFile=-/etc/sip-d.env
Environment=SIPD_ADDR=127.0.0.1:8090
Environment=SIPD_DB=/var/lib/sip-d/sip-d.db
ExecStart=/opt/sip-d/venv/bin/gunicorn --bind ${SIPD_ADDR} sipd.wsgi:app
ReadWritePaths=/var/lib/sip-d
```

Document virtual-environment installation, backup before switching services, `systemctl` reload/restart, health check, login/refresh smoke checks, and immediate Go-service rollback. Mark `SIPD_RAPIDAPI_KEY` as retained-but-unused; retain the variable so current environment files continue to load.

- [ ] **Step 4: Run final verification**

Run: `python -m pytest -v && /opt/sip-d/venv/bin/gunicorn --check-config sipd.wsgi:app`

Expected: all tests PASS and Gunicorn exits 0.

- [ ] **Step 5: Commit deployment artifacts**

```bash
git add deploy README.md .env.example tests/test_cutover.py
git commit -m "docs: add Flask deployment and rollback runbook"
```

## Plan self-review

## Execution status (2026-08-27)

- [x] Flask app factory, SQLite schema compatibility, Decimal accounting, database-backed sessions, CSRF, and login limiting.
- [x] yfinance batching plus Kraken, Metals.dev, Finnhub, Frankfurter, and last-known-price fallbacks.
- [x] Browser routes, Jinja forms, static URLs, transaction/archive/type workflows, refresh snapshots, and snapshot items.
- [x] Gunicorn systemd cutover unit, environment compatibility notes, runbook, rollback steps, and smoke checks.

- Spec coverage: Tasks 1–3 cover Flask, SQLite, schema, and Decimal parity; Task 4 covers sessions, CSRF, and headers; Task 5 covers yfinance and existing providers; Tasks 6–7 cover every route, UI, refresh, and snapshot behavior; Task 8 covers service, migration safety, cutover, and rollback.
- Placeholder scan: no deferred implementation markers or unspecified components remain.
- Interface consistency: all later route and refresh tasks consume the exact `create_app`, repository, auth, domain, and provider interfaces established by earlier tasks.
