# Crumbs for Shopify — custom-app pilot scaffold (v0.1)

A zero-dependency Node scaffold implementing the Shopify **OAuth token-exchange
flow** for a *custom app* (single-store/Plus pilots, per
shopify.dev/docs/apps/distribution — no app-store approval, no Shopify
Billing). The app confirms orders for attribution: it reads order status so the
Crumbs ledger can finalize attributed conversions.

**Status:** v0.1 scaffold. No app is registered yet and there is no store
listing. When you're ready, `APP_REVIEW_CHECKLIST.md` is a neutral
app-review-readiness checklist for the public listing path.

## Run (local development)

```sh
export SHOPIFY_API_KEY=... SHOPIFY_API_SECRET=...   # from a custom app you register
export SHOPIFY_SCOPES=read_orders,read_products
node app.js
```

The console prints the local install URL and the port (`PORT`, default 3000).
Open the `/install?shop=<your-shop>.myshopify.com` URL from a browser to walk
the OAuth flow. In production the app must be served over HTTPS and
`SHOPIFY_REDIRECT_URI` set to your public callback URL (Shopify requires it to
match exactly what you configured in the app settings).

## What is implemented (v0.1)

* `/install` — validates the shop param against the `*.myshopify.com`
  allowlist, verifies the install HMAC when the app secret is configured, and
  redirects to Shopify OAuth authorize with a per-install `state`
* `/callback` — validates the shop (allowlist + exact-match state binding) and
  the callback HMAC, then exchanges the auth code for an access token
  (`POST /admin/oauth/access_token`)
* `/health` — liveness

Secrets are read from the environment only — never committed. The access token
is held in memory (see stubs).

## What is stubbed / next

* **Secure token persistence + rotation** — in-memory Map today; production
  needs encrypted storage keyed by shop
* **Webhooks** — register `orders/updated` (and `app/uninstalled`) so the
  ledger's merchant order webhook can be fed for conversion finalization
* **Theme SDK injection** — consent-gated script tag for the storefront (the
  Crumbs SDK, vendored or SRI-pinned — never raw remote code)
* **App review readiness** — see `APP_REVIEW_CHECKLIST.md`

## Data posture

The app only reads order *status* data needed to confirm conversions
(finalized/cancelled/refunded). No customer PII is stored. Consent for
storefront receipt cookies remains the merchant's ePrivacy obligation (the SDK
is consent-gated).
