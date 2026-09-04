"""Webhook secret management: secretref indirection, admin API, fail-closed semantics.

Covers core/secrets.py resolution rules, the verification-time wiring in
services/webhooks.py (env references + strict-mode literal rejection), and
the admin reference-management endpoints — asserting the secret material
never appears in responses and unresolvable/untrusted configurations fail
closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings, get_settings
from app.core import secrets as secret_core
from app.db.models import AuditEvent
from app.seed import DEFAULT_WEBHOOK_SECRET
from app.services import webhooks

ADMIN = {"X-Crumbs-Admin-Token": "test-admin-token"}

ENV_SECRET = "env-material-secret-7f3a91"
ENV_VAR = "CRUMBS_WEBHOOK_SECRET_TEST_M_01J"


def webhook_sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def webhook_payload(conversion_id, order_status="finalized", t=None) -> dict:
    return {
        "conversion_id": conversion_id,
        "order_status": order_status,
        "t": int(t if t is not None else time.time()),
    }


def signed_body(payload: dict, secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return body, webhook_sig(body, secret)


def stamp(client, receipt, oid="ord_ws", value=5000):
    from app.core.receipt import parse_receipt

    payload = parse_receipt(receipt)
    resp = client.post(
        "/v1/conversions",
        json={
            "receipt": receipt,
            "merchant_id": payload["mid"],
            "order_id": oid,
            "cart_value_minor_units": value,
            "currency": "USD",
        },
        headers={"X-Crumbs-Key": "test-merchant-key",
                 "Idempotency-Key": f"{payload['rid']}:{oid}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["conversion_id"]


def pg_strict_settings(**kw) -> Settings:
    """Non-SQLite settings with strict secret-ref enforcement (overrides)."""
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@db:5432/crumbs",
        enforce_secret_refs=True,
        **kw,
    )


# --- core/secrets.py resolution rules ---------------------------------------


def test_core_env_ref_roundtrip():
    ref = secret_core.make_env_ref(ENV_VAR)
    assert ref == f"secretref:env:{ENV_VAR}"
    assert secret_core.is_secret_ref(ref)
    assert secret_core.secret_ref_env_name(ref) == ENV_VAR


def test_core_env_ref_rejects_invalid_names():
    for bad in ("1LEADING_DIGIT", "has space", "", "dash-name", "a.b"):
        with pytest.raises(ValueError):
            secret_core.make_env_ref(bad)


def test_core_resolve_env_present_and_missing():
    ref = secret_core.make_env_ref(ENV_VAR)
    env = {ENV_VAR: ENV_SECRET}
    assert secret_core.resolve_secret(ref, env=env) == ENV_SECRET
    # Missing variable -> None (fail closed), never an exception.
    assert secret_core.resolve_secret(ref, env={}) is None
    assert secret_core.resolve_secret(ref, env={ENV_VAR: ""}) is None


def test_core_resolve_malformed_and_unknown_scheme_refs_fail_closed():
    # Prefixed but no name / invalid name / unknown scheme: NEVER a literal.
    assert secret_core.resolve_secret("secretref:env:") is None
    assert secret_core.resolve_secret("secretref:env:9bad") is None
    assert secret_core.resolve_secret("secretref:kms:arn:aws:...") is None
    assert secret_core.resolve_secret("secretref:file:/etc/key") is None
    # ... even when literals would otherwise be allowed.
    assert (
        secret_core.resolve_secret("secretref:kms:x", literal_ok=True, enforce_refs=False)
        is None
    )


def test_core_resolve_literal_strict_mode_semantics():
    literal = "literal-secret-abc"
    # Back-compat default: literal resolves on any database.
    assert secret_core.resolve_secret(literal, enforce_refs=False) == literal
    # Local SQLite dev is exempt from strict-mode literal rejection.
    assert (
        secret_core.resolve_secret(literal, enforce_refs=True, literal_ok=True) == literal
    )
    # Strict mode on a real database: literal -> None (fail closed).
    assert secret_core.resolve_secret(literal, enforce_refs=True, literal_ok=False) is None


def test_core_resolve_empty_and_none():
    assert secret_core.resolve_secret(None) is None
    assert secret_core.resolve_secret("") is None
    assert secret_core.resolve_secret("   ") == "   "  # literal whitespace is material


# --- verification-time resolution (services/webhooks.py) ---------------------


def test_webhook_env_ref_authenticates(monkeypatch, client, seeded_merchant, issue_receipt):
    """A secretref:env reference resolves at verification time and signs OK."""
    monkeypatch.setenv(ENV_VAR, ENV_SECRET)
    merchant = seeded_merchant
    mid = merchant.mid
    # Store the reference via the admin API.
    resp = client.post(
        f"/v1/admin/merchants/{mid}/webhook-secret",
        json={"value": secret_core.make_env_ref(ENV_VAR)},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "env-ref"

    cid = stamp(client, issue_receipt()["receipt"], oid="ord_env")
    body, sig = signed_body(webhook_payload(cid), ENV_SECRET)
    resp = client.post(
        "/v1/webhooks/orders", content=body,
        headers={"X-Crumbs-Signature": sig},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["idempotent"] is False


def test_webhook_env_ref_unset_fails_closed(monkeypatch, client, seeded_merchant, issue_receipt):
    """Reference stored while resolvable; env var removed -> BAD_SIGNATURE 401."""
    monkeypatch.setenv(ENV_VAR, ENV_SECRET)
    mid = seeded_merchant.mid
    assert client.post(
        f"/v1/admin/merchants/{mid}/webhook-secret",
        json={"value": secret_core.make_env_ref(ENV_VAR)},
        headers=ADMIN,
    ).status_code == 200

    cid = stamp(client, issue_receipt()["receipt"], oid="ord_envoff")
    monkeypatch.delenv(ENV_VAR, raising=False)
    body, sig = signed_body(webhook_payload(cid), ENV_SECRET)
    resp = client.post(
        "/v1/webhooks/orders", content=body,
        headers={"X-Crumbs-Signature": sig},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "BAD_SIGNATURE"
    # The masked view reports the reference is no longer resolvable.
    view = client.get(f"/v1/admin/merchants/{mid}/webhook-secret", headers=ADMIN)
    assert view.status_code == 200
    assert view.json()["resolvable"] is False
    assert view.json()["mode"] == "env-ref"


def test_strict_mode_literal_fails_closed_on_postgres(monkeypatch, db_session, client,
                                                      seeded_merchant, issue_receipt):
    """Strict mode + literal on a non-SQLite DB: correct signature still 401."""
    literal = "literal-but-forbidden-on-pg"
    merchant = seeded_merchant
    merchant.webhook_secret = literal
    db_session.commit()

    cid = stamp(client, issue_receipt()["receipt"], oid="ord_strict")
    body, sig = signed_body(webhook_payload(cid), literal)
    with pytest.raises(webhooks.WebhookError) as exc_info:
        webhooks.process_order_webhook(
            db_session, mid=merchant.mid, body=body, signature=sig,
            settings=pg_strict_settings(),
        )
    assert exc_info.value.code == "BAD_SIGNATURE"
    assert exc_info.value.status_code == 401


def test_strict_mode_env_ref_ok_on_postgres(monkeypatch, db_session, client,
                                            seeded_merchant, issue_receipt):
    """Strict mode + resolvable env reference on a non-SQLite DB: works."""
    monkeypatch.setenv(ENV_VAR, ENV_SECRET)
    merchant = seeded_merchant
    merchant.webhook_secret = secret_core.make_env_ref(ENV_VAR)
    db_session.commit()

    cid = stamp(client, issue_receipt()["receipt"], oid="ord_strictenv")
    body, sig = signed_body(webhook_payload(cid), ENV_SECRET)
    result = webhooks.process_order_webhook(
        db_session, mid=merchant.mid, body=body, signature=sig,
        settings=pg_strict_settings(),
    )
    assert result["order_status"] == "finalized"


def test_dev_default_still_guarded_outside_sqlite(db_session, client,
                                                  seeded_merchant, issue_receipt):
    """The known dev constant keeps its absolute guard on non-SQLite DBs."""
    merchant = seeded_merchant
    assert merchant.webhook_secret == DEFAULT_WEBHOOK_SECRET  # seeded default

    cid = stamp(client, issue_receipt()["receipt"], oid="ord_devguard")
    body, sig = signed_body(webhook_payload(cid), DEFAULT_WEBHOOK_SECRET)
    with pytest.raises(webhooks.WebhookError) as exc_info:
        webhooks.process_order_webhook(
            db_session, mid=merchant.mid, body=body, signature=sig,
            settings=pg_strict_settings(),
        )
    assert exc_info.value.code == "DEV_SECRET_GUARD"
    assert exc_info.value.status_code == 500


# --- admin reference-management API ------------------------------------------


def test_admin_webhook_secret_lifecycle(client, db_session, seeded_merchant, monkeypatch):
    """POST env-ref -> masked GET -> DELETE -> GET; audited; value never leaks."""
    monkeypatch.setenv(ENV_VAR, ENV_SECRET)
    mid = seeded_merchant.mid

    resp = client.post(
        f"/v1/admin/merchants/{mid}/webhook-secret",
        json={"value": secret_core.make_env_ref(ENV_VAR)},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True and body["mode"] == "env-ref"
    assert body["env_var"] == ENV_VAR
    assert ENV_SECRET not in json.dumps(body)  # material never returned

    audit_rows = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "merchant_webhook_secret_set")
    ).scalars().all()
    assert len(audit_rows) == 1
    assert ENV_SECRET not in audit_rows[0].payload  # audit never logs material

    view = client.get(f"/v1/admin/merchants/{mid}/webhook-secret", headers=ADMIN)
    assert view.status_code == 200
    assert view.json()["resolvable"] is True
    assert ENV_SECRET not in json.dumps(view.json())

    cleared = client.delete(f"/v1/admin/merchants/{mid}/webhook-secret", headers=ADMIN)
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False

    view2 = client.get(f"/v1/admin/merchants/{mid}/webhook-secret", headers=ADMIN)
    assert view2.json()["configured"] is False and view2.json()["mode"] is None


def test_admin_ref_unresolvable_refused(client, seeded_merchant):
    """A reference whose env var is unset in this process is refused (422)."""
    resp = client.post(
        f"/v1/admin/merchants/{seeded_merchant.mid}/webhook-secret",
        json={"value": secret_core.make_env_ref("CRUMBS_WEBHOOK_SECRET_NOT_SET_ANYWHERE")},
        headers=ADMIN,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "UNRESOLVABLE_REF"


def test_admin_malformed_refs_refused(client, seeded_merchant):
    mid = seeded_merchant.mid
    for bad in ("secretref:env:", "secretref:kms:arn:x", "secretref:file:/tmp/k"):
        resp = client.post(
            f"/v1/admin/merchants/{mid}/webhook-secret",
            json={"value": bad},
            headers=ADMIN,
        )
        assert resp.status_code == 422, bad
        assert resp.json()["detail"]["code"] == "INVALID_SECRET_REF", bad


def test_admin_literal_ok_outside_strict_mode(client, seeded_merchant):
    """Literal accepted by default (back-compat) — local SQLite dev flow."""
    resp = client.post(
        f"/v1/admin/merchants/{seeded_merchant.mid}/webhook-secret",
        json={"value": "my-dev-literal"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "literal"
    assert "my-dev-literal" not in json.dumps(resp.json())


def test_admin_literal_refused_in_strict_mode(monkeypatch, db_session, seeded_merchant):
    """Strict mode: the admin API refuses to store a literal (422)."""
    monkeypatch.setenv("CRUMBS_ENFORCE_SECRET_REFS", "true")
    get_settings.cache_clear()
    try:
        from app.main import create_app

        app = create_app()
        with TestClient(app) as c:
            resp = c.post(
                f"/v1/admin/merchants/{seeded_merchant.mid}/webhook-secret",
                json={"value": "literal-should-fail"},
                headers=ADMIN,
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["code"] == "SECRET_REF_REQUIRED"
    finally:
        get_settings.cache_clear()


def test_admin_webhook_secret_gate(client, seeded_merchant):
    """Admin gate: wrong token 401; unknown merchant 404."""
    mid = seeded_merchant.mid
    assert client.post(
        f"/v1/admin/merchants/{mid}/webhook-secret",
        json={"value": "x"},
        headers={"X-Crumbs-Admin-Token": "wrong"},
    ).status_code == 401
    assert client.get(
        f"/v1/admin/merchants/{mid}/webhook-secret", headers=ADMIN
    ).status_code == 200
    assert client.get(
        "/v1/admin/merchants/m_no_such_merchant/webhook-secret", headers=ADMIN
    ).status_code == 404
    assert client.delete(
        "/v1/admin/merchants/m_no_such_merchant/webhook-secret", headers=ADMIN
    ).status_code == 404
