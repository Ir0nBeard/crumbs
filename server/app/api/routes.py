"""FastAPI routes — v1 API (see docs/ATTRIBUTION_PROTOCOL.md).

Endpoints:
  POST   /v1/journeys         consent-gated receipt issuance
  POST   /v1/conversions      idempotent conversion stamping
  GET    /v1/verify           receipt status check
  POST   /v1/webhooks/orders  merchant signed order confirmation
  POST   /v1/payouts/batch    payout scheduling (records only — no float)
  POST   /v1/payouts/{pid}/settlement   record a rail settlement proof (admin)
  GET    /v1/payouts/{pid}    payout record + splits (proof envelope; admin)
  POST   /v1/admin/merchants/{mid}/tokens       issue a per-merchant token (admin)
  GET    /v1/admin/merchants/{mid}/tokens       list per-merchant tokens (admin)
  POST   /v1/admin/tokens/{token_id}/revoke     revoke a per-merchant token (admin)
  POST   /v1/admin/revoke     revocation (env-gated admin token)
  GET    /v1/health           liveness
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Merchant, Receipt
from ..db.session import get_db
from ..services import ledger, merchant_auth, payouts, webhooks

log = logging.getLogger("crumbs.api")

router = APIRouter(prefix="/v1")


def _settings():
    return get_settings()


# --- request models ---------------------------------------------------------


class ConsentModel(BaseModel):
    basis: str = Field(..., pattern="^(gpp|tcf|explicit|88b)$")
    # gpp/tcf: the actual IAB GPP/TCF string. 88b: merchant-side
    # consent/attestation record id. explicit: optional consent-record id.
    # Server-side verification lives in services/consent.py (structural
    # checks always run; CMP re-validation when CRUMBS_CMP_VERIFY_URL set).
    ref: str | None = Field(None, max_length=2048)


class JourneyRequest(BaseModel):
    merchant_id: str
    surface: str = Field(..., pattern="^(browser|api|chat)$")
    consent: ConsentModel | None = None
    agent_id: str | None = None


class ConversionRequest(BaseModel):
    receipt: str
    merchant_id: str
    order_id: str = Field(..., min_length=1, max_length=128)
    cart_value_minor_units: int = Field(..., ge=0)
    currency: str = Field(..., pattern="^[A-Za-z]{3}$")
    surface: str | None = Field(None, pattern="^(browser|api|chat)$")


class WebhookRequest(BaseModel):
    conversion_id: str | None = None
    order_id: str | None = None
    order_status: str = Field(..., pattern="^(finalized|cancelled|refunded)$")
    final_cart_value_minor_units: int | None = None
    t: int | None = Field(None, description="unix seconds — replay window (required by service)")


class RevokeRequest(BaseModel):
    kind: str = Field(..., pattern="^(receipt|journey|agent)$")
    id: str
    reason: str = Field(..., min_length=1, max_length=1000)


class TokenCreateRequest(BaseModel):
    label: str | None = Field(None, max_length=64)
    # https origins this token may be presented from in a browser (Origin
    # header). Empty = no origin restriction (server-to-server use).
    origins: list[str] | None = None


class TokenRevokeRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


class PayoutBatchRequest(BaseModel):
    limit: int = Field(500, ge=1, le=5000)


class PayoutSettlementRequest(BaseModel):
    """Proof of an executed rail settlement (recorded here; money moves
    off-ledger on the licensed rail). With `calldata`, the ERC-8021 Schema 2
    suffix is parsed and must carry `builder_code` — an on-chain proof.
    Without it, the record is a rail attestation (`rail_ref` mode)."""

    tx_hash: str = Field(..., pattern="^0x[0-9a-fA-F]{64}$",
                         description="EVM transaction hash of the settlement")
    calldata: str | None = Field(
        None, pattern="^0x[0-9a-fA-F]*$", max_length=262144,
        description="settlement calldata hex — optional; proves the builder code on-chain")
    builder_code: str = Field("bc_crumbs", pattern="^[a-z0-9_]{1,32}$")
    referral_ref: str | None = Field(
        None, max_length=40,
        description="journey/receipt id echoed in the x402 PAYMENT-RESPONSE referral (rct_/jrn_)")
    rail_ref: str | None = Field(None, max_length=255,
                                 description="facilitator/rail-side settlement reference")
    asset: str = Field("USDC", max_length=16)
    network: str = Field("eip155:8453", max_length=64)
    executed_by: str = Field("rail", max_length=64)


# --- error mapping -----------------------------------------------------------


def _ledger_error_to_http(exc: ledger.LedgerError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc), **(exc.extra or {})},
    )


def _payout_error_to_http(exc: payouts.PayoutError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _require_admin(settings, admin_token: str | None):
    """Env-gated admin gate shared by the money-adjacent endpoints.

    Mirrors /v1/admin/revoke semantics: 501 while CRUMBS_ADMIN_TOKEN is
    unset (fail closed), 401 on a bad token.
    """
    import secrets

    if not settings.admin_token:
        raise HTTPException(501, detail={"code": "ADMIN_DISABLED",
                                         "message": "admin endpoints disabled (CRUMBS_ADMIN_TOKEN unset)"})
    if not secrets.compare_digest(admin_token or "", settings.admin_token):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED", "message": "bad admin token"})


# --- journeys ----------------------------------------------------------------


@router.post("/journeys", status_code=201)
def create_journey(
    body: JourneyRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    try:
        result = ledger.issue_journey(
            db,
            mid=body.merchant_id,
            surface=body.surface,
            consent={"basis": body.consent.basis, "ref": body.consent.ref}
            if body.consent
            else {},
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            agent_id=body.agent_id,
            signing=request.app.state.signing,
            nonce_store=request.app.state.nonce_store,
            rate_limiter=request.app.state.rate_limiter,
            settings=settings,
        )
        return result
    except ledger.LedgerError as exc:
        raise _ledger_error_to_http(exc) from exc


# --- conversions -------------------------------------------------------------


@router.post("/conversions", status_code=201)
def create_conversion(
    body: ConversionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    merchant_key: str | None = Header(default=None, alias="X-Crumbs-Key"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    # --- Merchant auth ---------------------------------------------------
    # Per-merchant keyed tokens (recommended): X-Crumbs-Key must match an
    # active token whose merchant owns the receipt; the token's origin
    # allowlist (scoped CORS) is enforced when one is set. The legacy
    # shared CRUMBS_MERCHANT_API_KEY is still accepted for back-compat
    # unless CRUMBS_REQUIRE_MERCHANT_TOKENS is set.
    rid = _rid_from(body.receipt)
    receipt_row = db.get(Receipt, rid) if rid else None
    # API contract: the body merchant_id must be the receipt's merchant
    # (the receipt's mid is authoritative in the ledger — reject a
    # mismatch up front instead of silently ignoring the field).
    if receipt_row is not None and body.merchant_id != receipt_row.mid:
        raise HTTPException(
            422,
            detail={"code": "MERCHANT_MISMATCH",
                    "message": "merchant_id does not match the receipt's merchant"},
        )
    origin = request.headers.get("origin")
    if origin:
        try:
            origin = merchant_auth.validate_origin(origin)
        except ValueError:
            origin = None  # not a browser Origin shape — scope checks treat as absent
    try:
        merchant_auth.authenticate_conversion(
            db,
            key=merchant_key,
            settings=settings,
            receipt_mid=receipt_row.mid if receipt_row is not None else None,
            origin=origin,
        )
    except merchant_auth.MerchantAuthError as exc:
        raise HTTPException(exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    expected_key = f"{rid}:{body.order_id}"
    # Idempotency-Key must be "<rid>:<oid>" (docs/ATTRIBUTION_PROTOCOL.md §4.3) — validate when supplied
    if idempotency_key and idempotency_key != expected_key:
        raise HTTPException(
            422,
            detail={"code": "BAD_IDEMPOTENCY_KEY",
                    "message": "Idempotency-Key must be <receipt rid>:<order_id>"},
        )
    client_ip = request.client.host if request.client else None
    if client_ip:
        allowed, _ = request.app.state.rate_limiter.hit(
            "conversions", client_ip, settings.conversion_rate_limit, 3600
        )
        if not allowed:
            raise HTTPException(429, detail={"code": "RATE_LIMITED", "message": "too many requests"})
    try:
        result = ledger.record_conversion(
            db,
            receipt_str=body.receipt,
            oid=body.order_id,
            cart_value_minor_units=body.cart_value_minor_units,
            currency=body.currency,
            surface=body.surface,
            signing=request.app.state.signing,
            nonce_store=request.app.state.nonce_store,
            rate_limiter=request.app.state.rate_limiter,
            settings=settings,
        )
        return result
    except ledger.LedgerError as exc:
        if exc.code == ledger.E_IDEMPOTENT:
            # Safe retry: return the existing conversion with HTTP 200 (idempotent)
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=200, content={**exc.extra, "idempotent": True})
        raise _ledger_error_to_http(exc) from exc


def _rid_from(receipt_str: str) -> str:
    try:
        return json.loads(receipt_str).get("rid", "")
    except (json.JSONDecodeError, TypeError):
        return ""


# --- verify ------------------------------------------------------------------


@router.get("/verify")
def verify(receipt: str, request: Request, db: Session = Depends(get_db)):
    """GET variant (query string) — kept for diagnostics/back-compat; prefer
    POST /v1/verify so bearer receipts never ride URLs
    (docs/ATTRIBUTION_PROTOCOL.md §5)."""
    return ledger.verify_receipt(
        db,
        receipt,
        signing=request.app.state.signing,
        nonce_store=request.app.state.nonce_store,
    )


class VerifyRequest(BaseModel):
    receipt: str


@router.post("/verify")
def verify_post(body: VerifyRequest, request: Request, db: Session = Depends(get_db)):
    """POST variant — the canonical verify call; receipt travels in the body,
    never in a query string (docs/ATTRIBUTION_PROTOCOL.md §5)."""
    return ledger.verify_receipt(
        db,
        body.receipt,
        signing=request.app.state.signing,
        nonce_store=request.app.state.nonce_store,
    )


# --- webhooks ----------------------------------------------------------------


@router.post("/webhooks/orders")
async def order_webhook(
    request: Request,
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    """Merchant signed order confirmation (docs/ATTRIBUTION_PROTOCOL.md §4.4).

    Auth: X-Crumbs-Signature = HMAC-SHA256(merchant webhook secret, raw body),
    hex-encoded. The merchant is resolved from the conversion reference so the
    signature is checked against the right merchant's secret.
    """
    raw = await request.body()
    try:
        body = WebhookRequest.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail={"code": "MALFORMED", "message": "invalid JSON body"}) from exc

    mid = _resolve_mid(db, body)
    try:
        result = webhooks.process_order_webhook(
            db,
            mid=mid,
            body=raw,
            signature=request.headers.get("X-Crumbs-Signature", ""),
            settings=settings,
        )
        return result
    except webhooks.WebhookError as exc:
        raise HTTPException(exc.status_code,
                            detail={"code": exc.code, "message": str(exc)}) from exc


def _resolve_mid(db, body: WebhookRequest) -> str:
    """Resolve the merchant id from the conversion/order reference (webhook auth)."""
    from sqlalchemy import select

    from ..db.models import Conversion

    conv = None
    if body.conversion_id:
        conv = db.get(Conversion, body.conversion_id)
    elif body.order_id:
        conv = db.execute(
            select(Conversion).where(Conversion.oid == body.order_id)
        ).scalars().first()
    if conv is None:
        raise webhooks.WebhookError("NOT_FOUND", "conversion not found", 404)
    return conv.mid


# --- payouts -----------------------------------------------------------------


@router.post("/payouts/batch")
def payout_batch(
    body: PayoutBatchRequest,
    request: Request,
    admin_token: str | None = Header(default=None, alias="X-Crumbs-Admin-Token"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    """Schedule payout records for finalized conversions. Money-adjacent — an
    admin token is required (endpoints fail closed while it is unset);
    scheduling is still records-only."""
    import secrets

    if not settings.admin_token or not secrets.compare_digest(
        admin_token or "", settings.admin_token
    ):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED", "message": "admin token required"})
    try:
        return payouts.schedule_payouts(db, limit=body.limit, settings=settings)
    except payouts.PayoutError as exc:
        raise _payout_error_to_http(exc) from exc


@router.post("/payouts/{pid}/settlement", status_code=200)
def payout_settlement(
    pid: str,
    body: PayoutSettlementRequest,
    request: Request,
    admin_token: str | None = Header(default=None, alias="X-Crumbs-Admin-Token"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    """Record a rail settlement proof against a scheduled payout (admin).

    Money does not move through this endpoint — the licensed rail executed
    the transfer off-ledger; this records the proof (see
    services/payouts.py). With `calldata`, the ERC-8021 Schema 2 builder-code
    suffix must carry `builder_code` (`bc_crumbs`) — on-chain proof mode.
    """
    _require_admin(settings, admin_token)
    try:
        return payouts.record_settlement(
            db,
            pid,
            tx_hash=body.tx_hash,
            calldata=body.calldata,
            builder_code=body.builder_code,
            referral_ref=body.referral_ref,
            rail_ref=body.rail_ref,
            asset=body.asset,
            network=body.network,
            executed_by=body.executed_by,
            settings=settings,
        )
    except payouts.PayoutError as exc:
        raise _payout_error_to_http(exc) from exc


@router.get("/payouts/{pid}", status_code=200)
def payout_detail(
    pid: str,
    request: Request,
    admin_token: str | None = Header(default=None, alias="X-Crumbs-Admin-Token"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    """Payout record + splits (proof envelope). Admin-gated: ledger records
    are not public (no PII is exposed, but amounts are merchant data)."""
    _require_admin(settings, admin_token)
    record = payouts.get_payout(db, pid)
    if record is None:
        raise HTTPException(404, detail={"code": "PAYOUT_NOT_FOUND",
                                         "message": f"no payout record {pid}"})
    return record


# --- admin (merchant tokens) -----------------------------------------------


@router.post("/admin/merchants/{mid}/tokens", status_code=201)
def admin_issue_token(
    mid: str,
    body: TokenCreateRequest,
    request: Request,
    admin_token: str | None = Header(default=None, alias="X-Crumbs-Admin-Token"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    """Issue a per-merchant keyed token (scoped credential for
    /v1/conversions). The plaintext ``cmk_`` value is returned ONCE; only
    its SHA-256 hash is stored. Optional ``origins`` restrict browser-origin
    use of the token (scoped CORS). Admin-gated (fail closed unset)."""
    _require_admin(settings, admin_token)
    merchant = db.get(Merchant, mid)
    if merchant is None:
        raise HTTPException(404, detail={"code": "MERCHANT_NOT_FOUND",
                                         "message": f"no merchant {mid}"})
    try:
        row, plaintext = merchant_auth.issue_token(
            db, mid=mid, label=body.label, origins=body.origins, actor="admin")
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "BAD_ORIGIN", "message": str(exc)}) from exc
    return {**merchant_auth.token_public_view(row), "token": plaintext}


@router.get("/admin/merchants/{mid}/tokens")
def admin_list_tokens(
    mid: str,
    request: Request,
    admin_token: str | None = Header(default=None, alias="X-Crumbs-Admin-Token"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    """List a merchant's tokens (metadata only — hashes never leave)."""
    _require_admin(settings, admin_token)
    merchant = db.get(Merchant, mid)
    if merchant is None:
        raise HTTPException(404, detail={"code": "MERCHANT_NOT_FOUND",
                                         "message": f"no merchant {mid}"})
    return {"merchant_id": mid, "tokens": merchant_auth.list_tokens(db, mid)}


@router.post("/admin/tokens/{token_id}/revoke")
def admin_revoke_token(
    token_id: str,
    body: TokenRevokeRequest | None = None,
    admin_token: str | None = Header(default=None, alias="X-Crumbs-Admin-Token"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    """Revoke a per-merchant token — conversions using it start failing
    immediately (403 TOKEN_REVOKED)."""
    _require_admin(settings, admin_token)
    try:
        row = merchant_auth.revoke_token(
            db, token_id=token_id,
            reason=(body.reason if body else None), actor="admin")
    except merchant_auth.MerchantAuthError as exc:
        raise HTTPException(exc.status_code,
                            detail={"code": exc.code, "message": exc.message}) from exc
    return {"token_id": row.token_id, "merchant_id": row.mid, "status": row.status}


# --- admin (revocation) -----------------------------------------------------


@router.post("/admin/revoke", status_code=200)
def admin_revoke(
    body: RevokeRequest,
    request: Request,
    admin_token: str | None = Header(default=None, alias="X-Crumbs-Admin-Token"),
    db: Session = Depends(get_db),
    settings=Depends(_settings),
):
    if not settings.admin_token:
        raise HTTPException(501, detail={"code": "ADMIN_DISABLED",
                                         "message": "admin endpoints disabled (CRUMBS_ADMIN_TOKEN unset)"})
    import secrets

    if not secrets.compare_digest(admin_token or "", settings.admin_token):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED", "message": "bad admin token"})
    try:
        ledger.revoke(db, body.kind, body.id, body.reason, actor="admin")
    except ledger.LedgerError as exc:
        raise _ledger_error_to_http(exc) from exc
    return {"revoked": {"kind": body.kind, "id": body.id}}


# --- health ------------------------------------------------------------------


@router.get("/health")
def health(request: Request):
    from sqlalchemy import text

    db_ok = True
    try:
        session = next(get_db())
        session.execute(text("SELECT 1"))
        session.close()
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "app": request.app.state.settings.app_name,
        "db": "ok" if db_ok else "error",
        "stores": "redis" if getattr(request.app.state, "redis_connected", False) else "memory",
        "version": 1,
    }
