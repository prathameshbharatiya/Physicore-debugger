import json

from physicore_collector.anomaly import report as R
from physicore_collector.anomaly.findings import DetectorResult, Reason, SourceRef, find_forbidden
from physicore_collector.anomaly.severity import SEVERITY_POLICY, severity_for
from tests.anomaly.helpers import SHA_A, make_finding

import pytest


def test_CS_06_severity_table_matches_the_approved_policy_row_by_row():
    expected = {
        "log.WARN": ("warning", "source_declared_level"), "log.ERROR": ("error", "source_declared_level"), "log.FATAL": ("critical", "source_declared_level"),
        "diagnostic.WARN": ("warning", "source_declared_level"), "diagnostic.ERROR": ("error", "source_declared_level"),
        "diagnostic.STALE": ("warning", "source_declared_level"),
        "goal.ABORTED": ("error", "detector_policy"), "goal.CANCELED": ("notice", "detector_policy"),
        "battery.health": ("warning", "detector_policy"), "battery.threshold": ("warning", "config"),
    }
    for k, v in expected.items():
        assert severity_for(k) == v
    for tb in ("tierB.publishing_gap", "tierB.rate_change", "tierB.stamp_order", "tierB.array_length", "tierB.non_finite"):
        assert severity_for(tb) == ("notice", "detector_policy")
    with pytest.raises(KeyError):
        severity_for("log.DEBUG")
    with pytest.raises(KeyError):
        severity_for("made.up")


def test_CS_06a_only_log_and_diagnostic_use_source_declared_level():
    sd = {k for k, (_, b) in SEVERITY_POLICY.items() if b == "source_declared_level"}
    assert all(k.startswith(("log.", "diagnostic.")) for k in sd) and len(sd) == 6
    assert not [k for k in SEVERITY_POLICY if k.startswith(("goal.", "battery.", "tierB")) and SEVERITY_POLICY[k][1] == "source_declared_level"]


def test_CS_06b_header_disclaimer_and_no_probability_wording():
    r = R.new_report(tool_version="x", exit_code=0, exit_reasons=[], findings=[make_finding()])
    assert "not a calibrated failure probability" in r.severity_disclaimer and "physical impact" in r.severity_disclaimer
    text = R.render_json(r)
    for f in r.findings:
        for w in ("probability", "likelihood", "impact"):
            assert w not in f.explanation.lower()


def test_CS_19_limitations_always_present():
    r = R.new_report(tool_version="x", exit_code=0, exit_reasons=[])
    assert "late-message detection is not performed (deferred)" in r.limitations
    joined = " ".join(r.limitations)
    for needle in ("real ROS recording validation is outstanding", "licensing", "documentation verification", "float32 NaN"):
        assert needle in joined


def test_W_wording_of_all_generated_text_is_non_causal():
    r = R.new_report(tool_version="x", exit_code=0, exit_reasons=[], findings=[make_finding()],
                     results=[DetectorResult(detector_id="tierB.rate_change", detector_version="1.0.0", status="insufficient_evidence",
                                             reasons=[Reason(code="baseline_count_too_small", detail="the baseline of 2 is below the required 3")])])
    assert R.check_wording(r) == []
    assert find_forbidden(R.render_text(r)) == []


def test_new_report_orders_results_and_evidence_deterministically():
    a = DetectorResult(detector_id="tierB.rate_change", detector_version="1.0.0", status="not_configured")
    b = DetectorResult(detector_id="tierA.ros_log", detector_version="1.0.0", status="not_configured")
    r = R.new_report(tool_version="x", exit_code=0, exit_reasons=[], results=[a, b],
                     evidence=[SourceRef(artifact_ref="z", sha256=SHA_A), SourceRef(artifact_ref="a", sha256=SHA_A)])
    assert [x.detector_id for x in r.results] == ["tierA.ros_log", "tierB.rate_change"] and [e.artifact_ref for e in r.evidence] == ["a", "z"]
    assert r.clock_domains == ["mcap_log_time"]


def test_report_text_lists_config_problems():
    r = R.new_report(tool_version="x", exit_code=3, exit_reasons=["invalid_config"], config_problems=[{"path": "limits", "message": "required section is missing"}])
    t = R.render_text(r)
    assert "Invalid anomaly configuration" in t and "limits: required section is missing" in t
    assert json.loads(R.render_json(r))["config_problems"][0]["path"] == "limits"
