"""RFC 8785 JSON Canonicalization Scheme (JCS) — receipt subset.

The Crumbs receipt is a FLAT object of string/int values, which is exactly the
subset where a faithful JCS implementation is small:

  * keys sorted lexicographically by UTF-16 code units (ASCII keys == byte order)
  * strings escaped per RFC 8785 s3.2.2.2.2.1: only `"`, `\\`, and control chars
    (U+0000..U+001F as lowercase \\uXXXX); all other code points emitted literally
    (UTF-8 encoded output)
  * ints serialized as decimal (no exponent, no leading zeros) — Python ints do
    this natively

Full RFC 8785 (nested structures, number handling per IEEE 754) is out of scope
for v0.1 — the receipt is flat by design (see spec A.2).
"""
from __future__ import annotations

import json

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape_str(s: str) -> str:
    out = ['"']
    for ch in s:
        esc = _ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))  # lowercase hex per RFC 8785
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonical_json(obj: dict) -> str:
    """Serialize a flat dict to JCS-canonical bytes (as str)."""
    parts = []
    for key in sorted(obj.keys()):
        value = obj[key]
        if isinstance(value, str):
            rendered = _escape_str(value)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int):
            rendered = str(value)
        else:
            raise TypeError(f"JCS receipt subset supports str/int/bool only, got {type(value)!r}")
        parts.append(_escape_str(key) + ":" + rendered)
    return "{" + ",".join(parts) + "}"


def json_roundtrip(obj: dict) -> dict:
    """Validate the payload round-trips through json (defensive; used in signing)."""
    return json.loads(json.dumps(obj))
