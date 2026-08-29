# Wallet Design

## Goal

Restore the old RDN cash workflow in Flask, renamed **Wallet** for every user-facing label. It remains a fixed-IDR ledger that funds buys and receives sale proceeds.

## Behavior

- The authenticated sidebar contains **Wallet** linking to `GET /wallet`.
- Every user gets one protected, active fixed-IDR `RDN` asset under a `Wallet` investment type. `RDN` is retained only as an internal ledger identifier; page titles, forms, labels, and navigation say **Wallet**.
- Provisioning is lazy and idempotent, invoked by `/wallet`, Wallet adjustments, and non-Wallet buys/sells. It inserts the fixed price `1` if missing.
- Existing `RDN Wallet` type data is normalized to `Wallet`. If a `Wallet` type already exists, `RDN` is reassigned and the legacy type is archived.
- `POST /wallet/top-up` creates a positive IDR `deposit`; `POST /wallet/withdraw` creates a positive IDR `withdrawal` only when replaying the Wallet ledger remains non-negative.
- Every non-Wallet `buy` creates a paired Wallet `withdrawal`; every non-Wallet `sell` creates a paired Wallet `deposit`. The value is `quantity × unit_price × fx_rate_to_idr`.
- Origin and paired entries share one SQLite transaction. A buy with insufficient Wallet funds writes neither transaction.
- Paired rows use `rdn:auto:<origin-id>` and are never directly deletable. Deleting an origin validates both ledgers without their candidate rows, removes both rows atomically, and rebuilds transaction-derived prices for each affected asset.

## Wallet page

`GET /wallet` shows IDR balance, available units, fixed pricing, top-up/withdraw forms, and the Wallet ledger. Forms take `amount`, optional `occurred_at`, optional `notes`, CSRF token, and idempotency key. Valid actions redirect to `/wallet`; duplicate keys redirect without another write.

## Constraints

- Use `Decimal` for all money and replay the established `calculate_position` rules.
- Keep every SQL query user-scoped and use `transaction(db)` for paired changes.
- Existing generic transaction behavior remains unchanged except the automatic pair for a non-Wallet buy/sell.
- No schema migration, new dependency, payment integration, bank synchronization, or multi-currency wallet.

## Acceptance tests

Pytest covers sidebar navigation, provisioning, legacy type normalization, top-up, withdrawal overdraft rejection, automatic buy debit, automatic sell credit, duplicate submission, rejection of direct automatic-row deletion, paired deletion, and a full-suite/Gunicorn verification.
