# Wiring an x402 seller into Crumbs

How a server-side x402 seller (or a worker agent selling paid API calls over
x402) participates in Crumbs attribution. The pattern is implemented in the
**Python x402 interop kit** (`sdk/python/crumbs_x402.py`, zero dependencies —
mirrors the JS SDK's carrier surface for server-side Python) and is fully
exercised by `tests/server/test_did_anchor_interop.py`.

Crumbs attribution has two halves:

1. **A journey** — a consent-gated receipt that records that an agent is
   active against a merchant program (docs/ATTRIBUTION_PROTOCOL.md §4).
2. **A settlement proof** — when the agent pays the seller over x402, the
   seller records the rail settlement with the attribution the agent carried:
   the x402 `PAYMENT-RESPONSE` referral (`referral_ref`) and the ERC-8021
   builder code (`bc_crumbs`) appended to the settlement calldata.

## 1. Anchor the agent to an on-chain identity (did:pkh)

The ledger can bind a receipt's agent to a wallet-style identity so
cross-merchant journeys of one agent stitch together. Pass `agent_did`
(CAIP-10 `did:pkh:<namespace>:<reference>:<account>`) when issuing the
journey:

```bash
curl -s -X POST https://<ledger>/v1/journeys \
  -H 'content-type: application/json' \
  -d '{
    "merchant_id": "m_...",
    "surface": "api",
    "consent": {"basis": "explicit", "ref": "consent-record-1"},
    "agent_did": "did:pkh:eip155:8453:0xAbCdef0123456789abcdef0123456789abcdef01"
  }'
```

The response carries the internal `agent_id` (the wire receipt always uses the
`ag_` form) plus the echo `agent_did`:

```json
{
  "receipt": "{...JCS-canonical signed receipt...}",
  "rid": "rct_...",
  "journey_id": "jrn_...",
  "agent_id": "ag_...",
  "agent_did": "did:pkh:eip155:8453:0xAbCdef0123456789abcdef0123456789abcdef01",
  "exp": 1790583495
}
```

Subsequent journeys for the **same** did resolve to the **same** agent id —
that is the cross-merchant stitching key. `POST /v1/verify` echoes
`agent_did` when the receipt's agent is anchored, so a verifier can prove the
receipt belongs to one on-chain identity. Malformed dids are rejected with
`422 INVALID_AGENT_DID`; rebinding an existing agent to a different did is
rejected with `409 AGENT_DID_CONFLICT`. Anchoring is additive — journeys
issued without a did keep working unchanged, and an unanchored agent can be
anchored later (backfill).

## 2. Emit the x402 referral field

When the seller holds a Crumbs receipt for the paying agent, the x402
`PAYMENT-RESPONSE` should echo the Crumbs referral so downstream attribution
can bind the settlement:

```python
from crumbs_x402 import x402_referral_field, builder_code

referral = x402_referral_field(receipt_wire)   # jid by default
# {"referral": {"ref": "jrn_...", "provider": "crumbs"}}
# pass refer="rid" to echo the single receipt id instead

code = builder_code()                          # "bc_crumbs"
```

`x402_referral_field` returns `None` when the seller holds no receipt — never
a half-built object. The builder code matches `/^[a-z0-9_]{1,32}$/` and is
what the facilitator appends to settlement calldata as an ERC-8021 Schema 2
`s` service code.

## 3. Record the settlement proof

Once the rail settles, record the proof against the scheduled payout
(admin-token gated). With `calldata`, the ledger parses the ERC-8021 suffix
and requires it to carry `bc_crumbs` — an **on-chain** proof. The
`referral_ref` is the journey/receipt id echoed from the x402
PAYMENT-RESPONSE referral:

```bash
curl -s -X POST https://<ledger>/v1/payouts/<pid>/settlement \
  -H 'content-type: application/json' \
  -H 'X-Crumbs-Admin-Token: <token>' \
  -d '{
    "tx_hash": "0x...64 hex...",
    "calldata": "0xdeadbeef...80218021...",   # suffix verified to carry bc_crumbs
    "builder_code": "bc_crumbs",
    "referral_ref": "jrn_...",
    "rail_ref": "facilitator-ref",
    "asset": "USDC",
    "network": "eip155:8453"
  }'
```

Response: `{"status": "settled", "proof_mode": "onchain", ...}`. Without
`calldata` the record is an **attestation** (`proof_mode: "attestation"`) —
never presented as an on-chain proof. `GET /v1/payouts/<pid>` returns the
full proof envelope; every transition is appended to `audit_events`.

## Reference implementation

The full loop — anchored journey → conversion → payout → bc_crumbs
settlement with the echoed referral → proof envelope — is exercised
end-to-end in `tests/server/test_did_anchor_interop.py`
(`test_referral_settlement_end_to_end`). The Python kit's helpers are
covered by `tests/server/test_python_x402_kit.py`, with parity assertions
against the JS SDK's carrier tests.
