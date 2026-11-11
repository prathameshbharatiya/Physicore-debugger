import json

import pytest
import yaml

from physicore_collector.mcap_reader import scan_recording
from physicore_collector.profile import load_profile
from physicore_collector.schemas import (
    ArtifactReadability as AR, BasisCompatibility, CheckOutcome as O, RobotProfile, SessionStatus as S,
    TimeBase, Trigger, TriggerComparisonResult as TR,
)
from physicore_collector.validation import validate_recording
from tests import make_fixtures as mf


def report(scan_path, profile_path, **kw):
    scan = scan_recording(scan_path)
    return validate_recording(scan, load_profile(profile_path).profile, **kw)


def checks(rep):
    return {c.check_id: c for c in rep.checks}


def make_profile(**over):
    d = {"profile_schema_version": "1.0", "profile_id": "p", "log_time_basis": "system_time",
         "topics": [{"name": "/joint_states", "expected_hz": 50}]}
    d.update(over)
    return RobotProfile.model_validate(d)


def scan_of(tmp_path, streams, **kw):
    p = tmp_path / "x.mcap"
    p.write_bytes(mf.build_mcap(streams, **kw))
    return scan_recording(p)


# ---- the five required scenarios ----
def test_valid_recording_is_complete(fx, synth_profile):
    r = report(fx / "valid.mcap", synth_profile)
    assert r.status is S.COMPLETE
    assert all(c.outcome in (O.PASS, O.NOT_APPLICABLE) for c in r.checks)
    assert r.cadence_findings == [] and r.trigger_comparison is None


def test_cadence_gap_recording_reports_timing_evidence_and_is_partial(fx, synth_profile):
    r = report(fx / "cadence_gap.mcap", synth_profile)
    assert r.status is S.PARTIAL
    c = checks(r)["cadence:/joint_states"]
    assert c.outcome is O.FAIL and c.required
    assert "timing evidence only, not proof of message loss" in c.message
    assert len(r.cadence_findings) == 1
    f = r.cadence_findings[0]
    assert f.observed_gap_seconds == pytest.approx(0.52) and f.threshold_seconds == pytest.approx(0.04)
    assert f.gap_start_log_time_ns == mf.BASE_NS + 24 * 20_000_000
    assert f.gap_end_log_time_ns == mf.BASE_NS + 50 * 20_000_000
    # other topics unaffected
    assert checks(r)["cadence:/camera/image"].outcome is O.PASS
    for banned in ("dropped", "lost messages", "DDS", "data loss detected"):
        assert banned not in c.message  # no causal claim beyond observed spacing


def test_missing_required_topic_is_partial(fx, synth_profile):
    r = report(fx / "missing_topic.mcap", synth_profile)
    assert r.status is S.PARTIAL
    c = checks(r)
    assert c["topic_present:/camera/image"].outcome is O.FAIL and c["topic_present:/camera/image"].required
    assert c["cadence:/camera/image"].outcome is O.NOT_APPLICABLE
    assert c["topic_present:/joint_states"].outcome is O.PASS


def test_truncated_recording_is_partial_never_complete(fx, synth_profile):
    r = report(fx / "truncated.mcap", synth_profile)
    assert r.status is S.PARTIAL and r.artifact_readability is AR.PARTIALLY_READABLE
    assert checks(r)["readability"].outcome is O.FAIL
    assert r.read_errors


def test_malformed_recording_is_failed(fx, synth_profile):
    r = report(fx / "not_mcap.mcap", synth_profile)
    assert r.status is S.FAILED and r.artifact_readability is AR.UNREADABLE
    assert [c.check_id for c in r.checks] == ["readability"]  # nothing else can be assessed


# ---- trigger / time basis ----
def trig(name, fx):
    return Trigger.model_validate_json((fx / "triggers" / name).read_text())


@pytest.mark.parametrize("name,expected_result", [
    ("trigger_unknown_basis.json", TR.UNAVAILABLE),
    ("trigger_labels_match_unattested.json", TR.INCONCLUSIVE),
    ("trigger_declared_incompatible.json", TR.UNAVAILABLE),
])
def test_unestablished_time_basis_makes_session_partial_and_comparison_not_a_pass(fx, synth_profile, name, expected_result):
    r = report(fx / "valid.mcap", synth_profile, trigger=trig(name, fx))
    assert r.trigger_comparison.result is expected_result
    c = checks(r)["trigger:coverage"]
    assert c.outcome is O.INCONCLUSIVE and c.required
    assert c.unknowns
    assert r.status is S.PARTIAL


def test_attested_trigger_within_window_gives_complete(fx, synth_profile):
    r = report(fx / "valid.mcap", synth_profile, trigger=trig("trigger_attested_within.json", fx))
    assert r.trigger_comparison.result is TR.WITHIN_RECORDING_WINDOW
    assert r.status is S.COMPLETE


def test_attested_trigger_after_window_fails_and_is_partial(fx, synth_profile):
    r = report(fx / "valid.mcap", synth_profile, trigger=trig("trigger_attested_after.json", fx))
    assert r.trigger_comparison.result is TR.AFTER_RECORDING_WINDOW
    assert checks(r)["trigger:coverage"].outcome is O.FAIL and r.status is S.PARTIAL


def test_profile_with_unknown_log_time_basis_makes_even_attested_trigger_unavailable(fx, example_profile):
    r = report(fx / "valid.mcap", example_profile, trigger=trig("trigger_attested_within.json", fx))
    assert r.trigger_comparison.result is TR.UNAVAILABLE and r.status is S.PARTIAL


def test_no_trigger_means_no_trigger_check(fx, synth_profile):
    assert "trigger:coverage" not in checks(report(fx / "valid.mcap", synth_profile))


# ---- required vs optional topics ----
def test_optional_topic_absent_is_warning_only_and_does_not_block_complete(tmp_path):
    prof = make_profile(topics=[{"name": "/joint_states"}, {"name": "/opt", "required": False}])
    rep = validate_recording(scan_of(tmp_path, [mf.joint_states()]), prof)
    c = checks(rep)["topic_present:/opt"]
    assert c.outcome is O.FAIL and not c.required
    assert rep.status is S.COMPLETE


def test_cadence_violation_on_optional_topic_does_not_block_complete(tmp_path):
    prof = make_profile(topics=[{"name": "/joint_states"}, {"name": "/camera/image", "required": False, "max_gap_seconds": 0.01}])
    rep = validate_recording(scan_of(tmp_path, [mf.joint_states(), mf.camera()]), prof)
    assert checks(rep)["cadence:/camera/image"].outcome is O.FAIL
    assert rep.status is S.COMPLETE and rep.cadence_findings


def test_topic_without_cadence_rule_gets_no_cadence_check(tmp_path):
    rep = validate_recording(scan_of(tmp_path, [mf.joint_states()]), make_profile(topics=[{"name": "/joint_states"}]))
    assert "cadence:/joint_states" not in checks(rep)


def test_channel_declared_but_no_messages_fails_required_topic(tmp_path):
    rep = validate_recording(scan_of(tmp_path, [mf.StreamSpec("/joint_states", ())]), make_profile())
    c = checks(rep)["topic_present:/joint_states"]
    assert c.outcome is O.FAIL and "channel declared but has no messages" in c.message


def test_single_message_cadence_is_inconclusive_and_blocks_complete(tmp_path):
    rep = validate_recording(scan_of(tmp_path, [mf.StreamSpec("/joint_states", (mf.BASE_NS,))]), make_profile())
    assert checks(rep)["cadence:/joint_states"].outcome is O.INCONCLUSIVE
    assert rep.status is S.PARTIAL


def test_cadence_on_merged_channels_of_same_topic(tmp_path):
    a = mf.StreamSpec("/joint_states", (0, 40_000_000))
    b = mf.StreamSpec("/joint_states", (20_000_000, 60_000_000))
    rep = validate_recording(scan_of(tmp_path, [a, b]), make_profile())
    assert checks(rep)["cadence:/joint_states"].outcome is O.PASS  # merged spacing is 20 ms


def test_explicit_threshold_boundary_equal_is_ok(tmp_path):
    prof = make_profile(topics=[{"name": "/t", "max_gap_seconds": 0.1}])
    ok = scan_of(tmp_path, [mf.StreamSpec("/t", (0, 100_000_000, 200_000_000))])
    assert validate_recording(ok, prof).status is S.COMPLETE
    bad = scan_of(tmp_path, [mf.StreamSpec("/t", (0, 100_000_001))])
    assert validate_recording(bad, prof).status is S.PARTIAL


# ---- metadata ----
def meta_profile():
    return make_profile(required_metadata_keys=["operator", "session_label"])


def test_metadata_found_in_mcap_record(fx, synth_profile):
    c = checks(report(fx / "valid.mcap", synth_profile))
    assert "MCAP metadata record" in c["metadata:operator"].message


def test_missing_metadata_is_a_required_failure(tmp_path):
    rep = validate_recording(scan_of(tmp_path, [mf.joint_states()]), meta_profile())
    assert checks(rep)["metadata:operator"].outcome is O.FAIL
    assert rep.status is S.PARTIAL


def test_metadata_satisfied_by_supplied_file(tmp_path):
    rep = validate_recording(scan_of(tmp_path, [mf.joint_states()]), meta_profile(),
                             supplied_metadata={"operator": "bob", "session_label": "s"})
    assert rep.status is S.COMPLETE and "supplied metadata file" in checks(rep)["metadata:operator"].message


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_metadata_values_count_as_missing(tmp_path, blank):
    rep = validate_recording(scan_of(tmp_path, [mf.joint_states()]), meta_profile(),
                             supplied_metadata={"operator": blank, "session_label": "s"})
    assert checks(rep)["metadata:operator"].outcome is O.FAIL


@pytest.mark.parametrize("value", [0, False, "x"])
def test_falsy_but_present_values_count_as_present(tmp_path, value):
    rep = validate_recording(scan_of(tmp_path, [mf.joint_states()]),
                             make_profile(required_metadata_keys=["k"]), supplied_metadata={"k": value})
    assert checks(rep)["metadata:k"].outcome is O.PASS


def test_metadata_in_any_mcap_metadata_record_counts(tmp_path):
    rep = validate_recording(
        scan_of(tmp_path, [mf.joint_states()], metadata={"a": {"x": "1"}, "b": {"operator": "z"}}),
        make_profile(required_metadata_keys=["operator"]))
    assert checks(rep)["metadata:operator"].outcome is O.PASS


# ---- truncated recordings: absence is inconclusive, not asserted ----
def test_absent_topic_and_metadata_in_truncated_file_are_inconclusive_not_failed(tmp_path):
    data = mf.build_mcap([mf.joint_states(), mf.camera()], metadata=mf.DEFAULT_METADATA)
    p = tmp_path / "t.mcap"
    p.write_bytes(data[: len(data) // 5])
    scan = scan_recording(p)
    assert scan.readability is AR.PARTIALLY_READABLE
    prof = make_profile(topics=[{"name": "/never_there"}], required_metadata_keys=["nothing"])
    c = checks(validate_recording(scan, prof))
    assert c["topic_present:/never_there"].outcome is O.INCONCLUSIVE
    assert c["metadata:nothing"].outcome is O.INCONCLUSIVE


def test_pass_in_truncated_file_notes_recovered_portion_only(fx, synth_profile):
    c = checks(report(fx / "truncated.mcap", synth_profile))["cadence:/joint_states"]
    assert c.outcome is O.PASS and any("recovered portion" in u for u in c.unknowns)


# ---- honesty ----
def test_passing_cadence_never_claims_no_loss(fx, synth_profile):
    for c in report(fx / "valid.mcap", synth_profile).checks:
        if c.check_id.startswith("cadence:"):
            assert "does not show that no messages were lost" in c.message


def test_report_is_deterministic(fx, synth_profile):
    a = report(fx / "cadence_gap.mcap", synth_profile)
    b = report(fx / "cadence_gap.mcap", synth_profile)
    assert a.model_dump_json() == b.model_dump_json()
