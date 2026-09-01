# Language Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global ID/EN language toggle to the SIP-D app shell while keeping Settings as the full preference page.

**Architecture:** Reuse the existing `user_settings.language` field and `POST /settings/language` route. Add safe `next` redirect handling in `sipd/routes.py`, render the toggle in `sipd/templates/page.html`, and style it with existing CSS patterns.

**Tech Stack:** Python 3.12, Flask, Jinja, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-language-toggle-design.md`

## Global Constraints

- Work only in the Python/Flask implementation; the Go implementation is deprecated.
- Keep Indonesian (`ID`) as the default language.
- Keep English (`EN`) as the only alternate language.
- Reuse the existing language setting storage.
- Keep language changes authenticated and CSRF-protected.
- Prevent open redirects through the `next` field.

---

### Task 1: Add Safe Redirect Handling

**Files:** Modify `sipd/routes.py`; test `tests/test_routes.py`.

**Interfaces:** `POST /settings/language` accepts optional `next` and redirects only to safe internal paths.

- [ ] **Step 1: Write failing redirect tests.**

Cover `next=/assets`, `next=/transactions`, an empty `next`, `https://example.com`, and `//example.com`.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k language -q
```

Expected: FAIL because `/settings/language` always redirects to `/settings`.

- [ ] **Step 3: Implement safe redirect helper.**

Add a small helper that returns the provided path only when it starts with a single `/` and does not contain a scheme or host. Otherwise return `/settings`.

- [ ] **Step 4: Use helper in language route.**

After a valid language update, redirect to the safe `next` path.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k language -q
git diff --check
```

Expected: PASS.

---

### Task 2: Render Global ID/EN Toggle

**Files:** Modify `sipd/templates/page.html`, `static/app.css`; test `tests/test_routes.py`.

**Interfaces:** Authenticated app pages render a compact language toggle form in the app shell.

- [ ] **Step 1: Write failing shell-rendering tests.**

Assert `/`, `/assets`, and `/settings` render a language toggle with both ID and EN controls.

- [ ] **Step 2: Verify red.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k language -q
```

Expected: FAIL because the global toggle is absent.

- [ ] **Step 3: Update the template.**

Render two small POST forms or buttons, one for ID and one for EN. Include the CSRF token and `next=request.path`.

- [ ] **Step 4: Style the active state.**

Use existing compact button styles. The active language should look selected and should not cause layout shift.

- [ ] **Step 5: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k language -q
git diff --check
```

Expected: PASS.

---

### Task 3: Keep Settings Language Selector Working

**Files:** Modify `sipd/templates/page.html`; test `tests/test_routes.py`.

**Interfaces:** The Settings page keeps the existing full language selector and save button.

- [ ] **Step 1: Add regression tests.**

Assert Settings still renders the language selector and valid saves still update `user_settings.language`.

- [ ] **Step 2: Verify.**

Run:

```bash
python3 -m pytest tests/test_routes.py -k language -q
```

Expected: PASS.

---

### Task 4: Full Verification

**Files:** No production changes beyond earlier tasks.

- [ ] **Step 1: Run full verification.**

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
