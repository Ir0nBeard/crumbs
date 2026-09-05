# Crumbs Python x402 interop kit

Zero-dependency helpers for **server-side x402 sellers and workers** written
in Python. Mirrors the JS SDK's carrier surface (`getX402ReferralField`,
`getBuilderCode`) so a Python seller can participate in Crumbs attribution
without a browser or a Node runtime.

Pure stdlib (`json`, `re`, `urllib`) — import it anywhere; no pip install,
no framework.

```python
import sys
sys.path.insert(0, "/path/to/crumbs/sdk/python")
from crumbs_x402 import x402_referral_field, builder_code

# When the seller holds a Crumbs receipt for the paying agent, echo the
# referral on the x402 PAYMENT-RESPONSE and settle with the builder code:
referral = x402_referral_field(receipt_wire)   # {"referral": {"ref": <jid>, "provider": "crumbs"}}
code = builder_code()                          # "bc_crumbs"
```

## What's inside

| Helper | Purpose | JS SDK twin |
|---|---|---|
| `parse_receipt_wire(wire)` | lenient receipt parse (None on garbage) | — |
| `x402_referral_field(wire, *, refer="jid"\|"rid")` | x402 PAYMENT-RESPONSE referral field; None when no receipt held | `getX402ReferralField` |
| `builder_code()` / `is_valid_builder_code()` | ERC-8021 `bc_crumbs` service code | `getBuilderCode` |
| `is_did_pkh()` / `build_did_pkh()` | did:pkh (CAIP-10) agent anchors | — |
| `CrumbsLedgerClient` | thin `urllib` client: `request_journey`, `verify_receipt` | `requestJourney` / `verifyReceipt` |

Semantics are kept identical to the JS SDK (see
`sdk/test/sdk.test.mjs` "carriers" case and
`tests/server/test_python_x402_kit.py` for parity assertions):

- `x402_referral_field` returns `None` when no receipt is held or the wire is
  unparseable — never a half-built object.
- the journey id (`jrn_…`) is the default referral ref (the cross-merchant
  stitching key); `refer="rid"` opts into the single-receipt id.
- `CrumbsLedgerClient` requires an explicit `api_url` — there is deliberately
  **no default endpoint**.

## Walkthrough

See [`docs/X402_INTEROP.md`](../docs/X402_INTEROP.md) — the seller wiring
guide (did:pkh agent anchoring, referral emission, settlement proofs), with
an end-to-end test at `tests/server/test_did_anchor_interop.py`.

## Tests

```sh
python -m pytest tests/server/test_python_x402_kit.py tests/server/test_did_anchor_interop.py -q
```
