"""Server-side consent-signal verification (docs/ATTRIBUTION_PROTOCOL.md §6).

Replaces the v0.1 501 fail-closed stub ("CMP re-validation is a STUB") in
``ledger.issue_journey`` (pre-launch consent-verification requirement).

Design — every journey issuance carries a recorded lawful basis; this module
stops the server from blindly trusting a client-asserted signal:

* ``explicit`` — no cross-vendor wire standard exists for an "explicit"
  claim; the merchant's own consent-record id (``ref``) is the audit key when
  supplied. The issuance audit row IS the record. ``ref`` optional (API/chat
  callers may run their own flow). Mode: ``record``.

* ``88b`` — the EU Data Omnibus (88a/88b) articles are still in trilogue
  (adoption ~2027-28); no machine-readable wire format exists yet. A non-empty
  ``ref`` is REQUIRED: it must name the merchant-side consent/attestation
  record an auditor can pull. Verified = presence + auditability. Mode:
  ``local`` (presence check).

* ``tcf`` — ``ref`` must be an IAB Europe TCF EU v2 TC string. The fixed
  200-bit prefix (version .. purposeLegitimateInterests) is decoded and
  checked: version == 2, sane 36-bit ``created`` timestamp, freshness within
  ``CRUMBS_MAX_CONSENT_SIGNAL_AGE_SECONDS``, alphabetic consent language,
  non-empty purposeConsents and — when
  ``CRUMBS_CONSENT_TCF_REQUIRE_PURPOSE1`` (default true) — purpose 1
  (storage/access) consented. Purpose 1 is consent-only under TCF and is the
  purpose Crumbs journey identifiers ride on; a TC string without it cannot
  ground storage-based attribution. Mode: ``local``.

* ``gpp`` — ``ref`` must be a GPP string: "~"-joined websafe-base64 header +
  sections whose header decodes to Type=3 / Version=1 (leading 6-bit values
  3,1 = leading chars "DB", per the IAB GPP Consent String Specification).
  v1 validates shape + segment decodability only; per-section purpose
  semantics are NOT decoded yet (documented limitation below) and are
  deferred to the configured CMP re-validation endpoint — exactly like
  TCF-EU-inside-GPP. No over-claiming. Mode: ``local``.

When ``CRUMBS_CMP_VERIFY_URL`` is set, a signal that passes the local checks
is POSTed to the CMP (JSON ``{"basis","ref","surface"}``, timeout-bounded)
and the CMP verdict is authoritative: 2xx + ``{"valid": true}`` passes;
anything else fails closed (``CONSENT_REFUSED`` / ``CMP_UNREACHABLE``). When
it is unset, the local structural checks above are the whole gate — they
replace the old "empty -> trust the client signal (MVP)" behaviour.

Documented v1 limitations (do not claim more):
- GPP per-section purpose decoding is not implemented; GPP strings are
  validated for shape and decodability. Deep GPP semantics require the CMP
  re-validation endpoint (or a later cycle's per-section parser).
- TCF strings are validated structurally and for purpose-1 consent; vendor
  bitfields, CMP-id allow-listing and GVL cross-checks are not performed.

Error contract: verification failures raise :class:`ConsentError` with a
stable code + HTTP status; ``ledger.issue_journey`` maps them onto
``LedgerError`` so the API layer is unchanged.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
import time

log = logging.getLogger("crumbs.consent")

# Stable error codes (surfaced through the API detail["code"]).
CONSENT_REQUIRED = "CONSENT_REQUIRED"
CONSENT_INVALID = "CONSENT_INVALID"
CONSENT_STALE = "CONSENT_STALE"
CONSENT_REFUSED = "CONSENT_REFUSED"
CMP_UNREACHABLE = "CMP_UNREACHABLE"

CONSENT_BASES = frozenset({"gpp", "tcf", "explicit", "88b"})
_REF_MAX = 2048  # matches models.Journey.consent_ref (VARCHAR(2048)/TEXT)

# --- IAB Europe TCF EU v2: fixed 200-bit prefix field layout --------------
# (name, start_bit, width) — bit 0 is the MSB of the decoded byte stream.
_TC_PREFIX = (
    ("version", 0, 6),
    ("created", 6, 36),
    ("last_updated", 42, 36),
    ("cmp_id", 78, 12),
    ("cmp_version", 90, 12),
    ("consent_screen", 102, 6),
    ("consent_language", 108, 12),
    ("vendor_list_version", 120, 12),
    ("tcf_policy_version", 132, 6),
    ("is_service_specific", 138, 1),
    ("use_non_standard_stacks", 139, 1),
    ("special_feature_optins", 140, 12),
    ("purpose_consents", 152, 24),
    ("purpose_legitimate_interests", 176, 24),
)
_TC_MIN_BITS = 213  # prefix (200) + publisherCountryCode (12) + 1 (v2.0 one-treatment bit)

_WEBSAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConsentError(Exception):
    """Consent verification failure with a stable code + HTTP status."""

    def __init__(self, code: str, message: str, status_code: int = 403,
                 extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}


# ---------------------------------------------------------------------------
# Base64 helpers (TC strings ship standard or websafe; GPP ships websafe,
# unpadded)
# ---------------------------------------------------------------------------

def _b64decode_tolerant(s: str) -> bytes:
    """Decode a base64 string tolerating standard/websafe alphabets + padding."""
    if not s or len(s) % 4 == 1:
        raise ValueError("not valid base64 length")
    if "-" in s or "_" in s:
        s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("not valid base64") from exc


def _b64decode_websafe_unpadded(s: str) -> bytes:
    """Decode a GPP segment: websafe alphabet, no '=' padding."""
    if not _WEBSAFE_RE.match(s):
        raise ValueError("not websafe base64")
    if len(s) % 4 == 1:
        raise ValueError("not valid websafe base64 length")
    padded = s + "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("not valid websafe base64") from exc


# ---------------------------------------------------------------------------
# TCF EU v2 TC string parser
# ---------------------------------------------------------------------------

def parse_tc_string(ref: str) -> dict:
    """Parse the fixed prefix of an IAB Europe TCF EU v2 TC string.

    Returns a dict with decoded fields. Raises ValueError with a reason on any
    structural problem. Bit layout per the IAB TCF v2 spec (fields are
    big-endian; purpose N of purposeConsents is bit (24 - N)).
    """
    raw = _b64decode_tolerant(ref.strip())
    total_bits = len(raw) * 8
    if total_bits < _TC_MIN_BITS:
        raise ValueError(f"too short ({total_bits} bits < {_TC_MIN_BITS})")
    stream = int.from_bytes(raw, "big")

    def read(start: int, width: int) -> int:
        shift = total_bits - start - width
        if shift < 0:
            raise ValueError("truncated field")
        return (stream >> shift) & ((1 << width) - 1)

    out: dict = {}
    for name, start, width in _TC_PREFIX:
        out[name] = read(start, width)

    if out["version"] != 2:
        raise ValueError(f"unsupported TCF version {out['version']} (expected 2)")

    # consent language: two 6-bit letters ('a' = 1 in the IAB encoding; a few
    # encoders use 0-based — accept 0..26, reject anything else as garbage)
    lang_bits = out["consent_language"]
    c1, c2 = (lang_bits >> 6) & 0x3F, lang_bits & 0x3F
    if c1 > 26 or c2 > 26:
        raise ValueError(f"invalid consent language code ({c1},{c2})")
    out["consent_language"] = (
        (chr(96 + c1) if 1 <= c1 <= 26 else "?")
        + (chr(96 + c2) if 1 <= c2 <= 26 else "?")
    )

    pc = out["purpose_consents"]
    if pc == 0:
        raise ValueError("purposeConsents is empty (no purpose consented)")
    out["purpose1"] = bool(pc & (1 << 23))  # purpose 1 = MSB of the 24-bit field
    out["purposes_consented"] = [i for i in range(1, 25) if pc & (1 << (24 - i))]
    return out


# ---------------------------------------------------------------------------
# Per-basis verification
# ---------------------------------------------------------------------------

def _check_ref(consent: dict, settings) -> str | None:
    ref = consent.get("ref")
    if ref is None:
        return None
    if not isinstance(ref, str) or not ref.strip():
        raise ConsentError(
            CONSENT_INVALID, "consent ref must be a non-empty string", 422)
    ref = ref.strip()
    if len(ref) > _REF_MAX:
        raise ConsentError(
            CONSENT_INVALID,
            f"consent ref exceeds {_REF_MAX} chars (real TC/GPP strings fit; "
            "anything longer is not a consent signal)", 422)
    return ref


def _verify_explicit(consent: dict, settings) -> tuple[list[str], dict]:
    # No wire standard to verify against — the issuance audit row is the
    # record. ref, when present, names the merchant-side consent record.
    ref = _check_ref(consent, settings)
    return ["record_only"], {"ref_present": ref is not None}


def _verify_88b(consent: dict, settings) -> tuple[list[str], dict]:
    # EU Data Omnibus (88a/88b) is still in trilogue — no wire format exists.
    # The ref MUST name the merchant-side consent/attestation record.
    ref = _check_ref(consent, settings)
    if not ref:
        raise ConsentError(
            CONSENT_INVALID,
            "basis '88b' requires a ref naming the merchant-side consent/"
            "attestation record (no wire standard exists yet; the ref is the "
            "audit key)", 422)
    return ["ref_required_present"], {
        "basis": "88b",
        "note": "no machine-readable standard (EU Data Omnibus in trilogue); "
                "ref is the merchant-side audit key",
    }


def _verify_tcf(consent: dict, settings, surface: str | None) -> tuple[list[str], dict]:
    ref = _check_ref(consent, settings)
    if not ref:
        raise ConsentError(
            CONSENT_INVALID,
            "basis 'tcf' requires the IAB Europe TCF EU v2 TC string in ref",
            422)
    try:
        parsed = parse_tc_string(ref)
    except ValueError as exc:
        raise ConsentError(
            CONSENT_INVALID, f"malformed TCF signal: {exc}", 422) from exc

    now = time.time()
    created = parsed["created"]
    checks = ["tcf_v2_structure"]
    if created > now + 3600:
        raise ConsentError(
            CONSENT_INVALID, "TCF signal has a future created timestamp", 422,
            {"created": created})
    max_age = settings.max_consent_signal_age_seconds
    age = now - created
    if age > max_age:
        raise ConsentError(
            CONSENT_STALE,
            "TCF signal is stale (TCF requires re-consent within 13 months)",
            403, {"created": created, "age_seconds": int(age),
                  "max_age_seconds": max_age})
    checks.append("fresh")

    if settings.consent_tcf_require_purpose1 and not parsed["purpose1"]:
        raise ConsentError(
            CONSENT_REFUSED,
            "TCF signal records no consent to storage/access (purpose 1), "
            "which is consent-only and is the purpose journey identifiers "
            "ride on — storage-based attribution cannot proceed",
            403, {"required_purpose": 1,
                  "purposes_consented": parsed["purposes_consented"]})
    checks.append("purpose1_consented" if parsed["purpose1"] else "purposes_present")

    summary = {
        "version": parsed["version"],
        "created": created,
        "tcf_policy_version": parsed["tcf_policy_version"],
        "consent_language": parsed["consent_language"],
        "purpose1": parsed["purpose1"],
        "purposes_consented": parsed["purposes_consented"],
    }
    return checks, summary


def _verify_gpp(consent: dict, settings) -> tuple[list[str], dict]:
    # GPP: "~"-joined websafe-base64 header + sections. Header leading 6-bit
    # values are Type=3 (GPP header field) then Version=1 — i.e. the string
    # starts with the base64 chars "DB". v1 validates shape/decodability only
    # (see module docstring); per-section semantics need the CMP endpoint.
    ref = _check_ref(consent, settings)
    if not ref:
        raise ConsentError(
            CONSENT_INVALID,
            "basis 'gpp' requires the IAB GPP string in ref", 422)
    if "~" not in ref:
        raise ConsentError(
            CONSENT_INVALID,
            "malformed GPP signal: header and sections are '~'-joined", 422)
    segments = ref.split("~")
    header = segments[0]
    if len(header) < 2 or header[0] != "D" or header[1] != "B":
        raise ConsentError(
            CONSENT_INVALID,
            "malformed GPP signal: header must begin with Type=3/Version=1 "
            "('DB') per the IAB GPP Consent String Specification", 422)
    if len(segments) < 2 or any(not s for s in segments):
        raise ConsentError(
            CONSENT_INVALID,
            "malformed GPP signal: at least one non-empty section is required "
            "after the header", 422)
    try:
        for seg in segments:
            decoded = _b64decode_websafe_unpadded(seg)
            if seg is not header and not decoded:
                raise ValueError("empty section payload")
    except ValueError as exc:
        raise ConsentError(
            CONSENT_INVALID, f"malformed GPP signal: {exc}", 422) from exc
    checks = ["gpp_shape", "header_type3_version1", "sections_decodable"]
    return checks, {
        "basis": "gpp",
        "sections": len(segments) - 1,
        "note": "v1 validates shape only; per-section purpose semantics are "
                "deferred to the CMP re-validation endpoint",
    }


# ---------------------------------------------------------------------------
# CMP re-validation (authoritative when configured)
# ---------------------------------------------------------------------------

def _cmp_verify(basis: str, ref: str, surface: str | None, settings) -> dict:
    import httpx

    url = settings.cmp_verify_url
    try:
        resp = httpx.post(
            url,
            json={"basis": basis, "ref": ref, "surface": surface},
            timeout=settings.cmp_verify_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise ConsentError(
            CMP_UNREACHABLE,
            f"CMP re-validation endpoint unreachable ({exc.__class__.__name__})",
            503, {"cmp_verify_url": url}) from exc
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code < 400 and isinstance(body, dict) and body.get("valid") is True:
        return {"url": url, "status": resp.status_code, "valid": True}
    if isinstance(body, dict) and body.get("valid") is False:
        raise ConsentError(
            CONSENT_REFUSED,
            "CMP re-validation rejected the consent signal",
            403, {"cmp_status": resp.status_code})
    raise ConsentError(
        CMP_UNREACHABLE,
        f"CMP re-validation endpoint returned HTTP {resp.status_code}",
        503, {"cmp_verify_url": url, "cmp_status": resp.status_code})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def verify_consent_signal(consent: dict | None, settings, *,
                          surface: str | None = None) -> dict:
    """Verify a consent claim and return a verdict.

    Returns ``{"basis", "mode", "checks", "parsed"}`` where ``mode`` is
    ``"record"`` (explicit — audit row is the record), ``"local"`` (structural
    verification passed) or ``"cmp"`` (local checks passed AND the configured
    CMP re-validation accepted the signal). Raises :class:`ConsentError` on
    any failure.
    """
    consent = consent or {}
    basis = consent.get("basis")
    if not isinstance(basis, str) or basis not in CONSENT_BASES:
        raise ConsentError(
            CONSENT_REQUIRED,
            "receipt issuance requires a recorded consent basis "
            "(gpp|tcf|explicit|88b)", 403, {"basis": basis})

    if basis == "explicit":
        checks, parsed = _verify_explicit(consent, settings)
        mode = "record"
    elif basis == "88b":
        checks, parsed = _verify_88b(consent, settings)
        mode = "local"
    elif basis == "tcf":
        checks, parsed = _verify_tcf(consent, settings, surface)
        mode = "local"
    else:  # gpp
        checks, parsed = _verify_gpp(consent, settings)
        mode = "local"

    verdict: dict = {
        "basis": basis,
        "mode": mode,
        "checks": checks,
        "parsed": parsed,
    }
    ref = consent.get("ref")
    if settings.cmp_verify_url and basis in ("gpp", "tcf", "88b"):
        verdict["cmp"] = _cmp_verify(basis, (ref or "").strip(), surface, settings)
        verdict["mode"] = "cmp"
        log.info("consent re-validated by CMP basis=%s surface=%s", basis, surface)
    return verdict
