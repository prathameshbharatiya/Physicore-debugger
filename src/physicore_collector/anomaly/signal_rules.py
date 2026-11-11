"""Deterministic numeric signal rules for robot-specific anomaly detection.

These functions operate on already decoded, provenance-bearing samples. They do not infer units,
expected transitions, or relationships: callers must provide those explicitly. Short events are
retained and labelled rather than silently debounced away.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class SignalPoint:
    timestamp_ns: int
    clock_domain: str
    value: Decimal
    unit: str
    source_ref: str

    def __post_init__(self) -> None:
        if not self.clock_domain or not self.unit or not self.source_ref:
            raise ValueError("clock_domain, unit and source_ref are required")
        if not self.value.is_finite():
            raise ValueError("signal values must be finite")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")


@dataclass(frozen=True)
class SignalEvent:
    rule: str
    start_ns: int
    end_ns: int
    clock_domain: str
    unit: str
    observed: tuple[str, ...]
    source_refs: tuple[str, ...]
    persistent: bool
    explanation: str


@dataclass(frozen=True)
class DeclaredRelationship:
    left_signal: str
    right_signal: str
    left_unit: str
    right_unit: str
    scale: str
    offset: str
    tolerance: str
    max_skew_ns: int


def _ordered(points: Iterable[SignalPoint]) -> list[SignalPoint]:
    values = sorted(points, key=lambda p: (p.timestamp_ns, p.source_ref))
    if len({p.clock_domain for p in values}) > 1:
        raise ValueError("a signal rule cannot compare different clock domains")
    if len({p.unit for p in values}) > 1:
        raise ValueError("a signal rule requires one normalized unit")
    return values


def _decimal(value: str, label: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a decimal string") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    return result


def _event(rule: str, points: list[SignalPoint], explanation: str, *, persistent: bool = True) -> SignalEvent:
    return SignalEvent(rule, points[0].timestamp_ns, points[-1].timestamp_ns, points[0].clock_domain,
                       points[0].unit, tuple(str(p.value) for p in points), tuple(p.source_ref for p in points),
                       persistent, explanation)


def detect_stuck(points: Iterable[SignalPoint], *, epsilon: str, min_duration_ns: int,
                 change_expected: bool) -> list[SignalEvent]:
    """Detect a flat run only when an external expectation says change was expected."""
    if not change_expected:
        return []
    if min_duration_ns <= 0:
        raise ValueError("min_duration_ns must be positive")
    tol = _decimal(epsilon, "epsilon")
    if tol < 0:
        raise ValueError("epsilon must be non-negative")
    seq = _ordered(points)
    out: list[SignalEvent] = []
    start = 0
    for i in range(1, len(seq) + 1):
        if i < len(seq) and abs(seq[i].value - seq[start].value) <= tol:
            continue
        run = seq[start:i]
        if len(run) >= 2 and run[-1].timestamp_ns - run[0].timestamp_ns >= min_duration_ns:
            out.append(_event("stuck", run, f"signal stayed within {tol} {run[0].unit} while change was declared expected"))
        start = i
    return out


def detect_rate_of_change(points: Iterable[SignalPoint], *, max_abs_rate: str) -> list[SignalEvent]:
    """Report adjacent-sample slopes whose magnitude exceeds the configured unit/second threshold."""
    limit = _decimal(max_abs_rate, "max_abs_rate")
    if limit <= 0:
        raise ValueError("max_abs_rate must be positive")
    seq = _ordered(points)
    out = []
    for a, b in zip(seq, seq[1:]):
        dt = b.timestamp_ns - a.timestamp_ns
        if dt <= 0:
            continue
        rate = abs((b.value - a.value) * Decimal(1_000_000_000) / Decimal(dt))
        if rate > limit:
            out.append(_event("rate_of_change", [a, b], f"absolute rate {rate} {a.unit}/s exceeded configured limit {limit}"))
    return out


def detect_discontinuities(points: Iterable[SignalPoint], *, max_delta: str) -> list[SignalEvent]:
    limit = _decimal(max_delta, "max_delta")
    if limit <= 0:
        raise ValueError("max_delta must be positive")
    seq = _ordered(points)
    out = []
    for a, b in zip(seq, seq[1:]):
        delta = abs(b.value - a.value)
        if delta > limit:
            out.append(_event("discontinuity", [a, b], f"absolute step {delta} {a.unit} exceeded configured limit {limit}"))
    return out


def detect_oscillation(points: Iterable[SignalPoint], *, min_turns: int, min_amplitude: str) -> list[SignalEvent]:
    if min_turns < 2:
        raise ValueError("min_turns must be at least 2")
    amplitude = _decimal(min_amplitude, "min_amplitude")
    if amplitude <= 0:
        raise ValueError("min_amplitude must be positive")
    seq = _ordered(points)
    if len(seq) < min_turns + 2:
        return []
    deltas = [b.value - a.value for a, b in zip(seq, seq[1:])]
    signs = [1 if d > 0 else -1 if d < 0 else 0 for d in deltas]
    turns = sum(1 for a, b in zip(signs, signs[1:]) if a and b and a != b)
    swing = max(p.value for p in seq) - min(p.value for p in seq)
    if turns >= min_turns and swing >= amplitude:
        return [_event("oscillation", seq, f"{turns} direction changes across {swing} {seq[0].unit}; thresholds were configured explicitly")]
    return []


def detect_cross_signal_inconsistency(left: Iterable[SignalPoint], right: Iterable[SignalPoint],
                                      relationship: DeclaredRelationship) -> list[SignalEvent]:
    """Compare paired samples only under a declared affine relationship and same clock domain."""
    scale = _decimal(relationship.scale, "relationship.scale")
    offset = _decimal(relationship.offset, "relationship.offset")
    tolerance = _decimal(relationship.tolerance, "relationship.tolerance")
    if tolerance < 0 or relationship.max_skew_ns < 0:
        raise ValueError("tolerance and max_skew_ns must be non-negative")
    aseq, bseq = _ordered(left), _ordered(right)
    if aseq and bseq and aseq[0].clock_domain != bseq[0].clock_domain:
        raise ValueError("cross-signal comparison requires the same clock domain")
    if aseq and aseq[0].unit != relationship.left_unit:
        raise ValueError("left signal unit does not match the declared relationship")
    if bseq and bseq[0].unit != relationship.right_unit:
        raise ValueError("right signal unit does not match the declared relationship")
    out = []
    j = 0
    for a in aseq:
        while j + 1 < len(bseq) and bseq[j + 1].timestamp_ns <= a.timestamp_ns:
            j += 1
        candidates = bseq[max(0, j - 1): min(len(bseq), j + 2)]
        if not candidates:
            continue
        b = min(candidates, key=lambda p: (abs(p.timestamp_ns - a.timestamp_ns), p.source_ref))
        if abs(b.timestamp_ns - a.timestamp_ns) > relationship.max_skew_ns:
            continue
        expected = b.value * scale + offset
        delta = abs(a.value - expected)
        if delta > tolerance:
            event = _event("cross_signal_inconsistency", [a], 
                           f"{relationship.left_signal} differed from declared {relationship.right_signal} relationship by {delta} {a.unit}")
            out.append(SignalEvent(event.rule, event.start_ns, event.end_ns, event.clock_domain, event.unit,
                                   (str(a.value), str(b.value)), (a.source_ref, b.source_ref), True, event.explanation))
    return out


def retain_short_events(events: Iterable[SignalEvent], *, persistence_ns: int) -> tuple[SignalEvent, ...]:
    """Mark short events as non-persistent but retain them; debounce never erases evidence."""
    if persistence_ns < 0:
        raise ValueError("persistence_ns must be non-negative")
    return tuple(SignalEvent(e.rule, e.start_ns, e.end_ns, e.clock_domain, e.unit, e.observed, e.source_refs,
                             e.end_ns - e.start_ns >= persistence_ns, e.explanation) for e in events)
