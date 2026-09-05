# Crumbs

Open-source, consent-native **agent-journey attribution and referral settlement**
for agentic commerce: when an AI agent refers a buyer to a merchant and the
buyer checks out, Crumbs records it with a signed attribution receipt and
schedules the commission to the referring agent — on licensed payout rails.

> **v0.1 — early development.** The receipt format (v1), the consent
> verifier, per-merchant keying, secret-reference management, settlement-proof
> recording, and the core ledger are implemented and tested; live payout-rail
> settlement and store/registry listings are not yet shipped (honest list in
> [CHANGELOG.md](CHANGELOG.md) and [ARCHITECTURE.md](ARCHITECTURE.md)). There
> is **no hosted Crumbs service yet** — you run your own ledger instance and
> point integrations at it with an explicit `apiUrl`/`CRUMBS_MCP_API_URL`.

![tests](https://img.shields.io/badge/tests-164%20green-brightgreen)
![license](https://img.shields.io/badge/license-MIT%20%2F%20GPL--2.0--or--later-blue)
![version](https://img.shields.io/badge/version-0.1.0-lightgrey)

## What's in the box

| Piece | What it does | Location |
|---|---|---|
| **Ledger server** | FastAPI: consent-gated `POST /v1/journeys`, idempotent `POST /v1/conversions`, `POST /v1/verify` (canonical; `GET` kept for back-compat), `POST /v1/webhooks/orders`, `POST /v1/payouts/batch`, `POST /v1/admin/revoke`, `GET /v1/health` | `server/` |
| **Receipt core** | JCS (RFC 8785) canonicalization, HMAC-SHA256 truncated-32B base64url signing, ULID ids, 16-byte nonces, key-id rotation | `server/app/core/` |
| **Ledger DB** | SQLAlchemy models + Postgres DDL (`server/migrations/0001_init.sql`): receipts, journeys, agents, agent_owners, merchants, merchant_programs, conversions, splits, payouts, disputes, revoked_*, used_nonces, audit_events (append-only) | `server/app/db/` |
| **Fraud controls** | Journey budgets (5 conversions / 10 merchants / $2k cart), nonce replay rejection, revocation (receipt/journey/agent), self-referral ownership + velocity caps, conversion-padding cart cross-check, surface mismatch | `server/app/services/ledger.py` |
| **JS SDK** | `requestJourney()`, `stampConversion()` (Idempotency-Key rid:oid), `verifyReceipt()`, consent gate, cookie/header/x402 carriers, WebMCP `crumbs_conversion` tool, agent-signal detection. ES module + IIFE bundle, zero dependencies | `sdk/` |
| **MCP server** | Stdio JSON-RPC MCP: `request_journey`, `verify_receipt`, `declare_conversion` (thin wrappers over the ledger API) | `mcp/` |
| **Chrome MV3 extension** | Minimal permissions (`storage` only), optional per-site host access, receipt-state viewer + verify button, privacy disclosure | `ext/` |
| **WordPress plugin** | GPL-2.0-or-later, SDK vendored (no third-party remote code), consent-gated server-side journey issuance (HttpOnly `__Host-crumbs_j` cookie), settings page, uninstall cleanup | `wp/crumbs-attribution/` |
| **Shopify app** | Custom-app pilot scaffold: OAuth token-exchange flow (zero-dependency Node), app-review checklist | `shopify/` |

## Quick start (60 seconds)

```sh
# 1. server: create a venv and install
python -m venv .venv
.venv/bin/pip install -r server/requirements.txt

# 2. run the end-to-end demo (receipt -> conversion -> payout scheduling)
cd server && ../.venv/bin/python -m app.dev_demo

# 3. SDK smoke test (Node >= 18, zero dependencies)
cd ../sdk && node --test test/sdk.test.mjs
```

Full fresh-clone walkthrough (dev server + SQLite + SDK snippet): **[QUICKSTART.md](QUICKSTART.md)**.

## Docs

- [QUICKSTART.md](QUICKSTART.md) — run the server, ledger, and SDK from a fresh clone
- [ARCHITECTURE.md](ARCHITECTURE.md) — receipt lifecycle, carriers, ledger, consent; threat-model summary
- [docs/ATTRIBUTION_PROTOCOL.md](docs/ATTRIBUTION_PROTOCOL.md) — the signed-receipt format, end to end
- [docs/LISTINGS.md](docs/LISTINGS.md) — distribution channels and publish runbooks (npm, stores, MCP, CDN)
- [SECURITY.md](SECURITY.md) — reporting vulnerabilities
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- [CHANGELOG.md](CHANGELOG.md) — release history (Keep a Changelog)

## Testing

```sh
# server + mcp (pytest)
.venv/bin/python -m pytest tests/ -q

# sdk (node --test, no deps)
(cd sdk && node --test test/sdk.test.mjs)
```

The SQLite suite is the CI default. Live Postgres/Redis tests
(`tests/server/test_live_infra.py`) additionally verify the migration DDL,
the service layer, the Redis stores, and concurrent budget/velocity
enforcement against real infrastructure. Enable them by pointing the two
variables at a dedicated test database / Redis instance (the tests drop and
recreate the target database's `public` schema, so never point them at data
you need):

```sh
CRUMBS_LIVE_TEST_DB_URL=postgresql://user:pass@127.0.0.1:5432/crumbs_test \
CRUMBS_LIVE_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
.venv/bin/python -m pytest tests/server/test_live_infra.py -q
```

## License

**Dual-licensed by component**, see [LICENSE-MIT](LICENSE-MIT) and
[LICENSE-GPL-2.0-or-later](LICENSE-GPL-2.0-or-later):

- The **core** (`server/`, `sdk/`, `mcp/`, `ext/`, `shopify/`, `scripts/`, `tests/`) is
  **MIT**. The npm-published SDK is a separate distribution unit and **stays MIT**.
- The **WordPress plugin** (`wp/crumbs-attribution/`) is **GPL-2.0-or-later**, as
  required by the WordPress.org plugin directory (plugin header and
  `readme.txt` declare it). The MIT-licensed SDK ships *vendored* inside the
  plugin with its license notice intact — MIT is GPL-compatible, and no code is
  loaded from third-party servers.

Copyright (c) 2026 Crumbs contributors.
