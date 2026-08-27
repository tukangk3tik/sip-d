# SIP-D

SIP-D is a lightweight, self-hosted application for recording and monitoring personal savings and investments in one private portfolio. It is designed for an owner who wants to keep financial records on infrastructure they control instead of relying on a hosted portfolio service.

The application uses a transaction ledger as its source of truth. It turns deposits, withdrawals, purchases, and sales into current holdings, weighted-average cost, invested capital, and realized or unrealized profit and loss. A dashboard combines cash, gold, money-market instruments, Indonesian stocks, and Bitcoin, with IDR or USD reporting, allocation summaries, market-price refreshes, and point-in-time portfolio snapshots.

SIP-D is a monitoring and record-keeping tool. It does not connect to brokers, execute trades, provide budgeting features, or offer financial advice. The current product is intended for one owner, while its ownership boundaries and database schema keep each user's records isolated for controlled multi-user support later.

The Flask application runs as a single Gunicorn process with server-rendered HTML and SQLite, making it suitable for a small VPS with minimal operational overhead. The Go binary remains available for rollback during cutover.

## Features

- First-owner setup, bcrypt passwords, server-side expiring sessions, CSRF protection, and login rate limiting.
- User-owned investment types, assets, and transaction ledger with strict ownership filters.
- Exact-decimal weighted-average cost, realized/unrealized P/L, and over-sell protection.
- Manual pricing plus Kraken BTC/USD and optional Metals.dev gold prices.
- Keyless daily USD/IDR reference rates from Frankfurter.
- Manual refresh snapshots with idempotency and partial-failure reporting.
- Responsive server-rendered admin interface with a desktop sidebar and mobile navigation drawer.
- Self-hosted Web Awesome 3.2.1 styles with minimal vanilla JavaScript; no CDN or SPA runtime.

## Local development

Requirements: Python 3.12+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
SIPD_DB=data/sip-d.db .venv/bin/gunicorn --bind 127.0.0.1:8090 sipd.wsgi:app
```

The pinned Web Awesome package is the development source for the vendored, embedded assets under `static/webawesome`. Production does not run Node.js.

The defaults are `127.0.0.1:8090` and `data/sip-d.db`. Environment files are not loaded automatically; export their values or let systemd load `/etc/sip-d.env`. The deployed instance is served at `https://sip-d.naratala.web.id`.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `SIPD_ADDR` | `127.0.0.1:8090` | HTTP bind address |
| `SIPD_DB` | `data/sip-d.db` | Persistent SQLite path |
| `SIPD_BASE_URL` | empty | Public HTTPS URL; enables `Secure` session cookies when it starts with `https://` |
| `SIPD_METALS_API_KEY` | empty | Optional Metals.dev key for gold pricing |
| `SIPD_FINNHUB_API_KEY` | empty | Optional Finnhub token for Indonesian stock quotes |
| `SIPD_RAPIDAPI_KEY` | empty | Optional Yahoo Finance 15 RapidAPI key for Indonesian stock lookup and quotes |

## Calculations

All persisted quantities, prices, rates, and calculated values are decimal strings. Binary floating point is not used.

- Buy/deposit: `new average cost = (old cost basis + quantity × price × transaction FX) / new quantity`.
- Sell/withdrawal: remaining average cost is unchanged; `realized P/L = proceeds in IDR − average cost × quantity`.
- Unrealized P/L: `current converted market value − remaining cost basis`.
- Net invested capital: buys/deposits minus sell/withdrawal proceeds, using each transaction's preserved FX rate.
- Total return: `(realized P/L + unrealized P/L) / net invested capital × 100`. It is shown as zero when net invested capital is not positive.

Deleting an erroneous transaction is allowed only when replaying the remaining ledger does not create a negative holding. A dashboard snapshot is created only by manual **Refresh Prices**, never by transaction price lookup.

## Providers

- **Kraken** public ticker: keyless BTC/USD last-trade price. Use provider `kraken` and symbol `XBTUSD`.
- **Frankfurter**: keyless daily USD/IDR reference rate. This is not an intraday executable exchange rate.
- **Metals.dev**: documented gold spot API; its free plan currently requires a key and allows 100 requests/month. Use provider `metalsdev` and symbol `gold`.
- **Indonesian stocks**: Yahoo Finance 15 through RapidAPI supports exact `.JK` symbols such as `BBCA.JK`, returns IDR, and consumes the account's RapidAPI quota. Finnhub remains available as a fallback but international quotes may require account entitlement. Failed or zero quotes never replace the last valid price.
- **Money-market funds**: manual unless a specific instrument later receives a reliable supported provider.

Provider calls have a six-second timeout and one limited retry. A failed refresh never overwrites the last valid price. Successful assets are retained and failures are shown per asset.

## Flask cutover

Back up the existing SQLite database, install the Python app and virtual environment in `/opt/sip-d`, then install `deploy/sip-d-python.service` as `/etc/systemd/system/sip-d-python.service`.

```sh
sudo -u sip-d sqlite3 /var/lib/sip-d/sip-d.db ".backup '/var/lib/sip-d/pre-flask-cutover.db'"
sudo -u sip-d python3 -m venv /opt/sip-d/venv
sudo -u sip-d /opt/sip-d/venv/bin/pip install -r /opt/sip-d/requirements.txt
sudo install -o root -g root -m 0644 deploy/sip-d-python.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl stop sip-d
sudo systemctl enable --now sip-d-python
curl --fail http://127.0.0.1:8090/healthz
```

Log in and run one Refresh Prices smoke check before changing the reverse proxy. To roll back, stop `sip-d-python`, start `sip-d`, and restore the backup only if data was changed incompatibly.

## Legacy Go deployment

Build and install:

```sh
go test ./...
go vet ./...
go build -trimpath -ldflags='-s -w' -o sip-d .
sudo install -d -o sip-d -g sip-d -m 0750 /var/lib/sip-d
sudo install -D -o root -g root -m 0755 sip-d /opt/sip-d/sip-d
sudo install -o root -g root -m 0644 deploy/sip-d.service /etc/systemd/system/sip-d.service
sudo systemctl daemon-reload
sudo systemctl enable --now sip-d
curl --fail http://127.0.0.1:8090/healthz
```

Copy and adapt `deploy/nginx.conf.example`, validate with `sudo nginx -t`, then reload Nginx. Obtain a certificate through the VPS's existing certificate workflow before setting `SIPD_BASE_URL=https://...` in `/etc/sip-d.env`.

Management:

```sh
sudo systemctl status sip-d
sudo journalctl -u sip-d -f
sudo systemctl restart sip-d
```

Logs contain operational errors only. Passwords, session tokens, cookies, and financial request bodies are not logged.

## Backup and restore

Use SQLite's online backup command while the service is running:

```sh
sudo -u sip-d sqlite3 /var/lib/sip-d/sip-d.db ".backup '/var/lib/sip-d/backup-$(date +%F).db'"
```

Copy the resulting backup to separate durable storage. Restore during downtime:

```sh
sudo systemctl stop sip-d
sudo -u sip-d sqlite3 /var/lib/sip-d/sip-d.db ".restore '/path/to/verified-backup.db'"
sudo systemctl start sip-d
curl --fail http://127.0.0.1:8090/healthz
```

## Update and rollback

Back up the database, build and test a new binary, then atomically replace `/opt/sip-d/sip-d` and restart the service. Keep the previous binary as `/opt/sip-d/sip-d.previous`. Roll back by restoring that binary and restarting. Schema migrations are forward-only; preserve the matching pre-update database backup for a full rollback.

## Troubleshooting

- `503` during lookup: enter an editable manual transaction price; the provider may be unavailable.
- Partial refresh: successful prices remain updated and each failure appears on the dashboard.
- USD display unavailable: run Refresh Prices to obtain USD/IDR.
- Database busy: verify only one SIP-D process uses the database and that it resides on local storage.
