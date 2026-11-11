from decimal import Decimal

import pytest

from physicore_collector.anomaly.signal_profile import evaluate_signal_profile, validate_signal_profile
from physicore_collector.anomaly.signal_rules import SignalPoint


def profile():
    return validate_signal_profile({
        "profile_version": 1, "robot_family": "mobile_base_v1", "persistence_ns": 2_000_000_000,
        "signals": [
            {"name": "wheel_speed", "unit": "rad/s", "rate_max": "2.0", "stuck_epsilon": "0.01",
             "stuck_min_duration_ns": 2_000_000_000, "change_expected": True,
             "discontinuity_max_delta": "3.0", "oscillation_min_turns": 2, "oscillation_min_amplitude": "1.0"},
            {"name": "odom_speed", "unit": "rad/s", "rate_max": "3.0"},
        ],
        "relationships": [{"left_signal": "wheel_speed", "right_signal": "odom_speed", "left_unit": "rad/s",
            "right_unit": "rad/s", "scale": "1", "offset": "0", "tolerance": "0.2", "max_skew_ns": 0}],
        "expected_transitions": [{"start_ns": 0, "end_ns": 1_000_000_000, "clock_domain": "mcap_log_time", "label": "startup"}],
    })


def pts(values, name):
    return [SignalPoint(i * 1_000_000_000, "mcap_log_time", Decimal(str(v)), "rad/s", f"{name}:{i}") for i, v in enumerate(values)]


def test_profile_evaluates_all_numeric_rules_and_relationships_with_provenance():
    p = profile()
    result = evaluate_signal_profile(p, {"wheel_speed": pts([0, 0, 4, 0, 4, 0], "wheel"),
                                         "odom_speed": pts([0, 0, 0, 0, 0, 0], "odom")})
    assert result.robot_family == "mobile_base_v1"
    assert result.signals_evaluated == ("odom_speed", "wheel_speed")
    assert result.events
    assert all(e.event.source_refs for e in result.events)
    assert any(e.expected_transition_overlap == "startup" for e in result.events)


def test_profile_preserves_short_events_as_nonpersistent():
    p = validate_signal_profile({"profile_version": 1, "robot_family": "arm", "persistence_ns": 10,
        "signals": [{"name": "joint", "unit": "rad", "discontinuity_max_delta": "1"}]})
    result = evaluate_signal_profile(p, {"joint": [SignalPoint(0, "mcap_log_time", Decimal("0"), "rad", "j:0"),
                                         SignalPoint(1_000_000_000, "mcap_log_time", Decimal("5"), "rad", "j:1")]})
    assert len(result.events) == 1 and result.events[0].event.persistent is False


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(extra=True),
    lambda x: x.update(profile_version=True),
    lambda x: x.update(persistence_ns=-1),
])
def test_profile_rejects_unknown_or_invalid_top_level_values(mutate):
    raw = {"profile_version": 1, "robot_family": "arm", "persistence_ns": 0,
           "signals": [{"name": "joint", "unit": "rad", "discontinuity_max_delta": "1"}]}
    mutate(raw)
    with pytest.raises(ValueError):
        validate_signal_profile(raw)


def test_profile_rejects_missing_units_bad_relationship_and_implicit_stuck_expectation():
    with pytest.raises(ValueError, match="unit"):
        validate_signal_profile({"profile_version": 1, "robot_family": "arm", "persistence_ns": 0,
                                  "signals": [{"name": "joint", "discontinuity_max_delta": "1"}]})
    with pytest.raises(ValueError, match="change_expected"):
        validate_signal_profile({"profile_version": 1, "robot_family": "arm", "persistence_ns": 0,
                                  "signals": [{"name": "joint", "unit": "rad", "stuck_epsilon": "0",
                                               "stuck_min_duration_ns": 10}]})
    p = profile()
    with pytest.raises(ValueError, match="unit"):
        evaluate_signal_profile(p, {"wheel_speed": [SignalPoint(0, "mcap_log_time", Decimal("1"), "m", "x")]})
