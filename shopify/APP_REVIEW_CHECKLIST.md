# Crumbs Shopify App — Review Checklist (for a FUTURE public listing)

Status: NOT SUBMITTED (v0.1 local scaffold). This checklist mirrors the
Shopify app-review requirements so the pilot path stays listing-ready.

## 1. Auth & security

- [x] OAuth token exchange implemented (scaffold: `app.js`) — **HMAC request
      verification IMPLEMENTED** (active whenever `SHOPIFY_API_SECRET` is set;
      inert while no real app exists)
- [x] Per-install `state` param + validation — **IMPLEMENTED** (random
      per-install nonce bound to the exact shop, one-time use, 10-min TTL; the
      `/callback` host is trusted only after the `*.myshopify.com` allowlist
      regex AND the state binding match — P3 N2 hard gate)
- [ ] Access tokens stored encrypted, never in logs, rotated
- [ ] Scopes minimal: `read_orders`, `read_products` (no write scopes needed)
- [ ] App requests only data required for its function (order status for
      conversion finalization)

## 2. Data use & privacy

- [ ] Privacy policy URL (store listing requires one) — publish only after
      legal review (project R2 legal memo is a hard gate)
- [ ] Data-use disclosure: what the app reads (order status), what it stores
      (nothing customer-identifying), what it sends (conversion confirmations
      to the Crumbs ledger)
- [ ] ePrivacy consent on the storefront: the Crumbs SDK issues receipts only
      after consent; document the CMP hook for merchants
- [ ] No sale/sharing of customer data; no third-party data resale

## 3. Functionality (Shopify review criteria)

- [ ] App works end-to-end without Shopify staff involvement (pilot-scoped)
- [ ] No unsupported APIs; use documented REST/GraphQL endpoints
- [ ] App uninstall cleans up: remove webhooks, tokens, theme snippet
- [ ] Error handling: token expiry → re-auth flow; webhook failures → retry
- [ ] No deceptive behavior, no hidden fees (attribution fee disclosed up front)

## 4. Merchant UX

- [ ] Install flow explains exactly what the app does (attribution receipts,
      commissions to referring agents) before OAuth
- [ ] Settings page: merchant ID, ledger URL, consent wiring, payout rail
      selection (x402/USDC vs Stripe Connect — env-gated)
- [ ] Transparency: the merchant sees every attributed conversion + scheduled
      payout in-app before anything is paid

## 5. Legal / compliance (hard gates before listing)

- [ ] R2 legal memo (§E of p5b-wedge-spec) — affiliate-network structuring,
      FTC endorsement disclosure for agent commissions, COPPA posture
- [ ] Payouts flow through licensed rails only; app holds no float
- [ ] Terms of service for merchants and referring agents

## 6. OPSEC

- [ ] No project-identifying strings in public artifacts
- [ ] No real credentials in code or git history
- [ ] GitHub account exists + explicit operator go before any submission
