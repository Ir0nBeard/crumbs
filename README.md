# Crumbs — agent-journey attribution ledger (MVP v0.1)

**Status: LOCAL MVP BUILD (2026-08-29). Nothing published, registered, or
uploaded.** Spec source of truth: `~/Projects/cookie-p5/` (p5b-wedge-spec.md,
p5-synthesis.md §8, p5b-distribution.md). Code lives here, per the project
folder rule.

Crumbs is a **neutral agent-journey attribution + referral-settlement layer**:
a signed-attribution receipt (~340 B, HMAC-SHA256, nonce, 30-day TTL) carried
on four interchangeable carriers (first-party cookie, HTTP header, x402
PAYMENT-RESPONSE referral field, ERC-8021 builder code) and verified against a
server-side ledger with journey budgets, revocation, nonce replay protection,
self-referral velocity checks, and merchant order-webhook confirmation.

## What is BUILT (v0.1)

| Piece | What it does | Location |
|---|---|---|
| **Ledger server** | FastAPI: consent-gated `POST /v1/journeys`, idempotent `POST /v1/conversions`, `POST /v1/verify` (canonical; `GET` kept for back-compat), `POST /v1/webhooks/orders`, `POST /v1/payouts/batch`, `POST /v1/admin/revoke`, `GET /v1/health` | `server/` |
| **Receipt core** | JCS (RFC 8785) canonicalization, HMAC-SHA256 truncated-32B base64url signing, ULID ids, 16-byte nonces, kid rotation | `server/app/core/` |
| **Ledger DB** | SQLAlchemy models + Postgres DDL (`migrations/0001_init.sql`): receipts, journeys, agents, agent_owners, merchants, merchant_programs, conversions, splits, payouts, disputes, revoked_*, used_nonces, audit_events (append-only) | `server/app/db/`, `server/migrations/` |
| **Fraud controls** | Journey budgets (5 conversions / 10 merchants / $2k cart), nonce replay rejection, revocation (receipt/journey/agent), self-referral ownership + velocity caps, conversion-padding cart cross-check, surface mismatch | `server/app/services/ledger.py` |
| **JS SDK** | `requestJourney()`, `stampConversion()` (Idempotency-Key rid:oid), `verifyReceipt()`, consent gate, cookie/header/x402 carriers, WebMCP `crumbs_conversion` tool (imperative + declarative), agent-signal detection. ES module + IIFE bundle, zero deps, no build step | `sdk/` |
| **MCP server** | Stdio JSON-RPC MCP: `request_journey`, `verify_receipt`, `declare_conversion` (thin wrappers over the API) | `mcp/` |
| **Chrome MV3 extension** | Minimal permissions (`storage` only), optional per-site host access, content script reads receipt state on opted-in sites, popup viewer + privacy disclosure | `ext/` |
| **WordPress plugin** | GPL header, SDK VENDORED (no third-party remote exec), consent-gated server-side journey issuance (HttpOnly `__Host-crumbs_j`), settings page, uninstall cleanup | `wp/crumbs-attribution/` |
| **Shopify app** | Custom-app pilot scaffold: OAuth token-exchange flow (zero-dep Node), app-review checklist | `shopify/` |

## Tests (all green)

```sh
# server + mcp (pytest) — 69 tests
cd /home/kali/Desktop/EXO-SYNERGY/Crumbs
.venv/bin/python -m pytest tests/ -q

# sdk (node --test, no deps) — 11 tests
cd sdk && node --test test/sdk.test.mjs
```

Run instructions, env config, and the honest stub list: **docs/BUILD.md**.

## Quick start

```sh
cd /home/kali/Desktop/EXO-SYNERGY/Crumbs
python3.13 -m venv .venv                 # NOTE: use python3.13, NOT python3 (Kali 3.14 pitfall)
.venv/bin/pip install -r server/requirements.txt
cd server && ../.venv/bin/python -m app.dev_demo   # end-to-end demo (receipt → conversion → payout)
```

## Stubs (honest list — see docs/BUILD.md for details)

* Live x402/CDP settlement + Stripe Connect — **stubbed** (payout *scheduling*
  records only; no float is ever held)
* GPP/TCF/Consent Mode v2 provider integration — **stubbed** (consent is
  recorded, provider re-validation is a 501 stub)
* WebMCP live registration — feature-detected and implemented in the SDK but
  untestable in real browsers (adoption ≈ 0%)
* x402 PAYMENT-RESPONSE `referral` field + ERC-8021 `bc_crumbs` builder-code
  settlement calldata — **stubbed shapes** in the SDK (`getX402ReferralField()`,
  `getBuilderCode()`); live payment-path integration is a later phase
* All store/registry listings (Chrome Web Store, wp.org, Shopify App Store,
  MCP Registry, npm) — **NOT STARTED / NOT PUBLISHED** (OPSEC)
* Per-merchant keyed auth, CMP re-validation, Redis-provisioned prod — TODO

## OPSEC

Local-only repo (git, no remotes). No secrets in files — `.env.example` has
placeholders only; signing keys are generated at runtime when unset. No
project-identifying strings beyond the product's own name. GitHub account does
not exist yet; nothing is pushed, published, or registered.
