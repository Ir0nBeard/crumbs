# Crumbs — Architecture

Crumbs is a **neutral attribution and referral-settlement layer for agentic
commerce**. It answers one question: *when an AI agent helped a buyer reach a
merchant and a sale happened, who gets credit — and how is that credit made
provable and payable?*

This document is human-written on purpose: no ticket ids, no audit shorthand —
just how the system actually fits together, followed by a plain-language threat
model.

## The problem

A browser tab and an AI agent look alike to a merchant's checkout. When an
agent researched options, carried a session, and steered a buyer to checkout,
the merchant cannot tell — and cannot pay the agent a referral commission
without a trustworthy record of the journey. Crumbs provides that record as a
**signed attribution receipt** that survives the journey, is verified against a
server-side ledger at conversion time, and feeds payout scheduling.

## Receipt lifecycle

```
   journey                     carriers                    conversion
┌──────────┐   signed receipt   ┌─────────────────────┐   ┌──────────────────────┐
│  agent   │ ─────────────────► │ cookie / header /   │ ─►│  merchant checkout   │
│ (SDK/MCP)│    consent-gated   │ x402 referral field │   │  stamps conversion   │
└──────────┘                    └─────────────────────┘   └──────────┬───────────┘
     │                                  ▲                            │
     │ POST /v1/journeys                │ carries receipt            │ POST /v1/conversions
     ▼                                  │ to the next hop            │ (idempotent)
┌──────────┐                            │                            ▼
│  LEDGER  │ ◄──────────────────────────┴───────────────────┐   ┌──────────────────┐
│  (this   │                                                │   │  pending (await  │
│   repo)  │ ◄──────────────────────────────────────────────┤   │  order webhook)  │
└──────────┘                                                │   └──────────────────┘
     │                                                      │            │
     │ ledger verifies: signature · nonce · expiry ·        │            │ POST /v1/webhooks/orders
     │ journey budgets · revocation · self-referral         │            │ (signed by merchant)
     │                                                      │            ▼
     │                                               ┌──────┴───┐  finalized / cancelled / refunded
     │                                               │ payout   │
     └──────────────────────────────────────────────►│ records  │  (scheduling only — no float)
                                                     └──────────┘
```

The steps in words:

1. **Journey start.** With a recorded consent basis, the merchant (or an API
   agent) calls `POST /v1/journeys`. The ledger issues a signed receipt bound
   to the journey: agent id, merchant id, commission terms snapshot, surface
   (browser/api/chat), nonce, issued/expiry times. **Nothing sensitive** — no
   order id, no cart value, no payout details — rides on the receipt. It is an
   *entitlement marker*, not stored value.
2. **Carriage.** The receipt travels with the buyer/agent on one of four
   carriers, all interchangeable:
   - first-party cookie (`__Host-crumbs_j`, HttpOnly — set server-side by the
     merchant; the SDK reads only a short-TTL JS mirror),
   - HTTP header (`X-Crumbs-Journey`) for API-level agents,
   - the x402 `PAYMENT-RESPONSE` referral field (`getX402ReferralField()` in
     the SDK emits `{"referral": {"ref": <jid>, "provider": "crumbs"}}`),
   - an ERC-8021 builder code (`bc_crumbs` — `getBuilderCode()`; a
     facilitator appends it to settlement calldata as an `s` service code).
3. **Ledger verify.** At checkout, the merchant stamps the conversion
   (`POST /v1/conversions`, idempotent on receipt rid + order id). The ledger
   re-verifies everything server-side — signature, nonce replay, expiry,
   revocation, journey budgets (max conversions / distinct merchants / cart
   value), and self-referral controls — then holds the conversion as
   `pending`.
4. **Merchant confirmation.** The merchant confirms final order state with a
   signed webhook (`POST /v1/webhooks/orders`, HMAC over the raw body, replay
   window, `conversion_id` required, monotonic
   `pending → finalized|cancelled|refunded` transitions, cart value
   cross-checked against the confirmed order). Only a `finalized` conversion is
   payable.
5. **Payout scheduling + proof recording.** `POST /v1/payouts/batch` turns
   finalized conversions into payout records (owner share, network take,
   splits). **Records only**: Crumbs never holds or moves funds — settlement
   executes on licensed rails (x402/USDC facilitator or Stripe Connect).
   `POST /v1/payouts/{pid}/settlement` (admin) then records the rail's
   settlement proof: with the settlement calldata it parses the ERC-8021
   Schema 2 builder-code suffix and requires it to carry `bc_crumbs`
   (on-chain proof); without calldata the record is a labelled rail
   attestation. See docs/ATTRIBUTION_PROTOCOL.md §4.5.

## Pieces

- **`server/`** — FastAPI ledger. SQLite for local dev/tests; Postgres DDL in
  `server/migrations/`. Append-only `audit_events` table records every
  state change.
- **`sdk/`** — zero-dependency JS SDK (ES module + IIFE bundle, one source of
  truth in `sdk/src/`). Consent gate, journey/conversion/verify calls, carrier
  helpers, WebMCP tool registration, heuristic agent-signal detection.
- **`mcp/`** — stdio MCP server exposing the same three operations
  (`request_journey`, `verify_receipt`, `declare_conversion`) to MCP clients.
- **`ext/`** — Chrome MV3 viewer: per-site opt-in, minimal permissions, no
  background network calls.
- **`wp/crumbs-attribution/`** — WordPress plugin (GPL-2.0-or-later). Issues
  journeys server-side so the receipt cookie is HttpOnly; SDK is vendored — no
  third-party remote code.
- **`shopify/`** — custom-app pilot scaffold (OAuth token exchange,
  zero-dependency Node).
- **`tests/`** — server, MCP, and SDK suites (see QUICKSTART.md).

## Consent model

Receipt issuance is gated on consent (ePrivacy Art 5(3)): the ledger records a
consent basis (`explicit`, `gpp`, `tcf`, `88b`) at journey time and refuses
issuance without one; the SDK and the WordPress plugin both refuse to call the
ledger before consent is granted. CMP/consent-provider *re-validation* is a
stub — v0.1 trusts the recorded signal.

## Threat model (plain language)

What an attacker might try, and what the design does about it:

- **Forge a receipt.** Receipts are HMAC-SHA256-signed with a server key
  (rotatable via key ids); forging one requires the key. Keys live in env /
  secret manager — never in the repo.
- **Steal a receipt and reuse it (theft).** A receipt is bearer-ish, so the
  durable copy is an HttpOnly cookie the browser JS cannot read, and
  localStorage never stores the signed wire — only an id marker. Cookie TTL is
  capped (30 days) and the browser mirror is short-TTL.
- **Replay the same receipt on many orders.** Every receipt carries a nonce
  the ledger marks used at conversion; a second use is rejected. Failed
  attempts do not burn the nonce — only accepted conversions do.
- **Blow through journey limits.** Journey budgets (5 conversions / 10
  merchants / $2k cart by default) are enforced in one atomic conditional
  update, so racing requests cannot all pass a check-then-increment gap.
- **Self-referral farming.** An agent steering buyers to its own merchant is
  the core fraud vector for referral payouts. The ledger checks agent↔merchant
  ownership and caps conversions per agent+merchant per time window. These are
  v0.1 controls, not guarantees.
- **Conversion padding.** A merchant inflating cart value is cross-checked
  against the confirmed order value in the signed finalization webhook
  (tolerance in basis points); mismatches are flagged on the conversion.
- **Webhook forgery/replay.** Order webhooks are HMAC-signed over the raw body
  (constant-time compare), carry a timestamp inside a tolerance window, and
  require an unambiguous `conversion_id`. The known dev secret is refused
  outside local SQLite databases.
- **Downgrade / ambiguity attacks.** Receipt parsing rejects type confusion
  (bools in int fields, floats), enforces JCS canonical form so there is one
  valid byte representation, and pins field types/ranges.
- **Interception.** Verify endpoints POST receipts in the body — never in a
  query string or log line. TLS is expected in front of the ledger in any real
  deployment.
- **Operational fail-open.** When Redis (used-nonce + rate-limit store) is
  configured but unreachable, the server refuses to start rather than silently
  degrading to memory — nonce dedup must not reset on restart. Payout
  scheduling defaults to disabled (fail-closed) and revocation endpoints are
  disabled until an admin token is configured.

**Honest limits (v0.1):** no per-merchant keyed tokens yet (one optional shared
merchant key), consent-provider re-validation is a stub, Redis/Postgres are
provisioned but not battle-tested in production, and the payout rails are not
live. See [CHANGELOG.md](CHANGELOG.md) for the full stub list. Treat v0.1 as a
well-tested reference implementation, not a hardened production service.

## Extending

- Add a merchant program → `server/app/db/models.py` + `server/migrations/`.
- New carrier → SDK carrier helpers (`sdk/src/crumbs-core.cjs`) + this doc.
- Protocol/format changes → bump `v` in `server/app/core/receipt.py`, keep the
  old version verifiable, and document the migration in
  [docs/ATTRIBUTION_PROTOCOL.md](docs/ATTRIBUTION_PROTOCOL.md).
