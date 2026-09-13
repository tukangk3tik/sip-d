# Asset Status Toggle Design

## Goal

Let users activate and deactivate assets directly from the Assets list without deleting asset history.

The feature should make asset status visible, reversible, and safe. Deactivated assets should stop participating in active dashboard and refresh workflows, while their transaction history and stored prices remain available.

## Problem

SIP-D already has an `assets.active` field and dashboard queries use active assets. The current list workflow is one-sided: users can archive or deactivate an asset, but reactivation is not obvious from the list.

Users need a simple way to:

- See whether an asset is active or inactive.
- Deactivate an asset from the list.
- Reactivate an inactive asset from the list.
- Keep historical records intact.

## Scope

In scope:

- Show active and inactive assets in the Assets list.
- Add status badges for active and inactive assets.
- Add list-level activate/deactivate POST actions.
- Preserve all transactions, asset prices, snapshots, and detail pages.
- Keep inactive assets hidden from dashboard holdings, largest assets, allocations, and price refresh.
- Protect the Wallet/RDN asset from deactivation.
- Add Indonesian and English labels.
- Add route and rendering tests.

Out of scope:

- Hard deletion of assets.
- Bulk activate/deactivate.
- Filtering by archived date.
- Changing historical snapshots.
- New database schema.
- Changing transaction or Wallet accounting.

## Behavior

Assets list behavior:

- Active assets appear before inactive assets.
- Inactive assets remain visible but use muted styling.
- Each asset displays an `Active` or `Inactive` badge.
- Active non-Wallet assets show a `Deactivate` action.
- Inactive non-Wallet assets show an `Activate` action.
- Wallet/RDN shows its status but does not show a deactivate action.

Activation behavior:

- `POST /assets/<asset_id>/activate` sets `assets.active = 1`.
- It updates `updated_at`.
- It requires a valid authenticated user and CSRF token.
- It only affects assets owned by the current user.
- It redirects back to `/assets`.

Deactivation behavior:

- `POST /assets/<asset_id>/deactivate` sets `assets.active = 0`.
- It updates `updated_at`.
- It requires a valid authenticated user and CSRF token.
- It only affects assets owned by the current user.
- It redirects back to `/assets`.
- It rejects deactivation of the Wallet/RDN asset.

Dashboard behavior:

- Inactive assets do not appear in dashboard holdings.
- Inactive assets do not appear in largest assets.
- Inactive assets do not contribute to allocation lists.
- Inactive automatic assets are not included in price refresh.

Asset detail behavior:

- Inactive assets can still be opened from the Assets list.
- Transaction history remains visible.
- Editing an inactive asset should remain possible unless a future feature decides otherwise.

## UI Copy

English labels:

- `Active`
- `Inactive`
- `Activate`
- `Deactivate`
- `Asset status`

Indonesian labels:

- `Aktif`
- `Tidak aktif`
- `Aktifkan`
- `Nonaktifkan`
- `Status aset`

## Security and Data Rules

- State-changing actions must use POST.
- CSRF validation is required.
- Asset ownership must be checked in SQL.
- No deactivation route may change another user's asset.
- Wallet/RDN is protected because it supports automatic cash movements.

## Acceptance Tests

Pytest should cover:

- Assets list renders active and inactive assets.
- Assets list renders active/inactive badges.
- Active asset can be deactivated from the list.
- Inactive asset can be activated from the list.
- Toggle actions require CSRF.
- Another user's asset cannot be toggled.
- Wallet/RDN cannot be deactivated.
- Deactivated asset is absent from dashboard holdings.
- Deactivated automatic asset is absent from refresh provider calls.
- Indonesian labels render by default and English labels render when selected.
