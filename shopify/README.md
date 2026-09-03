# Crumbs for Shopify — custom-app pilot scaffold (v0.1)

Local-only scaffold. **No app registered, no store listing** (OPSEC: gated on
domain + GitHub + explicit go). This is the *custom distribution* shape from
shopify.dev/docs/apps/distribution: single-store/Plus pilots, no app-store
approval, no Shopify Billing — the right pilot path for the first merchants.

## Run

```sh
export SHOPIFY_API_KEY=... SHOPIFY_API_SECRET=...   # from a real custom app (future)
export SHOPIFY_REDIRECT_URI=http://localhost:3000/callback
node app.js
# open http://localhost:3000/install?shop=<your-shop>.myshopify.com
```

## What is implemented (v0.1)

* `/install` — validates the shop param, redirects to Shopify OAuth authorize
* `/callback` — exchanges the auth code for an access token (POST
  `/admin/oauth/access_token`), stores it in-memory
* `/health` — liveness

Zero dependencies: Node >= 18 global fetch + `node:http`.

## What is STUBBED / next

* **HMAC verification** of install/callback requests (Shopify signs every
  request with your API secret) — TODO before any real pilot
* **State/CSRF** — per-install random state param + validation
* **Secure token persistence + rotation** — in-memory Map today
* **Webhooks** — register `orders/updated` (and `app/uninstalled`) so the
  ledger's merchant order webhook can be fed for conversion finalization
* **Theme SDK injection** — consent-gated script tag for the storefront
  (the Crumbs SDK, vendored or SRI-pinned — never raw remote code)
* **App review checklist** — see `APP_REVIEW_CHECKLIST.md` for the public
  listing path (future, approval-gated)

## Data posture

The app only reads order *status* data needed to confirm conversions
(finalized/cancelled/refunded). No customer PII is stored. Consent for
storefront receipt cookies remains the merchant's ePrivacy obligation (the SDK
is consent-gated).
