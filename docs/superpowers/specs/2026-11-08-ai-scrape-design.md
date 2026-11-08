# Shared AI price provider

Status: proposed design; application implementation has not started.

## Intent and agreed requirements

Add `AI Scrape` as an asset pricing provider in the Flask application. On
Refresh Prices, gather the requesting user's active assets that use this
provider, compose one batch prompt, ask the selected LLM to retrieve current
quotes, parse its response, and persist valid prices.

Support OpenAI and Claude through API keys only. Credentials belong to one
shared application configuration and serve multiple SIP-D users. Configure
them in Settings. Account/subscription login is out of scope following the
user's latest decision. Each service requires its own API key; a key cannot
be shared between OpenAI and Anthropic.

## Proposed product behavior

These are implementation recommendations, not additional user requirements:

- The original setup user administers the shared connection. Other users may
  refresh their own portfolios but cannot read, replace, test, or remove keys.
- Settings contains an AI pricing section with OpenAI and Claude key inputs,
  an active service selector, a model setting per service, and Test connection.
  Saved keys are represented only by a configured/not-configured indicator.
  Empty input keeps an existing key; removal is an explicit separate action.
- One service handles a refresh. Do not silently switch providers or billing
  accounts on failure. Selecting a service without a key shows a setup error.
- Each asset retains its symbol, currency, and unit. AI requests include those
  fields and the exchange when encoded in the symbol (for example BBCA.JK).
  Ambiguous instruments or units produce an unavailable result, not a guess.
- Request the latest verifiable market quote, permitting the most recent
  market close when trading is closed. Preserve the quote's actual timestamp,
  label whether it is a last-trade or closing price, and show its source URL.
  Do not describe these quotes as guaranteed real-time market data.
- Successful quotes update only assets owned by the requesting user. Missing,
  stale, ambiguous, or invalid results preserve prior prices and appear as
  per-asset failures. The existing snapshot and partial-refresh behavior remains.
- Stock lookup and transaction-price lookup use the same AI quote cache. A
  transaction lookup may request one instrument when uncached but does not
  create a portfolio snapshot.

## Batch retrieval and validation

Use official server-side APIs with their web-search tools enabled. A single
application request contains all uncached instruments for the refresh; the
provider may perform several searches internally. Use the existing 15-minute
quote-cache policy to avoid repeatedly paying to retrieve the same quote.
Do not send a request when all instruments are cached or cooling down.

The prompt includes a server-generated reference per unique instrument,
symbol, expected currency and unit, and current UTC time. It does not include
user identities, asset IDs, holdings, balances, transactions, or API keys.
Treat instrument strings and retrieved page text as untrusted data. Give the
model no database, shell, or application-control tools.

Request a machine-readable result per reference: symbol, decimal price as a
string, currency, unit, quote timestamp with timezone, price kind, source URL,
and either success or an unavailable reason. Provider adapters normalize
OpenAI and Claude output into one internal result. A JSON response is only
an extraction format, not proof of correctness.

Validate the entire response shape and then individual results. Reject unknown
or duplicated references, symbol/currency/unit mismatches, non-finite or
nonpositive prices, missing sources, timestamps in the future, and quotes
older than seven calendar days. The seven-day cutoff is a proposed conservative
default for weekends and holidays, not an exchange-calendar implementation.
Reject conflicting duplicate results for their affected instruments; other
valid results may succeed. A structurally unparseable response fails the batch.
Require web-search evidence and associate each accepted source URL with the
provider's retrieved/cited sources. This reduces unsupported responses but
does not independently prove that a quoted market price is correct.

Persist source URL, price kind, and actual quote time with accepted prices.
Keep fetch time separate from quote time so caching does not depend on how
recently the exchange traded. Existing historical prices need no fabricated
source URL or price kind.

## Shared configuration and isolation

Add a persisted administrator flag. During migration, promote the earliest
existing user (created_at then ID) once; for fresh installations, setup creates
the first administrator. Future users default to non-administrator. User
registration, role-management screens, and account deletion are outside scope.
Enforce administrator access server-side and retain CSRF protection on all
configuration mutations and paid connection tests.

Store API keys encrypted at rest using an established authenticated-encryption
library and a server-side encryption key supplied outside SQLite. Do not write
keys into HTML, browser storage, logs, model prompts, or error messages. Missing
encryption configuration disables credential writes with an administrator-facing
setup message. Document backup and recovery requirements for the encryption key.

Shared caching contains public instrument quotes only. Cache identity includes
service, model/configuration revision, symbol, currency, and unit. Settings
changes invalidate cache/backoff through a configuration revision. Existing
non-AI provider cache behavior remains unchanged.

Bound outbound concurrency for this shared provider using a short-lived SQLite
lease with an expiry; no database transaction remains open across a network call.
When another request holds the lease, serve available cached prices and report
uncached assets as temporarily unavailable. Honor retry-after and use bounded
provider-wide backoff after rate limits. Do not immediately retry paid requests
or fall back to another service. Connection tests use the same lease/backoff.

## Runtime and failure behavior

Keep the existing request/response refresh workflow for the first version.
Run the AI batch before other slow provider work, with a total 60-second AI
deadline including connection, body read, and parsing. Reject oversized or
truncated responses without writing incomplete quotes. The builder may choose
transport and cancellation details but must enforce a total deadline, rather
than treating a socket read timeout as one.

Document and adjust Gunicorn/reverse-proxy timeout settings for the bounded
AI call plus existing provider work. Do not add an unbounded job or external
queue. Timed-out AI assets use the existing last-known-price/partial-failure
path. Disable the submit control during refresh and retain refresh idempotency.
Concurrent duplicate refresh submissions must not create duplicate snapshots
or repeat the paid AI batch after a successful refresh.

## Scope and verification

Touch Flask provider integration, refresh and price lookup, settings and asset
forms, authentication/administrator authorization, schema/migrations, translation
strings, deployment configuration, and documentation. Do not implement the
feature in the legacy Go rollback application.

Verify with mocked provider responses: multi-ticker batching, both service
adapters, web-search enablement, malformed/partial/mismatched/stale responses,
source evidence, Decimal parsing, timeout and rate-limit handling, shared cache
reuse, concurrency, idempotency, administrator-only credential management,
secret redaction, encryption failure, migration, and cross-user isolation.
Run existing relevant Flask tests and inspect the settings/asset forms in a
browser. Live API tests require configured credentials; never claim end-to-end
provider success based only on mocks.

## Documentation basis

- OpenAI API keys: https://developers.openai.com/api/docs/guides/api-keys
- OpenAI web search: https://developers.openai.com/api/docs/guides/tools-web-search
- Claude API keys: https://platform.claude.com/docs/en/get-api-key
- Claude web search: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool

The builder must recheck the actual API contract and select supported
web-search-capable models when implementing. No model name is frozen by this
design. Model selection, transport libraries, and precise markup are builder
choices within the security and behavior requirements above.
