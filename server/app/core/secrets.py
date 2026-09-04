"""Secret references — keep credential material out of the ledger database.

The ledger's merchant webhook signing secret is credential material. It must
never live in the database in a form an attacker who reads the DB can use.
This module defines the indirection used everywhere a secret is stored:

  * a database column holds a REFERENCE, not the material;
  * the reference is resolved at use time from an external source;
  * unresolvable references fail closed (resolve to nothing).

Reference syntax (the stored value)::

    secretref:env:<NAME>

``<NAME>`` is the name of an environment variable (or ``.env`` entry) that
holds the material. The variable is read from the running process's
environment at resolution time — deployments manage it with their normal
secret tooling (systemd ``Environment=``, ``.env``, a vault agent that
injects env, a KMS-backed env shim), and the database never sees the value.

Anything that is not a ``secretref:`` URI is treated as a LITERAL value.
Literals are the v0.1 local-development behaviour and remain accepted for
back-compatibility while deployments migrate, but they are the thing this
module exists to remove:

  * a value that starts with the ``secretref:`` scheme but is not a valid
    env reference (malformed name, or an as-yet-unknown scheme such as a
    future ``secretref:kms:``) resolves to nothing — it is never treated as
    a literal;
  * in STRICT MODE (``CRUMBS_ENFORCE_SECRET_REFS=true``) a literal stored on
    a non-local database resolves to nothing too, so signature verification
    fails closed instead of authenticating with database-resident material.

Nothing in this module ever logs, echoes, or returns secret material to a
caller that is not the verifier itself; ``resolve_secret`` returns the value
only to the code that immediately uses it for a cryptographic comparison.

Production upgrade path (documented in docs/SECRET_MANAGEMENT.md): point the
same reference syntax at a KMS/encrypted-vault-backed resolver — callers and
stored values do not change.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

# Prefix for environment-backed references stored in database columns.
SECRET_REF_PREFIX = "secretref:env:"
# Any value carrying this scheme prefix is a reference of SOME kind; one that
# is not a well-formed env reference must fail closed, never be treated as a
# literal (a future `secretref:kms:...` scheme must not silently authenticate
# as literal material).
SECRETREF_SCHEME = "secretref:"

# Environment variable names: letter/underscore first, then letters, digits,
# underscores (POSIX portable). Anything else is not a valid reference.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_secret_ref(value: object) -> bool:
    """True when ``value`` is a well-formed ``secretref:env:`` reference."""
    return secret_ref_env_name(value) is not None


def secret_ref_env_name(value: object) -> str | None:
    """The environment variable name behind a reference, or None.

    None means "not a valid reference": either not a string, not prefixed,
    or prefixed with an empty/invalid name. A prefixed-but-invalid value is
    deliberately NOT treated as a literal by ``resolve_secret`` (fail
    closed — see module docstring).
    """
    if not isinstance(value, str) or not value.startswith(SECRET_REF_PREFIX):
        return None
    name = value[len(SECRET_REF_PREFIX):]
    if not name or not _ENV_NAME_RE.match(name):
        return None
    return name


def make_env_ref(env_var_name: str) -> str:
    """Build a ``secretref:env:<NAME>`` reference for ``env_var_name``.

    Raises ValueError when the name is not a valid environment variable
    name (POSIX portable subset). The value must be placed in the service
    environment by the operator BEFORE the reference is stored.
    """
    if not isinstance(env_var_name, str) or not _ENV_NAME_RE.match(env_var_name):
        raise ValueError(
            f"invalid environment variable name for a secret reference: {env_var_name!r}"
        )
    return SECRET_REF_PREFIX + env_var_name


def resolve_secret(
    stored: object,
    *,
    env: Mapping[str, str] | None = None,
    enforce_refs: bool = False,
    literal_ok: bool = False,
) -> str | None:
    """Resolve stored credential material to its live value, or None.

    ``stored``   — the database column value (a ``secretref:env:`` reference
                   or, in local dev, a literal).
    ``env``      — mapping used for environment lookups. Defaults to
                   ``os.environ`` (the running process's environment).
    ``enforce_refs`` — strict mode: when True, literal values resolve to
                   None unless ``literal_ok`` is also True.
    ``literal_ok``   — True ONLY for the local SQLite development database
                   (see webhooks.process_order_webhook).

    Resolution rules:

      * empty/None stored value          -> None (no secret configured)
      * valid ``secretref:env:<NAME>``   -> value of env var ``<NAME>``,
                                            or None when the variable is
                                            unset/empty (fail closed)
      * prefixed but malformed reference -> None (never a literal)
      * literal, strict mode, not local  -> None (fail closed)
      * literal otherwise                -> the literal itself
    """
    if not stored:
        return None
    name = secret_ref_env_name(stored)
    if name is not None:
        source = env if env is not None else os.environ
        value = source.get(name)
        return value if value else None
    if isinstance(stored, str) and stored.startswith(SECRETREF_SCHEME):
        # Prefixed but not a valid env reference (malformed name or an
        # unknown scheme) — fail closed, never a literal.
        return None
    if enforce_refs and not literal_ok:
        return None
    return stored if isinstance(stored, str) else None
