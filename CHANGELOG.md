# Changelog

All notable changes to this project are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. The project
uses [Semantic Versioning](https://semver.org/) (0.x — public API is not yet
stable).

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
