from decimal import Decimal

from physicore_collector.anomaly.signal_profile import evaluate_signal_profile, validate_signal_profile
from physicore_collector.anomaly.signal_rules import SignalPoint


def test_profile_events_keep_provenance_and_are_sorted_within_clock_domain():
    profile = validate_signal_profile({
        "profile_version": 1, "robot_family": "arm", "persistence_ns": 0,
        "signals": [{"name": "joint", "unit": "rad", "discontinuity_max_delta": "1"}],
    })
    points = [
        SignalPoint(20, "mcap_log_time", Decimal("3"), "rad", "a#2"),
        SignalPoint(10, "mcap_log_time", Decimal("0"), "rad", "a#1"),
    ]
    result = evaluate_signal_profile(profile, {"joint": points})
    assert result.events[0].event.source_refs == ("a#1", "a#2")
    assert result.events[0].event.start_ns == 10
    assert result.events[0].event.end_ns == 20


def test_profile_missing_signals_are_explicit():
    profile = validate_signal_profile({
        "profile_version": 1, "robot_family": "arm", "persistence_ns": 0,
        "signals": [{"name": "joint", "unit": "rad", "discontinuity_max_delta": "1"}],
    })
    result = evaluate_signal_profile(profile, {})
    assert result.missing_signals == ("joint",)
    assert result.events == ()
