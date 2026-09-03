# Crumbs — Quickstart

Run the full stack from a fresh clone: ledger server (SQLite), the end-to-end
demo, and the JS SDK. No hosted service is required — you run the ledger
yourself.

**Prerequisites:** Python 3.10+ and Node.js >= 18.

## 1. Get the code

```sh
git clone https://github.com/Ir0nBeard/crumbs.git
cd crumbs
```

Layout: `server/` (FastAPI ledger), `sdk/` (JS SDK), `mcp/` (MCP server),
`ext/` (Chrome extension), `wp/crumbs-attribution/` (WordPress plugin),
`shopify/` (Shopify app scaffold). New here? Start with
[ARCHITECTURE.md](ARCHITECTURE.md).

## 2. Install

```sh
python -m venv .venv
.venv/bin/pip install -r server/requirements.txt
```

The SDK has zero dependencies and no build step — nothing to install for it.

## 3. Run the test suite (should be all green)

```sh
# ledger server + MCP server (pytest; SQLite in-memory)
.venv/bin/python -m pytest tests/ -q

# JS SDK (node:test — a tiny in-process fake ledger stands in for the server)
(cd sdk && node --test test/sdk.test.mjs)
```

## 4. End-to-end demo (no server needed)

`dev_demo` seeds a merchant, issues a signed journey receipt, stamps a
conversion, finalizes it through the signed order webhook, and schedules a
payout record — printing each step:

```sh
cd server && ../.venv/bin/python -m app.dev_demo
```

You should see the journey id, the receipt size (`< 1 KB`), the conversion id
moving `pending -> finalized`, and a scheduled payout row. The demo uses a
throwaway SQLite file (`server/crumbs_demo.db`) — safe to delete.

## 5. Run the live ledger + talk to it over HTTP

Start the server (terminal 1). Generate a stable signing key first so receipts
survive restarts:

```sh
cd server
export CRUMBS_SIGNING_KEYS=1:$(../.venv/bin/python -c "from app.signing import generate_key_hex; print(generate_key_hex())")
../.venv/bin/python -m uvicorn app.main:app --port 8000
```

The startup log prints the address the server is bound to. In a second
terminal, set that address and check health:

```sh
cd server
export CRUMBS_URL=<paste-the-address-from-the-log>   # printed by uvicorn at startup
curl -s "$CRUMBS_URL/v1/health"
# -> {"status":"ok", ...}
```

Merchants are provisioned by the operator (there is deliberately no
merchant-signup endpoint yet — see ARCHITECTURE.md). Seed one into the same
SQLite file the server created (run from `server/`):

```sh
../.venv/bin/python - <<'PY'
from app.db.session import get_session_factory
from app.seed import seed_merchant
m = seed_merchant(get_session_factory()())
print("merchant_id:", m.mid)
PY
```

Copy the printed `merchant_id` and issue a consent-gated journey receipt,
capturing the response:

```sh
curl -s -X POST "$CRUMBS_URL/v1/journeys" \
  -H 'content-type: application/json' \
  -d '{"merchant_id":"<paste-merchant-id>","surface":"browser","consent":{"basis":"explicit","ref":"quickstart"}}' \
  -o journey.json
cat journey.json   # contains the signed receipt + rid/journey_id/exp
```

Verify it back (`POST` — the signed receipt travels in the request body):

```sh
curl -s -X POST "$CRUMBS_URL/v1/verify" -H 'content-type: application/json' \
  -d "$(../.venv/bin/python -c 'import json; print(json.dumps({"receipt": json.load(open("journey.json"))["receipt"]}))')"
# -> {"valid":true, ...}
```

## 6. JS SDK snippet

Point the SDK at the same ledger. In Node:

```js
import { createCrumbs } from "./sdk/crumbs.mjs";

const crumbs = createCrumbs({
  apiUrl: process.env.CRUMBS_URL, // required — the ledger base URL (no default)
  merchantId: process.env.CRUMBS_MERCHANT_ID,
});

// consent first — no receipt is ever issued before this
await crumbs.setConsent("granted");

// issue a journey receipt (throws CONSENT_REQUIRED if consent is missing)
const journey = await crumbs.requestJourney();

// verify the fresh receipt against the ledger
const status = await crumbs.verifyReceipt(journey.receipt); // {valid: true, ...}

// stamp the conversion at checkout (idempotent on receipt rid + order id)
const conversion = await crumbs.stampConversion({
  orderId: "ord_1001",
  cartValueMinorUnits: 9900, // $99.00 in minor units — always integers
  currency: "USD",
});
```

Run it from the repo root (new terminal; point `CRUMBS_URL` at the same
ledger address as above):

```sh
export CRUMBS_URL=<paste-the-address-from-the-log>
export CRUMBS_MERCHANT_ID=<paste-merchant-id>
node --input-type=module - <<'JS'
import { createCrumbs } from "./sdk/crumbs.mjs";
const crumbs = createCrumbs({
  apiUrl: process.env.CRUMBS_URL,
  merchantId: process.env.CRUMBS_MERCHANT_ID,
});
await crumbs.setConsent("granted");
const journey = await crumbs.requestJourney();
console.log("verify fresh receipt:", (await crumbs.verifyReceipt(journey.receipt)).valid);
const conversion = await crumbs.stampConversion({
  orderId: "ord_1001", cartValueMinorUnits: 9900, currency: "USD",
});
console.log("conversion:", conversion.conversion_id, conversion.status, "| awaiting:", conversion.awaiting);
JS
```

You should see `verify fresh receipt: true` and a conversion in `pending`
state awaiting the merchant's signed order webhook. (Note: verifying a receipt
*after* it has been spent by a conversion reports `valid:false,
REPLAYED_NONCE` — that is the nonce-replay protection working, not an error.)

## What's next

- [ARCHITECTURE.md](ARCHITECTURE.md) — how the pieces fit and the threat model
- [docs/ATTRIBUTION_PROTOCOL.md](docs/ATTRIBUTION_PROTOCOL.md) — receipt format, signing, carriers, lifecycle
- [SECURITY.md](SECURITY.md) — reporting a vulnerability
- [CHANGELOG.md](CHANGELOG.md) — what exists, what is stubbed
