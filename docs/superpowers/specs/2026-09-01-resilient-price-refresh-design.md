# Resilient Price Refresh Design

## Goal

Make SIP-D stock-price refresh resilient to unofficial Yahoo Finance limits and transient provider failures while keeping the app free, simple, and suitable for the current Python/Flask implementation.

The dashboard should keep showing useful portfolio data even when live quote refresh is partial or unavailable.

## Problem

SIP-D currently relies on Yahoo Finance through `yfinance` for `.JK` stock quotes. Yahoo Finance does not provide a stable public quota for these unofficial endpoints. It can return empty data, throttle requests, or fail differently depending on request burst, IP reputation, session state, and endpoint behavior.

Provider failures must not:

- Expose pandas, yfinance, or HTTP implementation errors to the UI.
- Overwrite a valid stored price with empty or invalid data.
- Prevent snapshots from using the latest known valid prices.
- Encourage repeated manual refresh attempts that worsen rate limiting.

## Scope

In scope:

- Batch Yahoo stock refreshes where possible.
- Keep `yfinance` as the primary free stock provider.
- Add a direct Yahoo chart request fallback using `requests`.
- Add a durable quote cache backed by SQLite.
- Add refresh cooldown and provider backoff behavior.
- Keep last known good prices as the final fallback.
- Improve dashboard refresh status wording.
- Add tests for cache hits, fallback behavior, partial failures, stale prices, and clean errors.

Out of scope:

- Paid market-data subscriptions.
- A background scheduler.
- Redis, Celery, or another cache service.
- Replacing the existing provider names in user data.
- Full provider marketplace or plugin architecture.
- Intraday charts, historical performance charts, or technical indicators.

## Provider Order

Quote resolution follows this order:

1. Use a fresh cached DB quote when it is inside the freshness window.
2. Fetch missing or stale Yahoo symbols with `yfinance` in batches.
3. For each failed Yahoo symbol, try the direct Yahoo chart endpoint.
4. If a later IDX-specific provider is configured, try it after Yahoo fallbacks.
5. If every live provider fails, use the last known good price and mark it stale.

Manual and fixed-price assets keep their existing behavior and do not use Yahoo.

## Quote Cache

The cache is stored in SQLite so it survives process restarts. Each cached quote records:

- `symbol`
- `provider`
- `price`
- `currency`
- `fetched_at`
- `source`
- `error`
- `error_at`
- `failure_count`
- `backoff_until`

Cached prices are valid only when the provider currency matches the asset quote currency and the price is positive.

The default freshness window should be conservative, around 5 to 15 minutes. Tests may set it to shorter values.

## Refresh Cooldown and Backoff

SIP-D should avoid refetching the same ticker repeatedly:

- If a cached quote is fresh, skip live provider calls and report `cached`.
- If a provider recently failed, skip live calls until `backoff_until` and report `backing_off`.
- Manual refresh still creates a refresh result, but it should explain which assets were updated, cached, stale, or skipped.

Backoff can start small and grow by failure count, for example 1 minute, 5 minutes, 15 minutes, then 30 minutes. A successful quote clears the failure count and backoff.

## Last Known Good Price

Every successful provider quote already has a corresponding persisted asset price. When all live providers fail, SIP-D should:

- Read the latest valid stored price for the asset.
- Use it for holdings and portfolio snapshot calculations.
- Mark the refresh item as stale or using last known price.
- Preserve the previous valid value instead of writing a failed or empty price.

If no previous valid price exists, the asset is marked failed for that refresh and excluded from live valuation as it is today.

## User-Facing Status

Refresh status should describe outcomes in application terms:

- `Updated successfully`
- `Using cached price`
- `Using last known price`
- `Skipped, recently refreshed`
- `Provider temporarily unavailable`
- `Yahoo Finance returned no quote data`

Internal exception names, stack traces, pandas index errors, and raw HTTP parser errors must not appear in the dashboard.

## Configuration

Use environment variables for tuning, with safe defaults:

- `SIPD_QUOTE_CACHE_TTL_SECONDS`
- `SIPD_QUOTE_BACKOFF_INITIAL_SECONDS`
- `SIPD_QUOTE_BACKOFF_MAX_SECONDS`
- `SIPD_YAHOO_TIMEOUT_SECONDS`
- `SIPD_YAHOO_BATCH_SIZE`

The app should work without setting any of them.

## Acceptance Tests

Pytest should cover:

- Fresh cached quote prevents a provider call.
- Stale cached quote triggers live refresh.
- `yfinance` batch success stores prices and clears failure state.
- Empty `yfinance` data falls back to direct Yahoo chart lookup.
- Direct Yahoo chart success stores a valid quote.
- Both Yahoo paths failing uses last known good price.
- Both Yahoo paths failing with no previous price records a clean asset failure.
- Provider backoff skips live calls until the backoff window expires.
- Manual refresh status shows cached, stale, updated, skipped, and failed items clearly.
- No pandas, yfinance, or raw HTTP internal error is rendered to the dashboard.

## Rollout

This can be shipped incrementally:

1. Add cache schema and repository helpers.
2. Add provider fallback without changing UI behavior.
3. Add cooldown and backoff.
4. Update refresh-status UI copy.
5. Tune cache TTL and backoff after observing real refresh behavior.
