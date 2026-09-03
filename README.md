# Crumbs

Open-source, consent-native **agent-journey attribution and referral settlement**
for agentic commerce: when an AI agent refers a buyer to a merchant and the
buyer checks out, Crumbs records it with a signed attribution receipt and
schedules the commission to the referring agent — on licensed payout rails.

> **v0.1 — early development.** The receipt format (v1) and the core ledger are
> implemented and tested; settlement rails, consent-provider integrations, and
> store/registry listings are stubs (honest list in
> [CHANGELOG.md](CHANGELOG.md) and [ARCHITECTURE.md](ARCHITECTURE.md)). There
> is **no hosted Crumbs service yet** — you run your own ledger instance and
> point integrations at it with an explicit `apiUrl`/`CRUMBS_MCP_API_URL`.

![tests](https://img.shields.io/badge/tests-81%20green-brightgreen)
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
