"""Onset intervals and strict interval order (DESIGN 4.2, FR-INT-01..03). Integer nanoseconds only, one clock domain per interval, never converted.
`X precedes Y` iff X.upper < Y.lower. Touching or equal bounds are unordered. The relation is an interval order: irreflexive, asymmetric, transitive."""
from __future__ import annotations

from dataclasses import dataclass

from ..anomaly.clock import ClockDomainError

__all__ = ["Interval", "ClockDomainError", "BEFORE", "AFTER", "UNORDERED"]

BEFORE, AFTER, UNORDERED = "before", "after", "unordered"


def _is_int(v: object) -> bool:
    return type(v) is int                       # excludes bool, float, numpy ints, str


@dataclass(frozen=True)
class Interval:
    domain: str
    lower_ns: int
    upper_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.domain, str) or not self.domain:
            raise ValueError("an interval needs a clock domain")
        if not (_is_int(self.lower_ns) and _is_int(self.upper_ns)):
            raise TypeError("interval bounds must be exact integers (nanoseconds)")
        if self.lower_ns > self.upper_ns:
            raise ValueError(f"interval lower bound {self.lower_ns} exceeds upper bound {self.upper_ns}")

    @classmethod
    def point(cls, domain: str, ns: int) -> "Interval":
        return cls(domain, ns, ns)

    def _same(self, other: "Interval") -> None:
        if not isinstance(other, Interval):
            raise TypeError("only Interval instances can be related")
        if other.domain != self.domain:
            raise ClockDomainError(f"refusing to relate '{self.domain}' with '{other.domain}': different clock domains are never compared")

    @property
    def width_ns(self) -> int:
        return self.upper_ns - self.lower_ns

    def precedes(self, other: "Interval") -> bool:
        """Strict: every possible instant of self is earlier than every possible instant of other."""
        self._same(other)
        return self.upper_ns < other.lower_ns

    def order(self, other: "Interval") -> str:
        """`before` (self precedes other), `after` (other precedes self) or `unordered`."""
        self._same(other)
        if self.upper_ns < other.lower_ns:
            return BEFORE
        if other.upper_ns < self.lower_ns:
            return AFTER
        return UNORDERED

    def unordered_with(self, other: "Interval") -> bool:
        return self.order(other) == UNORDERED

    def min_separation(self, other: "Interval") -> int:
        """Smallest possible `other - self` (may be <= 0 when the intervals are not strictly ordered)."""
        self._same(other)
        return other.lower_ns - self.upper_ns

    def max_separation(self, other: "Interval") -> int:
        """Largest possible `other - self`."""
        self._same(other)
        return other.upper_ns - self.lower_ns
