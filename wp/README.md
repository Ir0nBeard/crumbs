# Crumbs WordPress plugin (v0.1)

`crumbs-attribution/` — consent-native agent-journey attribution for
WordPress/WooCommerce merchants. Records agent-referred journeys as signed
attribution receipts and stamps attributed conversions at checkout, so
referring AI agents (and their owners) can be credited and paid via licensed
payout rails.

**License: GPL-2.0-or-later** — the WordPress.org plugin directory requires
all hosted plugin code to be GPL-compatible, so this plugin directory is
distributed under GPL-2.0-or-later (see the plugin header, `readme.txt`, and
the repo's [LICENSE-GPL-2.0-or-later](../LICENSE-GPL-2.0-or-later)). The
rest of the Crumbs repo is MIT (see [LICENSE-MIT](../LICENSE-MIT)); the
MIT-licensed SDK ships *vendored* inside this plugin with its license notice
intact — MIT is GPL-compatible, and no code is loaded from third-party
servers.

**Status:** v0.1 scaffold. Not yet listed in the WordPress.org directory.

## How it complies with the plugin-directory rules

| wordpress.org guideline | How this plugin complies |
|---|---|
| GPL-compatible | GPL-2.0-or-later header + `readme.txt` |
| No executable code via third-party systems | SDK is **vendored** in `vendor/crumbs-sdk/crumbs.iife.js` (a copy of `sdk/dist/crumbs.iife.js`) — no CDN script tag, no remote fetch |
| No tracking without consent | `crumbs_consent_status()` gates every issuance; the SDK refuses pre-consent calls |
| Human-readable code | Plain PHP + unminified JS (the IIFE bundle is a 1:1 wrapper of the readable core) |

## How it works

1. Merchant configures **merchant ID** and **ledger API URL** (Settings →
   Crumbs Attribution). The ledger URL is required — there is no built-in
   default endpoint.
2. On the storefront, the SDK loads (consent-gated). If the CMP signals
   consent, the merchant server requests a journey from the ledger and sets the
   **HttpOnly** `__Host-crumbs_j` receipt cookie (+ short-TTL `crumbs_jr` JS
   mirror).
3. At checkout the SDK stamps the conversion (idempotent POST with
   `Idempotency-Key <rid>:<oid>`); the ledger verifies signature/nonce/budget/
   revocation and holds the conversion until the merchant's signed order
   webhook confirms `finalized`.
4. Payouts are *scheduled* by the ledger — settled on licensed rails (x402/
   USDC facilitator or Stripe Connect; env-gated stub in v0.1).

## Consent wiring (stub)

The plugin ships a `crumbs_consent_status` filter. Wire your CMP
(GPP/TCF/Consent Mode v2) to return `granted`/`denied`:

```php
add_filter( 'crumbs_consent_status', function ( $status ) {
    // e.g. return my_cmp()->get_status(); // 'granted'|'denied'|'unknown'
    return $status;
} );
```

## Keep the vendored SDK in sync

The bundle is generated from `sdk/src/crumbs-core.cjs`; after changing it,
rebuild and copy (from the repo root):

```sh
node sdk/scripts/build-iife.mjs
cp sdk/dist/crumbs.iife.js wp/crumbs-attribution/vendor/crumbs-sdk/crumbs.iife.js
```

## Not in v0.1 (stubs)

* WooCommerce checkout-form WebMCP annotation (filter hook exists, annotation off)
* wp.org submission, SVN hosting, i18n pot files
* Consent-basis persistence (the consent *record* lives in the ledger)
* Secret-manager storage for the optional API key (v0.1 dev scaffold stores it
  as a plaintext option and warns in the admin UI)
