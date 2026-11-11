"""Cadence analysis: gaps between consecutive recorded log_time values.

IMPORTANT INTERPRETATION RULE: a gap here is an *observed spacing in the recorded
log_time sequence*. It is timing evidence. It is NOT proof of DDS (or any) message loss:
a publisher may have stalled, the source may be slow, or the recorder may have been
back-pressured. Conversely, finding no gap proves nothing about loss.

Rules:
  * All arithmetic is integer nanoseconds. threshold_ns = round(threshold_seconds * 1e9).
  * A gap is a violation iff (t[i+1] - t[i]) is STRICTLY greater than threshold_ns.
    A gap exactly equal to the threshold is not a violation.
  * Input times are sorted first, so the result does not depend on file order.
  * Silence before the first / after the last message of a topic is NOT evaluated.
  * With fewer than 2 messages, cadence cannot be evaluated (`evaluable` is False).
  * At most MAX_FINDINGS_PER_TOPIC findings (the largest gaps) are retained; the true
    total is always reported in `gap_count`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .schemas import CadenceFinding

NS_PER_SECOND = 1_000_000_000
MAX_FINDINGS_PER_TOPIC = 100


@dataclass(frozen=True)
class CadenceAnalysis:
    topic: str
    threshold_seconds: float
    message_count: int
    evaluable: bool
    gap_count: int
    max_gap_seconds: float | None
    findings: tuple[CadenceFinding, ...]  # largest gaps first, capped


def threshold_ns(threshold_seconds: float) -> int:
    if threshold_seconds <= 0:
        raise ValueError("threshold_seconds must be positive")
    return round(threshold_seconds * NS_PER_SECOND)


def analyze_cadence(
    topic: str, log_times_ns: Sequence[int], threshold_seconds: float
) -> CadenceAnalysis:
    limit = threshold_ns(threshold_seconds)
    times = sorted(log_times_ns)
    n = len(times)
    if n < 2:
        return CadenceAnalysis(topic, threshold_seconds, n, False, 0, None, ())

    gaps: list[tuple[int, int, int]] = []  # (gap_ns, start_ns, end_ns)
    max_gap = 0
    for a, b in zip(times, times[1:]):
        g = b - a
        max_gap = max(max_gap, g)
        if g > limit:
            gaps.append((g, a, b))
    # largest first; ties broken by earliest start for determinism
    gaps.sort(key=lambda x: (-x[0], x[1]))
    findings = tuple(
        CadenceFinding(
            topic=topic,
            threshold_seconds=threshold_seconds,
            observed_gap_seconds=g / NS_PER_SECOND,
            gap_start_log_time_ns=a,
            gap_end_log_time_ns=b,
        )
        for g, a, b in gaps[:MAX_FINDINGS_PER_TOPIC]
    )
    return CadenceAnalysis(
        topic, threshold_seconds, n, True, len(gaps), max_gap / NS_PER_SECOND, findings
    )
