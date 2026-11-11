import pytest
from pydantic import ValidationError

from physicore_collector.schemas import (
    ArtifactEntry, ArtifactReadability, ArtifactRole, BasisCompatibility, CadenceFinding,
    CheckCategory, CheckOutcome, CheckResult, KnownGap, LossDetectionCoverage, Manifest,
    RecordingWindow, RobotProfile, SessionStatus, TimeBase, TopicRequirement, Trigger,
    ValidationReport,
)

SHA = "a" * 64


def make_profile(**over):
    data = {"profile_schema_version": "1.0", "profile_id": "p", "topics": [{"name": "/a"}]}
    data.update(over)
    return RobotProfile.model_validate(data)


def check(outcome=CheckOutcome.PASS, required=True, cid="c1"):
    return CheckResult(check_id=cid, category=CheckCategory.OTHER, outcome=outcome,
                       required=required, message="m")


def make_report(status=SessionStatus.PARTIAL, readability=ArtifactReadability.READABLE, checks=None):
    return ValidationReport(status=status, status_reasons=["r"], artifact_readability=readability,
                            checks=[check()] if checks is None else checks)


def make_manifest(**over):
    report = over.pop("validation", make_report(SessionStatus.COMPLETE))
    data = dict(
        session_status=report.status, profile=make_profile(), profile_sha256=SHA,
        artifacts=[ArtifactEntry(relative_path="recording.mcap", role=ArtifactRole.RECORDING,
                                 size_bytes=1, sha256=SHA)],
        validation=report,
        known_gaps=[KnownGap(code="no_seq", description="no sequence counters analysed")],
        loss_detection_coverage=LossDetectionCoverage(statement="cannot rule out loss"),
    )
    data.update(over)
    return Manifest(**data)


# ---- profile ----
def test_profile_valid_and_defaults():
    p = make_profile()
    assert p.log_time_basis is TimeBase.UNKNOWN
    assert p.topics[0].required is True


def test_profile_rejects_unsupported_schema_version():
    with pytest.raises(ValidationError, match="unsupported profile_schema_version"):
        make_profile(profile_schema_version="9.9")


def test_profile_rejects_unknown_field_typo():
    with pytest.raises(ValidationError):
        make_profile(requried_metadata_keys=[])


def test_profile_rejects_duplicate_topics():
    with pytest.raises(ValidationError, match="duplicate topic"):
        make_profile(topics=[{"name": "/a"}, {"name": "/a"}])


def test_profile_requires_at_least_one_topic():
    with pytest.raises(ValidationError):
        make_profile(topics=[])


@pytest.mark.parametrize("bad", [{"expected_hz": 0}, {"expected_hz": -1},
                                 {"max_gap_seconds": 0}, {"gap_factor": 1.0}])
def test_topic_rejects_nonpositive_values(bad):
    with pytest.raises(ValidationError):
        TopicRequirement(name="/a", **bad)


def test_topic_name_rejects_whitespace():
    with pytest.raises(ValidationError, match="whitespace"):
        TopicRequirement(name="/a b")


def test_effective_gap_rules():
    assert TopicRequirement(name="/a").effective_max_gap_seconds is None
    assert TopicRequirement(name="/a", expected_hz=50).effective_max_gap_seconds == pytest.approx(0.04)
    assert TopicRequirement(name="/a", expected_hz=10, gap_factor=3).effective_max_gap_seconds == pytest.approx(0.3)
    # explicit max_gap_seconds wins over expected_hz
    t = TopicRequirement(name="/a", expected_hz=50, max_gap_seconds=0.5)
    assert t.effective_max_gap_seconds == 0.5


# ---- trigger ----
def test_trigger_requires_timestamp_basis():
    with pytest.raises(ValidationError):
        Trigger.model_validate({"trigger_id": "t", "timestamp_ns": 1})


def test_trigger_basis_label_alone_carries_no_compatibility():
    t = Trigger(trigger_id="t", timestamp_ns=1, timestamp_basis=TimeBase.ROS_TIME)
    assert t.basis_compatibility is None


def test_basis_compatibility_requires_stated_source():
    with pytest.raises(ValidationError):
        BasisCompatibility(compatible_with_log_time=True, established_by="")


def test_trigger_rejects_negative_timestamp():
    with pytest.raises(ValidationError):
        Trigger(trigger_id="t", timestamp_ns=-1, timestamp_basis=TimeBase.UNKNOWN)


# ---- evidence / loss honesty ----
def test_loss_coverage_cannot_claim_no_loss():
    with pytest.raises(ValidationError):
        LossDetectionCoverage(no_loss_claim_permitted=True, statement="s")
    assert LossDetectionCoverage(statement="s").no_loss_claim_permitted is False


def test_cadence_finding_is_labelled_as_timing_evidence_not_loss():
    f = CadenceFinding(topic="/a", threshold_seconds=0.1, observed_gap_seconds=0.5,
                       gap_start_log_time_ns=1, gap_end_log_time_ns=2)
    assert f.kind == "observed_log_time_gap"
    assert "not proof of DDS message loss" in f.interpretation


def test_recording_window_must_be_ordered():
    with pytest.raises(ValidationError):
        RecordingWindow(min_log_time_ns=5, max_log_time_ns=4)


# ---- 'complete' can never be represented when contradicted ----
def test_complete_report_valid_when_earned():
    assert make_report(SessionStatus.COMPLETE).status is SessionStatus.COMPLETE


@pytest.mark.parametrize("outcome", [CheckOutcome.FAIL, CheckOutcome.INCONCLUSIVE])
def test_complete_rejected_with_required_check_not_passing(outcome):
    with pytest.raises(ValidationError, match="required checks did not pass"):
        make_report(SessionStatus.COMPLETE, checks=[check(), check(outcome, cid="c2")])


def test_complete_allowed_with_nonrequired_failure():
    r = make_report(SessionStatus.COMPLETE, checks=[check(), check(CheckOutcome.FAIL, required=False, cid="w")])
    assert r.status is SessionStatus.COMPLETE


@pytest.mark.parametrize("readability", [ArtifactReadability.UNREADABLE, ArtifactReadability.PARTIALLY_READABLE])
def test_complete_rejected_when_not_fully_readable(readability):
    with pytest.raises(ValidationError, match="fully readable"):
        make_report(SessionStatus.COMPLETE, readability=readability)


def test_complete_rejected_with_no_checks():
    with pytest.raises(ValidationError, match="at least one executed check"):
        make_report(SessionStatus.COMPLETE, checks=[])


# ---- manifest ----
def test_manifest_valid_roundtrip_json():
    m = make_manifest()
    again = Manifest.model_validate_json(m.model_dump_json())
    assert again == m
    assert again.tool.name == "physicore-collector"


def test_manifest_requires_known_gaps():
    with pytest.raises(ValidationError):
        make_manifest(known_gaps=[])


def test_manifest_status_must_match_report():
    with pytest.raises(ValidationError, match="must equal validation report status"):
        make_manifest(session_status=SessionStatus.FAILED)


def test_manifest_rejects_duplicate_artifact_paths():
    a = ArtifactEntry(relative_path="x", role=ArtifactRole.RECORDING, size_bytes=1, sha256=SHA)
    with pytest.raises(ValidationError, match="duplicate artifact"):
        make_manifest(artifacts=[a, a])


@pytest.mark.parametrize("path", ["/abs", "../up", "a/../b", "C:\\x", "\\x"])
def test_artifact_path_must_be_safe_relative(path):
    with pytest.raises(ValidationError):
        ArtifactEntry(relative_path=path, role=ArtifactRole.RECORDING, size_bytes=1, sha256=SHA)


def test_artifact_sha256_must_be_64_lowercase_hex():
    for bad in ["abc", "A" * 64, "g" * 64]:
        with pytest.raises(ValidationError):
            ArtifactEntry(relative_path="x", role=ArtifactRole.RECORDING, size_bytes=1, sha256=bad)
