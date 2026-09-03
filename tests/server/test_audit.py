"""Audit trail — append-only audit_events on every mutation."""
from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import AuditEvent


def test_audit_events_written_for_journey_and_conversion(client, seeded_merchant, issue_receipt,
                                                        db_session):
    journey = issue_receipt()
    client.post(
        "/v1/conversions",
        json={
            "receipt": journey["receipt"],
            "merchant_id": seeded_merchant.mid,
            "order_id": "ord_audit",
            "cart_value_minor_units": 1000,
            "currency": "USD",
        },
        headers={"X-Crumbs-Key": "test-merchant-key"},
    )
    events = db_session.execute(
        select(AuditEvent.event_type).order_by(AuditEvent.event_id)
    ).scalars().all()
    assert "agent_registered" in events
    assert "journey_issued" in events
    assert "conversion_recorded" in events


def test_audit_events_append_only(client, seeded_merchant, issue_receipt, db_session):
    """No update path exists for audit_events — count only grows."""
    before = db_session.execute(select(func.count(AuditEvent.event_id))).scalar_one()
    journey = issue_receipt()
    after_issue = db_session.execute(select(func.count(AuditEvent.event_id))).scalar_one()
    assert after_issue > before
    # An idempotent retry must NOT duplicate the conversion_recorded event
    client.post(
        "/v1/conversions",
        json={
            "receipt": journey["receipt"],
            "merchant_id": seeded_merchant.mid,
            "order_id": "ord_ao",
            "cart_value_minor_units": 100,
            "currency": "USD",
        },
        headers={"X-Crumbs-Key": "test-merchant-key"},
    )
    client.post(
        "/v1/conversions",
        json={
            "receipt": journey["receipt"],
            "merchant_id": seeded_merchant.mid,
            "order_id": "ord_ao",
            "cart_value_minor_units": 100,
            "currency": "USD",
        },
        headers={"X-Crumbs-Key": "test-merchant-key"},
    )
    recorded = db_session.execute(
        select(func.count(AuditEvent.event_id)).where(
            AuditEvent.event_type == "conversion_recorded",
            AuditEvent.entity_id.like("c_%"),
        )
    ).scalar_one()
    assert recorded == 1, "idempotent retry must not append a duplicate event"
