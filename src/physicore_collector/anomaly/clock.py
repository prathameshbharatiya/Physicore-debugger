"""Clock domains for M3c. Every timestamp is a (value_ns, domain) pair; values of different domains are never compared,
subtracted or converted. There is deliberately no function that maps one domain onto another."""
from __future__ import annotations

from dataclasses import dataclass

LOG_TIME_DOMAIN = "mcap_log_time"  # recorder receipt/log time; NOT physical event time


def payload_stamp_domain(topic: str, field: str) -> str:
    """Domain of a payload Time field, named exactly as the accepted engine names it."""
    return f"payload_stamp:{topic}:{field}"


class ClockDomainError(ValueError):
    """Raised on any attempt to relate two values from different clock domains."""


@dataclass(frozen=True)
class ClockedValue:
    value_ns: int
    domain: str

    def __post_init__(self) -> None:
        if type(self.value_ns) is not int:
            raise TypeError("value_ns must be an int (exact nanoseconds)")
        if not self.domain:
            raise ValueError("a clocked value needs a clock domain")

    def _check(self, other: "ClockedValue") -> None:
        if not isinstance(other, ClockedValue):
            raise TypeError("only ClockedValue instances can be related")
        if other.domain != self.domain:
            raise ClockDomainError(f"refusing to relate '{self.domain}' with '{other.domain}': different clock domains are never compared")

    def delta_ns(self, earlier: "ClockedValue") -> int:
        """self - earlier, in nanoseconds, within one domain only."""
        self._check(earlier)
        return self.value_ns - earlier.value_ns

    def before(self, other: "ClockedValue") -> bool:
        self._check(other)
        return self.value_ns < other.value_ns

    def same_instant(self, other: "ClockedValue") -> bool:
        self._check(other)
        return self.value_ns == other.value_ns

    # ordering operators are intentionally not defined: use before()/same_instant()/delta_ns(), which check the domain.
