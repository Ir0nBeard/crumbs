# Crumbs WordPress plugin (scaffold, v0.1)

`crumbs-attribution/` — consent-native agent-journey attribution for
WordPress/WooCommerce merchants. **Not published to wordpress.org** (OPSEC:
listing is gated on domain + GitHub + explicit go).

## Distribution constraints honored (p5b-distribution.md §A.4.3)

| wordpress.org guideline | How this plugin complies |
|---|---|
| GPL-compatible | GPL-2.0-or-later header + readme.txt |
| No executable code via third-party systems | SDK is **vendored** in `vendor/crumbs-sdk/crumbs.iife.js` (a copy of `sdk/dist/crumbs.iife.js`) — no CDN script tag, no remote fetch |
| No tracking without consent | `crumbs_consent_status()` gates every issuance; SDK refuses pre-consent calls |
| Human-readable code | Plain PHP + unminified JS (the IIFE bundle is a 1:1 wrapper of the readable core) |

## How it works

1. Merchant configures merchant ID + ledger URL (Settings → Crumbs Attribution).
2. On the storefront, the SDK loads (consent-gated). If the CMP signals
   consent, the merchant server requests a journey from the ledger and sets the
   **HttpOnly** `__Host-crumbs_j` receipt cookie (+ short-TTL `crumbs_jr` JS
   mirror) — the real deployment shape from the spec (A.4).
3. At checkout the SDK stamps the conversion (idempotent POST with
   `Idempotency-Key <rid>:<oid>`); the ledger verifies signature/nonce/budget/
   revocation and holds the conversion until the merchant's signed order
   webhook confirms `finalized`.
4. Payouts are *scheduled* by the ledger — settled on licensed rails (x402/
   USDC facilitator or Stripe Connect; env-gated STUB in v0.1).

## Consent wiring (stub)

The plugin ships with a `crumbs_consent_status` filter. Wire your CMP
(GPP/TCF/Consent Mode v2) to return `granted`/`denied`:

```php
add_filter( 'crumbs_consent_status', function ( $status ) {
    // e.g. return my_cmp()->get_status(); // 'granted'|'denied'|'unknown'
    return $status;
} );
```

## Keep the vendored SDK in sync

```sh
cp sdk/dist/crumbs.iife.js wp/crumbs-attribution/vendor/crumbs-sdk/crumbs.iife.js
```

## NOT in v0.1 (stubs)

* WooCommerce checkout-form WebMCP annotation (filter hook exists, annotation off)
* wp.org submission, SVN hosting, i18n pot files
* Consent-basis persistence (the consent *record* lives in the ledger)
