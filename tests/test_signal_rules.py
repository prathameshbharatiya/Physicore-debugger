from decimal import Decimal

import pytest

from physicore_collector.anomaly.signal_rules import (
    DeclaredRelationship,
    SignalPoint,
    detect_cross_signal_inconsistency,
    detect_discontinuities,
    detect_oscillation,
    detect_rate_of_change,
    detect_stuck,
    retain_short_events,
)


def points(values, *, unit="rad", domain="mcap_log_time"):
    return [SignalPoint(i * 1_000_000_000, domain, Decimal(str(value)), unit, f"sample-{i}") for i, value in enumerate(values)]


def test_stuck_detection_requires_an_explicit_expectation_of_change():
    seq = points([1, 1, 1, 1])
    assert detect_stuck(seq, epsilon="0", min_duration_ns=2_000_000_000, change_expected=False) == []
    events = detect_stuck(seq, epsilon="0.001", min_duration_ns=2_000_000_000, change_expected=True)
    assert len(events) == 1
    assert events[0].rule == "stuck"
    assert events[0].source_refs == ("sample-0", "sample-1", "sample-2", "sample-3")


def test_rate_and_discontinuity_rules_use_explicit_thresholds():
    seq = points([0, 1, 5])
    assert len(detect_rate_of_change(seq, max_abs_rate="2")) == 1
    assert len(detect_discontinuities(seq, max_delta="2")) == 1


def test_oscillation_requires_configured_turn_count_and_amplitude():
    seq = points([0, 2, 0, 2, 0, 2])
    assert len(detect_oscillation(seq, min_turns=3, min_amplitude="1")) == 1
    assert detect_oscillation(points([0, 1, 2]), min_turns=2, min_amplitude="1") == []


def test_cross_signal_comparison_requires_declared_units_and_clock_compatibility():
    left = points([2, 5], unit="m")
    right = points([1, 2], unit="m")
    relation = DeclaredRelationship("left", "right", "m", "m", "2", "0", "0.5", 0)
    events = detect_cross_signal_inconsistency(left, right, relation)
    assert len(events) == 1
    assert events[0].source_refs == ("sample-1", "sample-1")

    with pytest.raises(ValueError, match="same clock domain"):
        detect_cross_signal_inconsistency(left, points([1], domain="payload_stamp:/x:stamp"), relation)


def test_short_events_are_retained_and_marked_non_persistent():
    raw = detect_discontinuities(points([0, 3]), max_delta="1")
    kept = retain_short_events(raw, persistence_ns=5_000_000_000)
    assert len(kept) == 1
    assert kept[0].persistent is False
    assert kept[0].source_refs == raw[0].source_refs


def test_signal_rules_reject_mixed_units_and_non_positive_thresholds():
    with pytest.raises(ValueError, match="one normalized unit"):
        detect_discontinuities(points([0], unit="m") + points([1], unit="rad"), max_delta="1")
    with pytest.raises(ValueError, match="positive"):
        detect_rate_of_change(points([0, 1]), max_abs_rate="0")
