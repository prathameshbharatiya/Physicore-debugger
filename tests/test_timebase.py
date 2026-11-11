import pytest

from physicore_collector.schemas import (
    ArtifactReadability as AR, BasisCompatibility, RecordingWindow, TimeBase as TB, Trigger,
    TriggerComparisonResult as R,
)
from physicore_collector.timebase import compare_trigger

W = RecordingWindow(min_log_time_ns=1000, max_log_time_ns=2000)
OK = BasisCompatibility(compatible_with_log_time=True, established_by="same host clock")
NO = BasisCompatibility(compatible_with_log_time=False, established_by="different simulator")


def trig(basis=TB.SYSTEM_TIME, ts=1500, compat=OK):
    return Trigger(trigger_id="t", timestamp_ns=ts, timestamp_basis=basis, basis_compatibility=compat)


def cmp(t, profile_basis=TB.SYSTEM_TIME, window=W, readability=AR.READABLE):
    return compare_trigger(t, profile_basis, window, readability).result


def test_unknown_trigger_basis_is_unavailable_even_if_attested():
    assert cmp(trig(TB.UNKNOWN)) is R.UNAVAILABLE


def test_unknown_profile_basis_is_unavailable_even_if_labels_and_attestation_present():
    assert cmp(trig(), profile_basis=TB.UNKNOWN) is R.UNAVAILABLE


def test_explicitly_incompatible_is_unavailable():
    r = compare_trigger(trig(compat=NO), TB.SYSTEM_TIME, W, AR.READABLE)
    assert r.result is R.UNAVAILABLE and "different simulator" in r.reason


def test_matching_labels_without_attestation_is_inconclusive_never_compared():
    # trigger is inside the window, but the clocks are not shown to be the same clock
    r = compare_trigger(trig(compat=None, ts=1500), TB.SYSTEM_TIME, W, AR.READABLE)
    assert r.result is R.INCONCLUSIVE and "matching labels do not prove" in r.reason


@pytest.mark.parametrize("ts", [0, 1500, 10**9])
def test_inconclusive_result_is_independent_of_timestamp_value(ts):
    assert cmp(trig(compat=None, ts=ts)) is R.INCONCLUSIVE


def test_sim_time_labels_matching_without_attestation_still_inconclusive():
    assert cmp(trig(TB.SIM_TIME, compat=None), profile_basis=TB.SIM_TIME) is R.INCONCLUSIVE


def test_differing_labels_are_inconclusive_even_when_attested_compatible():
    assert cmp(trig(TB.ROS_TIME), profile_basis=TB.SYSTEM_TIME) is R.INCONCLUSIVE


def test_differing_labels_without_attestation_inconclusive():
    assert cmp(trig(TB.ROS_TIME, compat=None), profile_basis=TB.SYSTEM_TIME) is R.INCONCLUSIVE


def test_no_window_is_unavailable():
    assert cmp(trig(), window=None) is R.UNAVAILABLE


@pytest.mark.parametrize("ts,expected", [
    (999, R.BEFORE_RECORDING_WINDOW), (1000, R.WITHIN_RECORDING_WINDOW),
    (1500, R.WITHIN_RECORDING_WINDOW), (2000, R.WITHIN_RECORDING_WINDOW), (2001, R.AFTER_RECORDING_WINDOW),
])
def test_established_compatibility_compares_inclusively(ts, expected):
    assert cmp(trig(ts=ts)) is expected


def test_after_window_in_truncated_recording_is_inconclusive():
    assert cmp(trig(ts=5000), readability=AR.PARTIALLY_READABLE) is R.INCONCLUSIVE


def test_before_and_within_still_decidable_in_truncated_recording():
    assert cmp(trig(ts=999), readability=AR.PARTIALLY_READABLE) is R.BEFORE_RECORDING_WINDOW
    assert cmp(trig(ts=1500), readability=AR.PARTIALLY_READABLE) is R.WITHIN_RECORDING_WINDOW


def test_reason_records_the_stated_source_of_compatibility():
    assert "same host clock" in compare_trigger(trig(), TB.SYSTEM_TIME, W, AR.READABLE).reason
