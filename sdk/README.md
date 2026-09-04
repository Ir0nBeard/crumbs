# Crumbs JS SDK (v0.1)

Consent-native agent-journey attribution SDK for [Crumbs](https://github.com/Ir0nBeard/crumbs).
Zero dependencies, no build step. Licensed **MIT** (this package stays MIT even
though the WordPress plugin in the same repository is GPL-2.0-or-later — they
are separate distribution units; see the repo's LICENSE files).

**Status:** v0.1 — not yet published to npm. Until then, use the module/bundle
directly from the repository (below).

## Install

```sh
# from the repo (not yet on npm):
import { createCrumbs } from "./crumbs.mjs";   # Node / bundlers
# or browser script tag:
# <script src="dist/crumbs.iife.js"></script>  -> window.Crumbs.createCrumbs
```

## Quick start

```js
import { createCrumbs } from "./crumbs.mjs";

const crumbs = createCrumbs({
  // Required — the base URL of the Crumbs ledger instance you run.
  // There is no default endpoint: configure it explicitly.
  apiUrl: process.env.CRUMBS_LEDGER_URL,
  merchantId: "m_...",
  surface: "browser",
});

// 1. consent FIRST (ePrivacy Art 5(3)) — no receipt before this
await crumbs.setConsent("granted");       // or wire consentProvider()

// 2. issue a journey receipt
const journey = await crumbs.requestJourney();

// 3. at checkout, stamp the conversion (idempotent)
const conversion = await crumbs.stampConversion({
  orderId: "ord_123",
  cartValueMinorUnits: 9900,             // $99.00 in cents — always minor units
  currency: "USD",
});

// 4. verify any receipt
const status = await crumbs.verifyReceipt(journey.receipt);
```

Calling any network method without an `apiUrl` throws a clear
`crumbs: apiUrl is required ...` error — nothing silently targets a default
host.

## Carriers

* Cookie: the merchant SERVER sets `__Host-crumbs_j` (Secure+HttpOnly+SameSite=Lax).
  The SDK reads the short-TTL JS mirror `crumbs_jr` via `getCookieValue()`.
* Header (API agents): `X-Crumbs-Journey: <receipt>` via `getHeaderValue()`.
* x402 PAYMENT-RESPONSE referral: `getX402ReferralField()` →
  `{referral:{ref:jid, provider:"crumbs"}}` (journey id by default;
  `{refer: "rid"}` opts into the receipt id; null without a receipt).
* ERC-8021 builder code: `getBuilderCode()` → `bc_crumbs`
  (`/^[a-z0-9_]{1,32}$/`); a facilitator appends it to settlement calldata
  as an `s` service code and the ledger verifies the on-chain suffix when
  recording a settlement proof.

## WebMCP

```js
await crumbs.registerWebmcpTool();     // registers "crumbs_conversion" if
                                       // document.modelContext ?? navigator.modelContext
                                       // is available (feature-detected; no-op otherwise)
```

Declarative path (zero JS) — annotate the checkout form:

```html
<form webmcp toolname="crumbs_conversion"
      data-order-id-field="order_id"
      data-value-field="cart_value_minor_units"
      data-currency-field="currency">
  <input name="order_id" ...>
  <input name="cart_value_minor_units" ...>
</form>
```

`bindDeclarativeForms()` binds these and stamps conversions on submit.

## Agent signals (heuristic — never a security boundary)

`crumbs.detectAgentSignals()` → `{webmcp, userAgentHits, agentLike}`.

## Tests

```sh
node --test test/sdk.test.mjs
```

## Files

* `src/crumbs-core.cjs` — single source of truth (UMD factory)
* `crumbs.mjs` — ES module entry
* `dist/crumbs.iife.js` — browser bundle (regenerate: `node scripts/build-iife.mjs`)
* `test/sdk.test.mjs` — node:test suite (fake ledger server, no deps)
