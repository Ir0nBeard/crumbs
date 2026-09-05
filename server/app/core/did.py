"""did:pkh (CAIP-10) identity helpers for agent anchoring.

Agents in the ledger are identified by an internal ``ag_`` id on the wire
receipt. A did:pkh anchor (``agents.registry_ref``) binds that internal id to
a wallet-style decentralized identifier (CAIP-10 account, e.g.
``did:pkh:eip155:8453:0xAbC…``) so an agent's journeys are provably tied to
one on-chain identity — the identity an x402 seller or worker presents when
it participates in attribution.

This module is deliberately dependency-free (pure stdlib) so it can be
mirrored by the SDK side (``sdk/python/crumbs_x402.py``) without imports.
"""

from __future__ import annotations

import re

DID_PREFIX = "did:pkh:"
# CAIP-10: did:pkh:<namespace>:<reference>:<account>
# namespace: lowercase alphanumeric (e.g. eip155, solana)
# reference: 1+ alnum (chain id / network)
# account:   1+ alnum, may include 0x and separators
_DID_PKH_RE = re.compile(r"^did:pkh:[a-z0-9]+:[a-zA-Z0-9]+:[a-zA-Z0-9]+$")
_EIP155_ACCOUNT_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

MAX_REGISTRY_REF = 255  # agents.registry_ref column width


class DidError(ValueError):
    """Raised for malformed did:pkh identifiers."""


def is_did_pkh(value: object) -> bool:
    """Shape check only — returns False for None/non-str/malformed."""
    if not isinstance(value, str) or not value:
        return False
    if len(value) > MAX_REGISTRY_REF:
        return False
    if not value.startswith(DID_PREFIX):
        return False
    parts = value.split(":")
    if len(parts) != 5:  # did, pkh, namespace, reference, account
        return False
    if not _DID_PKH_RE.match(value):
        return False
    namespace = parts[2]
    account = parts[4]
    if namespace == "eip155" and not _EIP155_ACCOUNT_RE.match(account):
        return False
    return True


def validate_did_pkh(value: object) -> str:
    """Return the did:pkh string or raise DidError (used at API edges)."""
    if not is_did_pkh(value):
        raise DidError("agent_did must be a did:pkh CAIP-10 identifier "
                       "(did:pkh:<namespace>:<reference>:<account>)")
    return value


def build_did_pkh(namespace: str, reference: str, account: str) -> str:
    """Build ``did:pkh:...`` from parts; validates on the way out."""
    if not isinstance(namespace, str) or not namespace.islower() \
            or not namespace.isalnum():
        raise DidError("namespace must be lowercase alphanumeric (e.g. eip155)")
    if not isinstance(reference, str) or not reference:
        raise DidError("chain reference is required")
    if not isinstance(account, str) or not account:
        raise DidError("account is required")
    did = f"did:pkh:{namespace}:{reference}:{account}"
    validate_did_pkh(did)
    return did


def split_did_pkh(did: str) -> tuple[str, str, str]:
    """Return (namespace, reference, account) for a valid did:pkh."""
    validate_did_pkh(did)
    _prefix, _pkh, namespace, reference, account = did.split(":")
    return namespace, reference, account
