# Resilient Price Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SIP-D quote refresh resilient against Yahoo/yfinance limits by adding SQLite-backed quote caching, direct Yahoo fallback, provider backoff, last-known-price fallback, and clean refresh statuses.

**Architecture:** Keep the work in the existing Python/Flask stack. `sipd/providers.py` owns live provider calls and provider-level errors. `sipd/repositories.py` owns cached quote persistence. `sipd/routes.py` orchestrates refresh behavior and dashboard status. `migrations.sql` remains idempotent and upgrades existing SQLite databases safely.

**Tech Stack:** Python 3.12, Flask, Jinja, SQLite, `Decimal`, `requests`, `yfinance`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-resilient-price-refresh-design.md`

## Global Constraints

- Work only in the Python/Flask implementation; the Go implementation is deprecated.
- Use `Decimal` for every price and portfolio value.
- Keep all SQL user-scoped where user data is involved.
- Do not add Redis, Celery, a scheduler, an ORM, or a paid data provider.
- Do not expose pandas, yfinance, stack traces, or raw HTTP parsing errors to users.
- Never overwrite a valid stored price with empty, invalid, zero, or failed provider data.
- Keep existing manual and fixed-price asset behavior unchanged.

---

### Task 1: Add Quote Cache Schema and Repository Helpers

**Files:** Modify `migrations.sql`, `sipd/repositories.py`; test `tests/test_repositories.py`.

**Interfaces:** Add cached quote helpers such as `get_cached_quote(symbol, provider)`, `save_cached_quote(...)`, `record_quote_failure(...)`, and `clear_quote_failure(...)`.

- [ ] **Step 1: Write failing schema tests.**

Cover fresh DB creation and upgrade behavior. Assert the quote cache table exists and has fields for symbol, provider, price, currency, fetched time, source, error state, failure count, and backoff time.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_repositories.py -q
```

Expected: FAIL because the cache schema and helpers do not exist.

- [ ] **Step 3: Implement the idempotent schema.**

Add a SQLite table for provider quote cache. Use a unique key on `(provider, symbol)`. Store decimal prices as strings. Keep migrations idempotent for existing databases.

- [ ] **Step 4: Implement repository helpers.**

Helpers should parse prices into `Decimal`, return `sqlite3.Row` or small data objects consistently with existing repository style, and preserve existing ownership rules.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_repositories.py -q
git diff --check
```

Expected: PASS.

---

### Task 2: Add Direct Yahoo Chart Fallback

**Files:** Modify `sipd/providers.py`; test `tests/test_providers.py`.

**Interfaces:** Add a provider helper that can fetch one Yahoo symbol through the chart endpoint when `yfinance` returns empty data or raises a provider-level failure.

- [ ] **Step 1: Write failing provider tests.**

Cover:

- Empty `yfinance` close data raises a clean no-quote error.
- Direct Yahoo chart success returns price, currency, timestamp, and source.
- Direct Yahoo chart empty response raises a clean provider error.
- Raw pandas/yfinance/HTTP errors are normalized.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_providers.py -q
```

Expected: FAIL for missing fallback behavior.

- [ ] **Step 3: Implement fallback provider.**

Use `requests` with a short timeout. Validate response shape, close values, positive price, and currency. Return the same quote shape as existing provider functions.

- [ ] **Step 4: Keep errors clean.**

Provider-facing failures should use application wording such as `Yahoo Finance returned no quote data` or `Yahoo Finance is temporarily unavailable`.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_providers.py -q
git diff --check
```

Expected: PASS.

---

### Task 3: Use Cache Before Live Provider Calls

**Files:** Modify `sipd/routes.py`, `sipd/repositories.py`; test `tests/test_routes.py` or `tests/test_workflow.py`.

**Interfaces:** Refresh orchestration checks cached quotes before calling Yahoo. Cache TTL is configurable with `SIPD_QUOTE_CACHE_TTL_SECONDS`.

- [ ] **Step 1: Write failing refresh tests.**

Cover:

- Fresh cached quote is used without calling live providers.
- Stale cached quote triggers live provider refresh.
- Successful live provider quote updates cache and asset price.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_routes.py tests/test_workflow.py -q
```

Expected: FAIL for missing cache orchestration.

- [ ] **Step 3: Add cache-first resolution.**

Refresh should group active Yahoo assets, resolve cached fresh quotes first, and only request missing or stale symbols from providers.

- [ ] **Step 4: Store successful quotes.**

On success, write both the cached quote and the asset price. Clear failure count and backoff for that symbol.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py tests/test_workflow.py -q
git diff --check
```

Expected: PASS.

---

### Task 4: Add Provider Backoff

**Files:** Modify `sipd/routes.py`, `sipd/repositories.py`; test `tests/test_workflow.py`.

**Interfaces:** A failed live provider attempt records failure count and `backoff_until`. Refresh skips symbols still inside backoff.

- [ ] **Step 1: Write failing backoff tests.**

Cover:

- First failure records initial backoff.
- Repeated failures increase backoff up to the configured maximum.
- A symbol inside backoff does not call live providers.
- A successful refresh clears backoff.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_workflow.py -q
```

Expected: FAIL for missing backoff state.

- [ ] **Step 3: Implement backoff calculation.**

Use configurable defaults, for example 60 seconds initial and 1800 seconds max. Keep it deterministic for tests by injecting or controlling current time.

- [ ] **Step 4: Surface skipped status.**

Refresh status should record `Skipped, recently refreshed` or `Provider temporarily unavailable` rather than failing noisily.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_workflow.py -q
git diff --check
```

Expected: PASS.

---

### Task 5: Preserve Last Known Good Prices

**Files:** Modify `sipd/routes.py`, `sipd/repositories.py`; test `tests/test_workflow.py`, `tests/test_routes.py`.

**Interfaces:** When live providers fail and no fresh cache is available, refresh uses the latest valid stored asset price if present.

- [ ] **Step 1: Write failing stale-price tests.**

Cover:

- Provider failure with previous price uses last known good price.
- Provider failure with no previous price records an asset-level failure.
- Stale price is visible in refresh status without changing the stored price.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_workflow.py tests/test_routes.py -q
```

Expected: FAIL where stale fallback is absent or unclear.

- [ ] **Step 3: Implement last-known fallback.**

Use existing price repository behavior where possible. Do not insert a new asset price row for fallback usage unless it came from a live successful provider response.

- [ ] **Step 4: Keep snapshot behavior stable.**

Snapshots should continue using the best available valid price. Assets with no valid price remain failed for that refresh.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_workflow.py tests/test_routes.py -q
git diff --check
```

Expected: PASS.

---

### Task 6: Improve Dashboard Refresh Status Copy

**Files:** Modify `sipd/routes.py`, `sipd/templates/page.html`, `sipd/i18n.py`; test `tests/test_routes.py`.

**Interfaces:** Refresh status entries distinguish updated, cached, stale, skipped, and failed outcomes. Indonesian remains the default language and English remains selectable.

- [ ] **Step 1: Write failing UI tests.**

Assert dashboard output includes clean user-facing refresh statuses and does not include internal provider/library exception names.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_routes.py -q
```

Expected: FAIL for missing status labels or translations.

- [ ] **Step 3: Add status labels and translations.**

Add Indonesian and English labels for cached, stale, skipped, updated, and temporarily unavailable states.

- [ ] **Step 4: Render status details.**

Refresh cards should show source, last update time, and whether the displayed value is live, cached, stale, skipped, or failed.

- [ ] **Step 5: Verify full suite.**

Run:

```bash
python3 -m pytest -q
python3 -m gunicorn --check-config sipd.wsgi:app
git diff --check
```

Expected: all commands exit 0.

---

## Final Verification

Before commit or PR:

```bash
python3 -m pytest -q
python3 -m gunicorn --check-config sipd.wsgi:app
git diff --check
git status --short
```

Expected:

- Tests pass.
- Gunicorn config is valid.
- Whitespace check is clean.
- Only intentional Python, template, test, migration, and docs files are changed.
