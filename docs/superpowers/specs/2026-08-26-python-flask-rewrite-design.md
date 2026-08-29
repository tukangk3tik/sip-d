# SIP-D Python/Flask Rewrite Design

## Goal

Replace the Go server with a Flask/Jinja application while preserving the deployed app's database, routes, server-rendered UI, security behavior, and systemd/Nginx operating model. Replace only the RapidAPI Yahoo implementation with `yfinance` for Yahoo `.JK` prices.

## Scope

The replacement preserves:

- The existing SQLite schema and every existing row. `migrations.sql` remains the startup schema script and must stay idempotent.
- All current HTTP routes, request fields, redirects, response status codes, asset URLs, cookies, and static asset paths.
- Existing bcrypt password hashes, opaque `sipd_session` cookie values, database-backed sessions, and CSRF tokens. A user with a valid Go-created session remains authenticated after cutover.
- The current single-owner UI and its Web Awesome assets. Templates are translated to Jinja without a frontend redesign.
- `SIPD_ADDR`, `SIPD_DB`, `SIPD_BASE_URL`, `SIPD_METALS_API_KEY`, `SIPD_FINNHUB_API_KEY`, and `SIPD_RAPIDAPI_KEY` environment variables. The RapidAPI key is accepted for deployment compatibility but is no longer used.
- Kraken, Metals.dev, Frankfurter, manual pricing, fixed cash pricing, snapshots, weighted-average accounting, ownership rules, and partial-refresh behavior.

Out of scope: an ORM, API redesign, SPA frontend, database migration, background scheduler, multi-user product work, and market-data licensing changes.

## Architecture

Use Python 3.12 with Flask, Jinja, the standard-library `sqlite3` and `decimal.Decimal`, `bcrypt`, `requests`, `yfinance`, and Gunicorn. Flask's app factory owns configuration and registers the route module; it does not use Flask's signed-cookie session feature. Authentication retains the current opaque, SHA-256-hashed session ID stored in SQLite.

```text
Nginx -> Gunicorn -> Flask routes -> repositories -> SQLite
                              -> providers -> yfinance / HTTP APIs
                              -> Jinja templates + existing static assets
```

The source is split only at existing responsibility boundaries:

- `sipd/app.py`: app factory, configuration, startup schema initialization, and security headers.
- `sipd/db.py`: SQLite connection lifecycle and transaction helper.
- `sipd/domain.py`: `Decimal` ledger replay and currency conversion.
- `sipd/repositories.py`: SQL reads and writes with explicit `user_id` filters.
- `sipd/auth.py`: opaque sessions, CSRF, login throttling, and request-user loading.
- `sipd/providers.py`: quote types, HTTP providers, and Yahoo batch quotes through `yfinance`.
- `sipd/routes.py`: the existing route contract and page/API responses.
- `sipd/templates/page.html`: the translated existing page template.
- `sipd/wsgi.py`: Gunicorn entry point.

No SQLAlchemy, Celery, Redis, or custom provider framework is introduced.

## Route and UI Compatibility

The Flask application serves the existing health, setup, login/logout, dashboard, refresh, assets, transactions, settings, ticker lookup, price lookup, and exchange-rate routes. Form names, redirect targets, CSRF field name, flash/error wording where practical, JSON keys, and static URLs are preserved.

The Go template in `ui.go` is translated into one Jinja template, retaining the existing CSS classes and JavaScript hooks. `static/webawesome`, `app.css`, and `app.js` are served as normal Flask static files at their current URLs.

## Database and Security Compatibility

Connections enable foreign keys, WAL mode, a 5-second busy timeout, and explicit transactions for refresh snapshot writes. All persisted decimal values remain strings and are reconstructed with `Decimal`; floats are never used for money or quantities.

`bcrypt.checkpw` validates current hashes. Session lookup hashes the incoming `sipd_session` cookie with SHA-256 and loads the existing `sessions` row; creation, expiry, secure-cookie behavior, logout, and CSRF validation follow the current schema and cookie name. Constant-time comparisons protect CSRF validation. Login attempts retain the current per-process, per-IP window.

The current response security headers remain: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and the self-only content-security policy.

## Market Data

`provider = 'yahoo'` keeps its database meaning but is backed by `yfinance`:

- Refresh groups all active Yahoo assets and makes one `yf.download` request for the symbols, using daily close data. It maps each valid final close to the asset's configured quote currency and records source `Yahoo Finance (yfinance)`.
- Ticker lookup validates one exact Yahoo symbol. `yfinance` is not presented as an unlimited or licensed market-data service.
- Price lookup reuses a 30-second in-process cache for Yahoo results; on failure it returns the existing stored last-known price, otherwise a 503 exactly as today.
- Missing, empty, non-positive, or currency-mismatched data is a per-asset failure. It never replaces a valid stored price.

Kraken, Metals.dev, Finnhub, and Frankfurter retain their existing endpoint behavior, timeout, response validation, and last-known-price fallback.

## Refresh and Accounting Behavior

A refresh validates its idempotency key before provider work. It obtains USD/IDR, fetches provider quotes, saves valid rates/prices, records individual provider errors, and records a `success` or `partial` refresh. It then uses one SQLite transaction to create the refresh record, calculate holdings, and insert the portfolio snapshot and snapshot items.

The ledger calculation preserves sort order, weighted-average cost, realized P/L, over-sell prevention, fixed price behavior, USD/IDR conversion, and exact decimal formatting. A failed provider never removes or overwrites the latest valid price.

## Testing and Cutover

Pytest covers exact decimal ledger results; database ownership boundaries; Go-hash-compatible session authentication; CSRF; all route methods and redirects; asset and transaction validation; refresh idempotency and partial failure; yfinance batch quote mapping; and the existing static paths.

Before deployment, run the Flask suite against a copied production-format SQLite database. Cutover backs up the live database, installs the Python virtual environment and Gunicorn entry point, stops the Go service, starts the Flask service on the same bind address, and checks `/healthz`, an existing login/session, dashboard rendering, and a manual refresh. The current Go binary remains available as the rollback target; rollback restores its systemd `ExecStart` and restarts it against the unchanged database.

