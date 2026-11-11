"""Integer-only formatting (FR-VW-07): no float, no true division anywhere in this package (a test scans the syntax trees)."""
from __future__ import annotations


def _int(n: object) -> int:
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("an integer is required")
    return n


def ns(n: int) -> str:
    return f"{_int(n)} ns"


def offset_text(off: int) -> str:
    """A non-negative nanosecond offset as milliseconds with exactly six decimals ('+2.000000 ms'), by integer division."""
    off = _int(off)
    if off < 0:
        raise ValueError("offsets are non-negative by construction")
    ms, rem = divmod(off, 1_000_000)
    return f"+{ms}.{rem:06d} ms"


def interval(lo: int, hi: int) -> str:
    lo, hi = _int(lo), _int(hi)
    return f"{lo} ns" if lo == hi else f"{lo} to {hi} ns"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def x_pos(t: int, origin: int, span: int, width: int) -> int:
    """Integer pixel position of time t on an axis of `width` units covering [origin, origin + span]; the exact rounded integer quotient."""
    if span <= 0:
        return 0
    return ((t - origin) * width * 2 + span) // (span * 2)


LIST_CAP = 20


def capjoin(parts, sep: str = ", ", none: str = "none") -> str:
    """Join already formatted parts; more than LIST_CAP are cut with an exact count ('... and N more'); empty gives `none`."""
    parts = list(parts)
    if not parts:
        return none
    if len(parts) <= LIST_CAP:
        return sep.join(parts)
    return sep.join(parts[:LIST_CAP]) + f" ... and {len(parts) - LIST_CAP} more"
