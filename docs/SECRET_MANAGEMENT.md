# Secret Management & Key Rotation

How credential material is stored, referenced, and rotated across the Crumbs
deployment. The governing rule: **credential material never lives in the
ledger database, in the plugin options table, or in the repository.** Databases
and repos leak; a secret stored in them is a secret already lost.

| Material | Where it lives | Managed via |
|---|---|---|
| Merchant webhook signing secrets | service environment, referenced from the DB | `secretref:env:` refs + admin API |
| Merchant conversion tokens (`cmk_`) | plaintext shown once; SHA-256 hash at rest | `/v1/admin/merchants/{mid}/tokens` |
| Receipt signing keys (`kid`) | service environment | `CRUMBS_SIGNING_KEYS` |
| Admin token | service environment | `CRUMBS_ADMIN_TOKEN` |
| WordPress merchant key | `wp-config.php` constant (recommended) | `CRUMBS_MERCHANT_API_KEY` |
| Shopify app credentials | Shopify partner dashboard + service env | `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET` |
| Shopify install tokens | never persisted (v0.1 in-memory) | re-obtained via OAuth on restart |

---

## 1. Merchant webhook secrets (`secretref` indirection)

The merchants table stores a **reference**, never the material
(`server/app/core/secrets.py`):

```
secretref:env:<NAME>
```

`<NAME>` is an environment variable in the service process. At verification
time (`server/app/services/webhooks.py`) the reference is resolved to the live
value, used for one HMAC comparison, and discarded. The database never sees
the secret; a database read yields only the reference.

Literals (the v0.1 dev behaviour) are accepted **only** for the local SQLite
development database. Production deployments should set
`CRUMBS_ENFORCE_SECRET_REFS=true`: on any non-SQLite database a literal then
resolves to nothing, so verification **fails closed** (`401 BAD_SIGNATURE`)
instead of authenticating with database-resident material. The known dev
default (`dev-webhook-secret-do-not-use-in-prod`) is rejected outright outside
SQLite, with or without strict mode.

### Provisioning a merchant webhook secret

1. Generate the material: `openssl rand -hex 32` (or your vault's generator).
2. Put it in the service environment under a merchant-specific name, e.g.
   `CRUMBS_WEBHOOK_SECRET_M_<mid>` (systemd `Environment=` file, `.env`, or a
   vault agent that injects env at start).
3. Restart the service so the variable is live.
4. Store the reference with the admin API:

   ```bash
   curl -X POST https://<ledger>/v1/admin/merchants/<mid>/webhook-secret \
     -H "X-Crumbs-Admin-Token: <token>" \
     -H "Content-Type: application/json" \
     -d '{"value": "secretref:env:CRUMBS_WEBHOOK_SECRET_M_<mid>"}'
   ```

   The endpoint refuses (422) a reference whose variable is not set in the
   current process — a reference that cannot resolve would silently fail every
   webhook, so it is better to fail at write time.
5. Confirm with `GET /v1/admin/merchants/{mid}/webhook-secret` →
   `{"configured": true, "mode": "env-ref", "resolvable": true}`. Responses
   never contain the material.

### Rotating a merchant webhook secret (HMAC key rotation)

Single-active-secret model: the merchant signs with exactly the secret the
reference resolves to, so rotation is a coordinated flip. The safe sequence
fails closed at every step (a wrong signature is a `401`, never a false
accept):

1. **Provision**: generate the new secret; put it in the environment under a
   NEW variable name (`CRUMBS_WEBHOOK_SECRET_M_<mid>_v2`); restart the service.
   (New name, not overwrite — instant rollback if the merchant misconfigures.)
2. **Merchant side first**: the merchant switches their signer to the new
   secret. From this moment their old-signature deliveries will `401` — the
   intended fail-closed window. Do this during low traffic.
3. **Flip the reference**: `POST /v1/admin/merchants/{mid}/webhook-secret`
   with `{"value": "secretref:env:CRUMBS_WEBHOOK_SECRET_M_<mid>_v2"}`.
4. **Verify**: ask the merchant to deliver a test webhook; confirm a `200`
   and the expected conversion state transition in the audit log.
5. **Clean up**: once the merchant confirms steady state (observe at least one
   full finalized cycle), remove the old variable and restart. Record the
   rotation in the audit trail via the append-only `merchant_webhook_secret_set`
   events.

Emergency rollback: flip the reference back to the `_v1` variable — no
re-keying needed, the old material is still in the environment until step 5.

To disable a merchant's webhooks entirely:
`DELETE /v1/admin/merchants/{mid}/webhook-secret` — verification then fails
closed until a new secret is configured.

## 2. Merchant conversion tokens (`cmk_`)

Per-merchant tokens are the recommended conversion credential
(`server/app/services/merchant_auth.py`). Only the SHA-256 hash is stored; the
`cmk_` plaintext is returned once at issuance. Rotating a token = issue a new
one, hand it to the merchant, revoke the old:

```bash
curl -X POST https://<ledger>/v1/admin/merchants/<mid>/tokens -H "X-Crumbs-Admin-Token: <token>"
curl -X POST https://<ledger>/v1/admin/tokens/<token_id>/revoke -H "X-Crumbs-Admin-Token: <token>"
```

Revocation is immediate (`403 TOKEN_REVOKED`). Tokens may carry an origin
allowlist to restrict browser use (scoped CORS). The legacy shared key
(`CRUMBS_MERCHANT_API_KEY`) is deprecated; set `CRUMBS_REQUIRE_MERCHANT_TOKENS=true`
to reject it.

## 3. Receipt signing keys (`CRUMBS_SIGNING_KEYS`)

Comma-separated `kid:hex` pairs; `kid=1` is the issuance default. Rotate by
adding a new kid and moving issuance to it; keep old kids until every
outstanding receipt's TTL + grace has passed, then remove them. Receipts are
self-verifying against the key id they carry (`kid` field), so old keys must
remain resolvable for the lifetime of the receipts they signed (see
`server/app/signing.py`).

## 4. WordPress merchant key

The plugin reads the key from the `CRUMBS_MERCHANT_API_KEY` constant in
`wp-config.php` (never from the database) when it is defined:

```php
define( 'CRUMBS_MERCHANT_API_KEY', 'cmk_...' );
```

The settings UI shows the source (`constant` vs the legacy plaintext
`wp_options` value) and never echoes the key back into the page. While a key
is stored as a `wp_options` value the admin notices warn; saving the settings
form with the constant defined clears the legacy option row. The plaintext
option path is a v0.1 development scaffold only.

## 5. Shopify

App credentials come from the Shopify partner dashboard and are read from the
environment (`SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`) — never from the repo or
a database. Install/access tokens are kept in memory only in the v0.1 app
(`shopify/app.js`): after a restart the app re-runs OAuth. A production
deployment should persist tokens in a proper store (encrypted KV) keyed by
shop; the store key must itself come from the environment. Rotating the app
secret = generate a new secret in the partner dashboard and update the
environment (installed shops must re-install or re-authorize).

## 6. Operational rules

- No private key or seed phrase is ever placed on an application server; the
  server only ever holds what it needs to verify or sign in memory.
- Secrets are not logged: audit events record references, modes, and ids —
  never material.
- Rotations are recorded (audit events / operator log) with a rollback path
  defined before the change.
- The reference syntax is deliberately small (`secretref:env:`). A KMS or
  encrypted-vault-backed resolver can be added behind the same syntax without
  changing callers or stored values; any new scheme must fail closed until it
  is resolvable (unknown `secretref:` schemes are never treated as literals).
