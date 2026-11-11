import pytest

from physicore_collector.cadence import MAX_FINDINGS_PER_TOPIC, analyze_cadence, threshold_ns


def test_threshold_ns_rounds_not_truncates():
    assert threshold_ns(0.04) == 40_000_000
    assert threshold_ns(1 / 3) == 333_333_333
    with pytest.raises(ValueError):
        threshold_ns(0)


def test_no_gap():
    a = analyze_cadence("/t", [0, 10, 20, 30], 1e-8)
    assert a.evaluable and a.gap_count == 0 and a.findings == ()
    assert a.max_gap_seconds == pytest.approx(1e-8)


def test_gap_strictly_greater_than_threshold_is_a_violation_equal_is_not():
    assert analyze_cadence("/t", [0, 40_000_000], 0.04).gap_count == 0
    assert analyze_cadence("/t", [0, 40_000_001], 0.04).gap_count == 1


def test_single_gap_reported_with_exact_bounds():
    a = analyze_cadence("/t", [0, 20_000_000, 540_000_000, 560_000_000], 0.04)
    assert a.gap_count == 1
    f = a.findings[0]
    assert (f.gap_start_log_time_ns, f.gap_end_log_time_ns) == (20_000_000, 540_000_000)
    assert f.observed_gap_seconds == 0.52 and f.threshold_seconds == 0.04
    assert f.kind == "observed_log_time_gap" and "not proof of DDS message loss" in f.interpretation


def test_input_order_does_not_matter():
    assert analyze_cadence("/t", [30, 0, 20, 10], 5e-9).gap_count == analyze_cadence("/t", [0, 10, 20, 30], 5e-9).gap_count == 3


@pytest.mark.parametrize("times", [[], [5]])
def test_fewer_than_two_messages_is_not_evaluable(times):
    a = analyze_cadence("/t", times, 0.1)
    assert not a.evaluable and a.gap_count == 0 and a.max_gap_seconds is None


def test_duplicate_timestamps_are_zero_gaps():
    a = analyze_cadence("/t", [5, 5, 5], 0.001)
    assert a.evaluable and a.gap_count == 0


def test_findings_are_largest_first_and_capped_but_total_is_exact():
    times = [0]
    for i in range(1, MAX_FINDINGS_PER_TOPIC + 51):
        times.append(times[-1] + 1_000_000_000 + i)  # every gap exceeds 0.5 s, growing
    a = analyze_cadence("/t", times, 0.5)
    assert a.gap_count == MAX_FINDINGS_PER_TOPIC + 50
    assert len(a.findings) == MAX_FINDINGS_PER_TOPIC
    sizes = [f.observed_gap_seconds for f in a.findings]
    assert sizes == sorted(sizes, reverse=True)


def test_edges_are_not_evaluated():
    # recording could start long before the first message; that silence is not a finding
    assert analyze_cadence("/t", [10**12, 10**12 + 10], 0.001).gap_count == 0


def test_deterministic():
    t = [0, 1_000_000_000, 5_000_000_000]
    assert analyze_cadence("/t", t, 0.5) == analyze_cadence("/t", t, 0.5)
