"""Identity classification (DESIGN 3.0.1, Revision 2). Severity never depends on identity; this module only says what is known about
WHO an observation belongs to. States: known | empty | absent | malformed. Unknown identity = empty, absent or malformed."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .access import plain, strict_int

KNOWN, EMPTY, ABSENT, MALFORMED = "known", "empty", "absent", "malformed"
UNKNOWN_STATES = (EMPTY, ABSENT, MALFORMED)


def classify_text(found: bool, value: Any) -> str:
    """A string identity field (logger name, diagnostic name/hardware_id, goal id, battery serial/location)."""
    if not found:
        return ABSENT
    if not isinstance(value, str):
        return MALFORMED            # includes JSON null, numbers, bool, lists
    return EMPTY if value == "" else KNOWN


def classify_hardware_id(found: bool, value: Any) -> str:
    """Diagnostics hardware_id: an empty string is a KNOWN identity component (name, "") when the name is non-empty (DESIGN 3.0.1)."""
    if not found:
        return ABSENT
    return KNOWN if isinstance(value, str) else MALFORMED


def classify_uuid(found: bool, value: Any) -> str:
    """16-byte uuid (action_msgs goal_id.uuid). All-zero is EMPTY (a grouping decision only). Accepts bytes or a list of 16 ints 0..255."""
    if not found:
        return ABSENT
    if isinstance(value, (bytes, bytearray)):
        octets = list(value)
    elif isinstance(value, (list, tuple)) or type(value).__name__ in ("array", "ndarray"):
        raw = [strict_int(o.item() if hasattr(o, "item") and not isinstance(o, (int, Decimal)) else o) for o in value]
        if any(o is None for o in raw):
            return MALFORMED
        octets = raw
        if any(not 0 <= o <= 255 for o in octets):
            return MALFORMED
    else:
        return MALFORMED
    if len(octets) != 16:
        return MALFORMED
    return EMPTY if not any(octets) else KNOWN


def render_raw(state: str, value: Any, max_bytes: int) -> str:
    """Original identity evidence, rendered as text (type name + value), capped."""
    if state == ABSENT:
        s = "<absent>"
    elif state == EMPTY and value in ("", b"", [], ()):
        s = '""'
    elif state == EMPTY:
        s = f"all-zero {type(value).__name__}: {value!r}"
    elif isinstance(value, Decimal):
        s = f"number: {value}"
    elif isinstance(value, (list, tuple)):
        s = f"list: {plain(value)!r}"
    else:
        s = f"{type(value).__name__}: {value!r}"
    b = s.encode("utf-8")
    return s if len(b) <= max_bytes else b[:max_bytes].decode("utf-8", "ignore")


def truncate_text(text: str, max_bytes: int) -> tuple[str, int | None]:
    """(text capped to max_bytes of UTF-8 on a character boundary, original byte length if it was cut else None). Never parsed."""
    b = text.encode("utf-8", "surrogatepass") if text else b""
    if len(b) <= max_bytes:
        return text, None
    return b[:max_bytes].decode("utf-8", "ignore"), len(b)
