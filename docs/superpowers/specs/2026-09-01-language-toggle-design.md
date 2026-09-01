# Language Toggle Design

## Goal

Add a quick ID/EN language toggle to the SIP-D app shell so users can change language without opening Settings.

Settings should continue to provide the full preference form, but the common language switch should be available globally.

## Problem

SIP-D already supports Indonesian as the default language and English as an alternative. The current language choice lives in Settings only. That makes translation testing and day-to-day switching slower than necessary.

Users need a compact, always-available toggle that:

- Shows the current language.
- Lets users switch between `ID` and `EN`.
- Keeps them on the page they were viewing.
- Reuses the existing language preference storage.

## Scope

In scope:

- Add a compact ID/EN segmented toggle to the app shell.
- Reuse `POST /settings/language`.
- Add a safe `next` redirect so language changes return to the current page.
- Keep the Settings language selector.
- Add active-state styling for the selected language.
- Add Indonesian and English labels where needed.
- Add route and rendering tests.

Out of scope:

- Adding more languages.
- Browser language detection.
- Anonymous language preferences.
- Per-request query-string language overrides.
- Client-side translation.
- Moving translations out of `sipd/i18n.py`.

## Behavior

Global toggle behavior:

- Authenticated pages show an `ID | EN` language control in the sidebar or app shell.
- The current language is visibly selected.
- Selecting the other language submits a POST form immediately.
- The form sends `csrf_token`, `language`, and `next`.
- After a valid change, the user returns to the page they came from.

Settings behavior:

- The existing Settings language selector remains.
- It may also use the same `next` redirect behavior, but default redirect remains `/settings` when no safe `next` is provided.

Redirect safety:

- Relative internal paths such as `/assets` and `/transactions` are allowed.
- Empty, external, protocol-relative, or suspicious values redirect to `/settings`.
- The redirect target should not allow open redirects.

## UI Placement

Preferred placement:

- Desktop: sidebar footer or lower sidebar controls.
- Mobile: inside the same mobile navigation/sidebar.

The toggle is global app preference UI. It should not be placed inside feature-specific areas such as the Assets list.

## UI Copy

The toggle can use the short labels `ID` and `EN` directly.

Optional accessible label:

- English: `Language`
- Indonesian: `Bahasa`

## Security and Data Rules

- Language changes require an authenticated user.
- CSRF validation is required.
- Only `ID` and `EN` are accepted.
- The update is scoped to the current authenticated user's `user_settings` row.
- Redirects must stay inside the app.

## Acceptance Tests

Pytest should cover:

- Authenticated app shell renders the ID/EN toggle.
- Indonesian is selected by default.
- English is selected after changing preference.
- Posting `language=EN` with `next=/assets` redirects to `/assets`.
- Posting `language=ID` with `next=/transactions` redirects to `/transactions`.
- External `next` values redirect to `/settings`.
- Invalid language values return 400.
- CSRF is required.
- The Settings language selector still renders.
