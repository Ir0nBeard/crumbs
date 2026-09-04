# Changelog

All notable changes to this project are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. The project
uses [Semantic Versioning](https://semver.org/) (0.x — public API is not yet
stable).

## [Unreleased]

### Added

- **Headless browser-behaviour tests for the Chrome extension + a grant-flow
  correctness fix**:
  - New `tests/ext/` suite runs the extension's MV3 logic headlessly with a
    faithful `chrome.*`/DOM mock harness (`node --test`, no browser or
    display required): the per-site opt-in grant flow (single combined
    `scripting` + host permission prompt, dynamic content-script registration,
    idempotent re-enable), the popup's enable/disable/verify state machine
    driven end-to-end through the service worker, and the content script's
    page-state collection (JS mirror cookie `crumbs_jr`, SDK localStorage
    receipts, agent-signal heuristics).
  - Manifest conformance checks lock the privacy posture in place: MV3 with
    only `storage` + `activeTab` at install, `scripting` and host access
    optional, no `cookies` permission, no static `content_scripts` (the viewer
    registers scripts dynamically per opted-in site), and no remote code.
  - The content-script checks pin the merchant-cookie behaviour: the HttpOnly
    `__Host-crumbs_j` receipt cookie stays server-side — never read from
    extension code and never via `chrome.cookies`; the viewer reads only the
    JS-visible mirror, exactly like the SDK.
  - Grant-flow fix in `ext/background.js`: a site used to be recorded in
    `enabled_sites` BEFORE the permission prompt resolved, so a denied prompt
    left the site listed as enabled with no host permission and no content
    script behind it (the popup would offer "Disable on this site" for a site
    that was never actually viewable). The optional `scripting` permission is
    now requested together with the host permission in one prompt, and the
    site is persisted only after the grant. Disabling the last enabled site
    additionally drops the now-unused `scripting` permission.
- **Live Postgres/Redis test coverage + conversion concurrency hardening**:
  - New `tests/server/test_live_infra.py` runs the canonical migration SQL
    (`server/migrations/0001_init.sql`, `0002_merchant_tokens.sql`) against a
    real Postgres server — clean apply, idempotent re-run, and table/column
    parity with the ORM models — then exercises the service layer
    (journey -> receipt -> conversion -> verify) on Postgres and verifies the
    Redis nonce/rate-limit stores (replay rejection, fixed windows, TTL).
    Concurrent-conversion tests prove budget caps never overshoot, the
    distinct-merchant counter never drifts, and the self-referral velocity
    window holds under multi-connection races. Gated by
    `CRUMBS_LIVE_TEST_DB_URL` / `CRUMBS_LIVE_TEST_REDIS_URL` and skipped in
    the default SQLite CI run; the Redis fail-closed startup test runs
    everywhere.
  - Conversion recording is serialized per journey (`SELECT ... FOR UPDATE`
    on Postgres; compiled away on SQLite) so two concurrent conversions of a
    first-time merchant cannot both compute `merchant_delta=1` and drift
    `journeys.merchants_used` above the true distinct count. Postgres
    advisory transaction locks keyed on (agent, merchant) additionally close
    the velocity window across different journeys of the same agent.
  - A failed conversion attempt now rolls its transaction back before
    re-raising: the journey row lock and (agent, merchant) advisory lock no
    longer outlive the error inside a still-open transaction, which could
    deadlock concurrent conversions on Postgres.
  - `RedisRateLimiter` uses an atomic Lua INCR+EXPIRE script instead of
    `EXPIRE ... NX` (Redis >= 7 only), so fixed-window rate limiting works on
    Redis >= 6.
  - `server/requirements.txt` (and the hash-pinned lock) gained
    `psycopg2-binary` — the Postgres driver for `postgresql://` database
    URLs, matching the production target declared by `server/migrations/`.
- **Secret-manager indirection for merchant webhook secrets** (credential
  material out of the ledger database):
  - New `server/app/core/secrets.py`: a merchant's `webhook_secret` column
    now holds a **reference** — `secretref:env:<NAME>` — resolved from the
    service environment at verification time. The material never touches the
    database; unresolvable references fail closed (`401 BAD_SIGNATURE`), and
    any value under the `secretref:` scheme that is not a well-formed env
    reference is never treated as a literal.
  - New admin endpoints (admin-token gated): `POST`/`GET`/`DELETE
    /v1/admin/merchants/{mid}/webhook-secret` manage the reference. Writes
    are fail-fast: an env reference whose variable is unset in the process is
    refused (`422 UNRESOLVABLE_REF`); responses are masked and never contain
    the material. Set/clear events are appended to `audit_events`.
  - New `CRUMBS_ENFORCE_SECRET_REFS` strict mode: on a non-SQLite database a
    literal value resolves to nothing (fail closed) and the admin API refuses
    to store one (`422 SECRET_REF_REQUIRED`). Default `false` preserves
    back-compatibility while deployments migrate. The known dev-default
    literal keeps its absolute guard outside SQLite.
  - WordPress plugin: the merchant key is read from the
    `CRUMBS_MERCHANT_API_KEY` constant in `wp-config.php` when defined (the
    key never enters `wp_options`); the settings UI shows the source and no
    longer echoes a stored key back into the page; saving with the constant
    defined clears the legacy option row.
  - Docs: `docs/SECRET_MANAGEMENT.md` — inventory of where every credential
    lives, the HMAC webhook-secret rotation runbook (fail-closed sequence
    with rollback), and token/signing-key rotation notes.
  - Dependency manifest: `server/requirements.lock` — exact pins with SHA-256
    hashes (`uv pip compile --generate-hashes`) for reproducible installs.
- **Per-merchant keyed tokens + scoped CORS** (conversion credentials are
  now merchant-scoped and revocable):
  - `POST /v1/admin/merchants/{mid}/tokens` (admin) issues a `cmk_`
    per-merchant token — the plaintext is returned ONCE, only its SHA-256
    hash is stored at rest. Tokens are bound to exactly one merchant;
    conversions authenticated with one are scoped to that merchant's
    receipts (`TOKEN_MERCHANT_MISMATCH` 403 otherwise).
  - `GET /v1/admin/merchants/{mid}/tokens` lists token metadata (never
    hashes); `POST /v1/admin/tokens/{token_id}/revoke` revokes — failing
    conversions immediately (`TOKEN_REVOKED` 403). Issuance/revocation
    are appended to `audit_events`.
  - **Scoped CORS**: a token may carry an `origins` allowlist (JSON list
    of https origins) — browser requests (Origin header) from other
    origins are rejected (`ORIGIN_NOT_ALLOWED` 403) while server-to-server
    calls are unaffected. The app-level browser transport gate is
    `CRUMBS_CORS_ORIGINS` (comma-separated; empty = no cross-origin
    browser access, fail closed).
  - `/v1/conversions` now enforces the body `merchant_id` equals the
    receipt's merchant (`MERCHANT_MISMATCH` 422) instead of silently
    ignoring the field.
  - The legacy shared `CRUMBS_MERCHANT_API_KEY` remains accepted for
    back-compatibility (deprecated); `CRUMBS_REQUIRE_MERCHANT_TOKENS=true`
    makes per-merchant tokens mandatory and rejects the shared key.
  - New table `merchant_tokens` (migration `0002_merchant_tokens.sql`,
    alembic revision `0002`).
- **Settlement-proof recording on the x402 rail** (the ledger half of
  real payout attribution):
  - `POST /v1/payouts/{pid}/settlement` (admin-token gated) records an
    executed rail settlement against a `scheduled` payout: `tx_hash`
    (0x-64-hex EVM), optional `rail_ref` (facilitator reference), optional
    `referral_ref` (the `rct_`/`jrn_` id echoed by an x402 PAYMENT-RESPONSE
    referral), `asset`, `network`. Splits roll to `settled`; the transition
    is appended to `audit_events` (`payout_settled`).
  - **ERC-8021 Schema 2 on-chain verification** (`server/app/core/
    buildercode.py`, zero-dependency CBOR codec): when settlement
    `calldata` is supplied, the ledger parses its builder-code suffix
    (marker `80218021…`, schema id `0x02`, big-endian CBOR length, CBOR map
    of `a`/`w`/`s` codes, each matching `^[a-z0-9_]{1,32}$`) and REQUIRES
    it to carry `bc_crumbs` — `proof_mode: "onchain"`. Without calldata the
    record is a labelled rail attestation (`proof_mode: "attestation"`),
    never presented as an on-chain proof. Money still never moves through
    the ledger: the record is proof of an off-ledger rail settlement.
  - `GET /v1/payouts/{pid}` (admin) returns the proof envelope: payout
    record + splits + proof fields.
- **SDK carrier hardening** — `getX402ReferralField()` now returns null
  without a receipt (never a half-built object) and supports
  `{refer: "rid"}` (receipt id) alongside the journey-id default;
  `getBuilderCode()` documented against the ERC-8021 code format. IIFE
  bundle + WordPress vendored copy rebuilt.

### Changed

- **Consent verifier** — the v0.1 501 "CMP re-validation is a
  STUB" fail-closed block in `ledger.issue_journey` is replaced by a real
  server-side verifier (`server/app/services/consent.py`):
  - `tcf`: `ref` must be an IAB Europe TCF EU v2 TC string — decoded and
    checked server-side (version 2, sane 36-bit `created`, freshness within
    `CRUMBS_MAX_CONSENT_SIGNAL_AGE_SECONDS` (400-day default), alphabetic
    consent language, non-empty purposeConsents, and purpose-1 consent
    required for storage-based attribution —
    `CRUMBS_CONSENT_TCF_REQUIRE_PURPOSE1`, default true).
  - `gpp`: `ref` must be a spec-shaped GPP string (websafe-base64
    "~"-joined header + sections; header Type=3/Version=1 => leading "DB").
    v1 validates shape/decodability; per-section purpose semantics are
    deferred to the CMP endpoint (documented limitation).
  - `88b`: `ref` is REQUIRED as the merchant-side consent/attestation audit
    key (no wire standard exists — EU Data Omnibus still in trilogue).
  - `explicit`: unchanged record-mode (the issuance audit row is the record).
  - `CRUMBS_CMP_VERIFY_URL` (previously a 501 stub) now performs real CMP
    re-validation: the locally-verified signal is POSTed as JSON
    `{"basis","ref","surface"}` and must answer 2xx `{"valid": true}`; any
    other outcome fails closed (`CONSENT_REFUSED` 403 / `CMP_UNREACHABLE`
    503). Unset => local structural checks are the whole gate (the old
    "trust the client signal" behaviour is gone).
  - Journey responses now include `consent.verified` (`record|local|cmp`);
    `journey_issued` audit payloads record `consent_mode` + `consent_checks`.
  - `consent_ref` column widened 128 -> 2048 (real TC/GPP strings exceed 128
    chars; the Postgres DDL was already TEXT, SQLAlchemy model now matches).

## [0.1.0] — 2026-08-29

First release. A consent-native agent-journey attribution ledger: signed
attribution receipts (v1) issued per journey, carried on cookie/header/x402
carriers, verified server-side at conversion, confirmed by the merchant's
signed order webhook, and turned into payout *records* (scheduling only — the
ledger never holds funds).

### Added

- **Ledger server** (`server/`, FastAPI + SQLAlchemy): `POST /v1/journeys`
  (consent-gated issuance), `POST /v1/conversions` (idempotent on receipt rid +
  order id), `POST /v1/verify` (canonical; receipt in the body) plus a `GET`
  variant kept for diagnostics, `POST /v1/webhooks/orders` (signed merchant
  confirmation), `POST /v1/payouts/batch`, `POST /v1/admin/revoke`,
  `GET /v1/health`.
- **Receipt core** (`server/app/core/`): JCS (RFC 8785) canonicalization,
  HMAC-SHA256 signing (truncated 32-byte, base64url), ULID ids, 16-byte
  nonces, key-id rotation, strict type/range validation. Wire format v1 — see
  [docs/ATTRIBUTION_PROTOCOL.md](docs/ATTRIBUTION_PROTOCOL.md).
- **Fraud controls**: journey budgets (5 conversions / 10 merchants / $2k cart
  defaults), atomic budget updates, nonce replay rejection, revocation
  (receipt/journey/agent), self-referral ownership + velocity caps,
  conversion-padding cross-check, surface-mismatch rejection.
- **JS SDK** (`sdk/`): `requestJourney`, `stampConversion`, `verifyReceipt`,
  consent gate + `consentProvider` hook, carrier helpers, WebMCP
  `crumbs_conversion` tool (imperative + declarative), heuristic agent-signal
  detection. ES module + IIFE bundle, zero dependencies.
- **MCP server** (`mcp/`): stdio JSON-RPC `request_journey`,
  `verify_receipt`, `declare_conversion`.
- **Chrome MV3 extension** (`ext/`): minimal permissions (`storage` only),
  per-site opt-in, receipt-state viewer, ids-only verify button.
- **WordPress plugin** (`wp/crumbs-attribution/`, GPL-2.0-or-later): settings
  page, consent-gated server-side journey issuance with HttpOnly
  `__Host-crumbs_j` cookie, vendored SDK, uninstall cleanup.
- **Shopify app scaffold** (`shopify/`): zero-dependency OAuth token-exchange
  flow (custom-app shape).
- **Tests**: server + MCP pytest suite, SDK node:test suite, shared
  `scripts/run_all_tests.sh`.

### Honest stub list (not yet implemented in v0.1)

- **Payout settlement rails** — x402/CDP and Stripe Connect are *stubbed*:
  `payouts/batch` writes scheduling records only. Settlement is gated off by
  default (`CRUMBS_PAYOUTS_ENABLED=false`). No float is ever held.
- **Consent-provider re-validation** — GPP/TCF/Consent Mode v2 provider
  integration is a stub; the ledger records the consent signal the client
  sends and does not yet re-validate it with a CMP.
- **Per-merchant keyed auth** — one optional shared merchant key
  (`CRUMBS_MERCHANT_API_KEY`) gates `/v1/conversions`; per-merchant tokens are
  a post-v0.1 item.
- **WebMCP live registration** — implemented and feature-detected in the SDK,
  but browser adoption is ~0%, so it is untestable in the wild.
- **x402 referral field / ERC-8021 builder-code settlement** — carrier shapes
  exist in the SDK (`getX402ReferralField()`, `getBuilderCode()`); live
  payment-path integration is a later phase.
- **Production deployment** — Postgres + Redis paths are provisioned
  (migrations, fail-closed Redis guard) but not battle-tested in production;
  no secret manager integration yet (webhook secrets and the optional merchant
  key live in config/DB for local dev, with the dev default rejected outside
  SQLite).
- **Registry/listing presence** — nothing is published yet: no npm package,
  no WordPress.org/Chrome Web Store/Shopify App Store/MCP-directory listings,
  no hosted Crumbs service. Integrations point at a ledger instance you run
  (explicit `apiUrl` — there is intentionally no built-in default endpoint).
