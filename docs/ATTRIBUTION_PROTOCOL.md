# Crumbs Attribution Protocol — signed receipts v1

This document is the public description of the Crumbs signed-attribution
receipt: the wire format, the signing scheme, the lifecycle, and the
verification semantics. It is the canonical reference that code comments and
integrations point at.

Status: **stable at v1** within the 0.x development series. Format changes
bump the `v` field; the ledger keeps verifying older versions while they are
within their expiry window.

## 1. What a receipt is (and is not)

A receipt is an **entitlement marker**, not stored value. It records that an
agent journey existed against a merchant program at a point in time, with the
commission terms snapshotted. It deliberately carries **no** order id, no cart
value, no payout details, and no customer data — those are bound at conversion
time, server-side, and live only in the ledger.

A forged, stolen, or stale receipt is therefore low-value on its own: it cannot
name an order or an amount. The ledger is the system of record; the receipt is
the provable pointer the journey carries.

## 2. Wire format

A receipt is a single flat JSON object in **JCS (RFC 8785) canonical form** —
one canonical byte representation, which is what makes signatures stable and
duplicates detectable. Typical size is ~300 bytes (well under the 1 KB cookie
design rule).

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Format version — currently `1` |
| `rid` | string | Receipt id: `rct_` + ULID (26 chars) |
| `jid` | string | Journey id: `jrn_` + ULID |
| `aid` | string | Agent id: `ag_` + base32 |
| `mid` | string | Merchant id: `m_` + base32 |
| `oid` | string | Order id — **empty at issuance**; stamped at conversion |
| `cv` | int | Cart value in minor units — `0` at issuance; stamped at conversion |
| `cur` | string | ISO 4217 currency (3 letters) |
| `crb` | int | Commission rate in basis points (0–10000), snapshotted at issuance |
| `ntb` | int | Network take in basis points (0–10000), snapshotted at issuance |
| `sf` | string | Surface: `browser` \| `api` \| `chat` |
| `nc` | string | Nonce: 16 random bytes, base64url-unpadded (22 chars) |
| `iat` | int | Issued-at, unix seconds |
| `exp` | int | Expiry, unix seconds (default TTL 30 days) |
| `kid` | int | HMAC key id (rotation) |
| `sig` | string | Signature (below), appended as the **last** key |

Type rules are strict by design: the integer fields accept only JSON integers
(bools and floats are rejected), strings are length/prefix-checked, `crb`/`ntb`
are range-checked, and the object must arrive in exact canonical form.

## 3. Signing

- **Algorithm:** HMAC-SHA256 over the JCS-canonical payload with the `sig`
  field removed.
- **Truncation:** the first 32 bytes of the digest, base64url-unpadded → a
  43-character `sig`.
- **Keying:** `kid` selects the signing key (comma-separated `kid:hex` pairs
  in server config). Key rotation = add a new kid, stop issuing with the old
  one, retire the old key only after max TTL + nonce grace has elapsed.
- **Verification:** constant-time compare (`hmac.compare_digest`); an unknown
  or malformed `kid` fails closed.

Because `sig` sorts last under JCS, the canonical form of the full object and
the canonical form of the unsigned payload agree on every byte of the signed
region — so the "signed string" is unambiguous.

## 4. Lifecycle

1. **Issuance** — `POST /v1/journeys` with `{merchant_id, surface, consent}`
   where `consent` carries a basis (`explicit` | `gpp` | `tcf` | `88b`).
   The ledger **refuses issuance without a recorded consent basis**. Response:
   `201` with the signed receipt string, `rid`, `journey_id`, `agent_id`, `exp`.
2. **Carriage** — the receipt is carried on one of four interchangeable
   carriers:
   - **Cookie** — the merchant server sets `__Host-crumbs_j` (Secure, HttpOnly,
     SameSite=Lax); JS can read only a short-TTL mirror (`crumbs_jr`).
   - **Header** — `X-Crumbs-Journey: <receipt>` for API-level agents.
   - **x402 referral field** (later phase) — `getX402ReferralField()` produces
     `{"referral": {"ref": <journey_id>, "provider": "crumbs"}}`.
   - **ERC-8021 builder code** (later phase) — `bc_crumbs`
     (matches `/^[a-z0-9_]{1,32}$/`).
3. **Conversion** — at checkout, `POST /v1/conversions` with the receipt, the
   merchant id, `order_id`, `cart_value_minor_units` (always minor units,
   integer), and `currency`, plus an `Idempotency-Key: <rid>:<order_id>`
   header. The ledger re-verifies everything (below) and returns
   `201 {conversion_id, status: "pending", ...}`; a safe retry with the same
   idempotency key returns the existing conversion (`200`, `idempotent: true`).
4. **Merchant confirmation** — `POST /v1/webhooks/orders` signed by the
   merchant (`X-Crumbs-Signature: hex HMAC-SHA256` over the **raw body**;
   body includes `t` unix-seconds inside the replay window, a required
   `conversion_id`, an `order_status` of `finalized|cancelled|refunded`, and an
   optional `final_cart_value_minor_units`). Transitions are monotonic:
   `pending → {finalized, cancelled, refunded}`, `finalized → {refunded}`,
   terminal states stay terminal. The confirmed cart value is cross-checked
   against the stamped value (conversion-padding control).
5. **Payout** — finalized conversions become payout *records* via
   `POST /v1/payouts/batch` (admin-token gated). Settlement itself runs on
   licensed rails outside this ledger (see ARCHITECTURE.md — v0.1 stub).

## 5. Verification semantics

`POST /v1/verify` (canonical — the receipt travels in the request **body**,
never a URL query string) checks, in order:

1. **Structure** — parseable, JCS-canonical, correct types/ranges.
2. **Signature** — valid under the key named by `kid`.
3. **Revocation** — the receipt, its journey, or its agent is not revoked.
4. **Nonce** — the receipt nonce is not already consumed.
5. **Budgets** — the journey still has conversion/merchant/cart headroom
   (checked atomically at conversion time).
6. **Expiry** — `now < exp` (+ nonce-grace for replay detection).

Outcome: `{"valid": true, ...}` (with journey counters attached when the
journey row exists — callers can inspect a spent receipt's budget state) or
`{"valid": false, "reason": "<code>"}` with codes such as `MALFORMED_RECEIPT`,
`BAD_SIGNATURE`, `UNKNOWN_KID`, `EXPIRED`, `REVOKED_RECEIPT`,
`REVOKED_JOURNEY`, `REVOKED_AGENT`, `REPLAYED_NONCE`. Conversion-time rejections
use their own codes (`BUDGET_EXCEEDED`, `SELF_REFERRAL`, `VELOCITY_EXCEEDED`,
`SURFACE_MISMATCH`, `CART_VALUE_MISMATCH`). A `GET /v1/verify` variant exists
for diagnostics only.

## 6. Consent

No receipt is issued without a recorded consent basis. The ledger stores the
basis/ref from the issuance call; the SDK and the WordPress plugin both refuse
to issue pre-consent. Re-validation against a CMP (GPP/TCF/Consent Mode v2) is
a v0.1 stub — see [CHANGELOG.md](../CHANGELOG.md).

## 7. Reference implementation

The canonical implementation is the receipt core in `server/app/core/receipt.py`
and its tests in `tests/`. The SDK (`sdk/src/crumbs-core.cjs`) implements the
client half (consent gate, carriers, stamping). Behavior differences between
this document and the code are bugs — report them per [SECURITY.md](../SECURITY.md).
