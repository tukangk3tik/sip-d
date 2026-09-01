# Asset Status Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add list-level activate/deactivate controls for SIP-D assets while preserving history and keeping inactive assets out of active dashboard and refresh workflows.

**Architecture:** Reuse the existing `assets.active` column. Add explicit POST routes in `sipd/routes.py`, render status badges and actions in `sipd/templates/page.html`, and add labels in `sipd/i18n.py`. No schema change is required.

**Tech Stack:** Python 3.12, Flask, Jinja, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-asset-status-toggle-design.md`

## Global Constraints

- Work only in the Python/Flask implementation; the Go implementation is deprecated.
- Do not delete asset, transaction, price, or snapshot rows.
- Keep all state-changing actions as POST with CSRF validation.
- Keep every asset mutation scoped by `user_id`.
- Do not allow Wallet/RDN to be deactivated.
- Preserve existing dashboard active-asset behavior.

---

### Task 1: Render Status Badges in the Assets List

**Files:** Modify `sipd/templates/page.html`, `sipd/i18n.py`; test `tests/test_routes.py`.

**Interfaces:** The Assets page receives active and inactive assets from the existing `/assets` route and renders status labels.

- [ ] **Step 1: Write failing rendering tests.**

Cover an active asset and inactive asset on `/assets`. Assert both render, each has a status badge, and inactive rows/cards have a muted class.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k assets -q
```

Expected: FAIL because badges and inactive styling do not exist.

- [ ] **Step 3: Update the template.**

Render `Active`/`Inactive` labels next to asset names or metadata. Keep active assets first using the current route ordering.

- [ ] **Step 4: Add translations.**

Add Indonesian and English-facing labels through `sipd/i18n.py`.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k assets -q
git diff --check
```

Expected: PASS.

---

### Task 2: Add Activate and Deactivate Routes

**Files:** Modify `sipd/routes.py`; test `tests/test_routes.py`.

**Interfaces:** Add `POST /assets/<asset_id>/activate` and `POST /assets/<asset_id>/deactivate`.

- [ ] **Step 1: Write failing route tests.**

Cover successful deactivate, successful reactivate, invalid CSRF, missing asset, and another user's asset.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k "asset and active" -q
```

Expected: FAIL because the new routes do not exist.

- [ ] **Step 3: Implement a shared status helper.**

Create a small private helper in `routes.py` that validates CSRF, checks ownership, optionally protects Wallet/RDN, updates `assets.active`, and redirects to `/assets`.

- [ ] **Step 4: Keep old archive compatibility.**

If `POST /assets/<asset_id>/archive` already exists, either keep it as an alias for deactivate or have it call the shared helper.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k "asset and active" -q
git diff --check
```

Expected: PASS.

---

### Task 3: Add List-Level Action Buttons

**Files:** Modify `sipd/templates/page.html`; test `tests/test_routes.py`.

**Interfaces:** The Assets list renders one POST form per status-changing action.

- [ ] **Step 1: Write failing UI action tests.**

Assert active non-Wallet assets render a deactivate form and inactive non-Wallet assets render an activate form.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k assets -q
```

Expected: FAIL because action forms are missing.

- [ ] **Step 3: Render action forms.**

Use existing CSRF token. Use compact buttons so the list remains scannable.

- [ ] **Step 4: Protect Wallet in the UI.**

Do not render a deactivate button for Wallet/RDN. It may still render a protected status label.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k assets -q
git diff --check
```

Expected: PASS.

---

### Task 4: Verify Dashboard and Refresh Exclusion

**Files:** Test `tests/test_routes.py`.

**Interfaces:** Existing dashboard and refresh queries should continue filtering `a.active=1`.

- [ ] **Step 1: Add regression tests.**

Cover inactive asset absence from dashboard holdings and inactive automatic asset absence from Yahoo refresh calls.

- [ ] **Step 2: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py -q
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
