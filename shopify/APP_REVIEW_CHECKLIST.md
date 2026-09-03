# Crumbs Shopify App — review-readiness checklist

Working checklist for taking the custom-app scaffold (`app.js`) toward a
public Shopify app listing. Not submitted — v0.1 scaffold. Every unchecked box
is a real gap to close before a submission; the checklist mirrors Shopify's
app-review requirements so the pilot path stays listing-ready.

## 1. Auth & security

- [x] OAuth token exchange implemented (`app.js`) — install/callback HMAC
      verification active whenever `SHOPIFY_API_SECRET` is set
- [x] Per-install `state` param + validation — random per-install nonce bound
      to the exact shop, one-time use, 10-minute TTL; the `/callback` host is
      trusted only after the `*.myshopify.com` allowlist **and** the state
      binding match
- [ ] Access tokens stored encrypted, never in logs, rotated
- [ ] Scopes minimal: `read_orders`, `read_products` (no write scopes needed)
- [ ] App requests only data required for its function (order status for
      conversion finalization)

## 2. Data use & privacy

- [ ] Privacy policy URL (a store listing requires one)
- [ ] Data-use disclosure: what the app reads (order status), what it stores
      (nothing customer-identifying), what it sends (conversion confirmations
      to the Crumbs ledger the merchant configures)
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
      selection
- [ ] Transparency: the merchant sees every attributed conversion + scheduled
      payout in-app before anything is paid

## 5. Legal & compliance

- [ ] Legal review of the affiliate/commission structure (affiliate-network
      obligations, disclosure requirements for agent commissions, and
      child-privacy posture where applicable)
- [ ] Payouts flow through licensed rails only; the app holds no float
- [ ] Terms of service for merchants and referring agents

## 6. Release hygiene

- [ ] No real credentials in code or git history
- [ ] Reproducible build/deploy documented; the scaffold's in-memory token
      store replaced
- [ ] Tested against a real custom app before any submission
