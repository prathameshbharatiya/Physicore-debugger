"""TA-LOG-* (TEST_PLAN 3). Synthetic recordings only."""
import pytest

from physicore_collector.anomaly.detectors.tier_a_log import RosLogDetector
from physicore_collector.anomaly.findings import find_forbidden
from physicore_collector.investigator.cdr import decoder_info
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

RCL = "rcl_interfaces/msg/Log"
ROS1 = "rosgraph_msgs/msg/Log"
ID = "tierA.ros_log"
C = cfg({ID: {"enabled": True}})
needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")


def log(level, name="nav", msg="m", **extra):
    d = {"level": level, "msg": msg}
    if name is not ...:
        d["name"] = name
    d.update(extra)
    return d


def stream(payloads, start=F.BASE, step=F.MS):
    return [(start + i * step, p) for i, p in enumerate(payloads)]


PAD = stream([log(20, "pad", f"info {i}") for i in range(40)])
PAD2 = stream([log(20, "pad", f"info {i}") for i in range(40)], start=F.BASE + 200 * F.MS)


def go(tmp_path, segments, *, typ=RCL, config=C, alternate=None, topic="/rosout", schema_text=None):
    segs = [{topic: s} for s in segments]
    bag = X.write_typed_bag(tmp_path / "bag", segs, {topic: typ}, alternate=alternate, schema_text=schema_text)
    out, ev, inv = run_detectors(bag, [RosLogDetector()], config)
    return out, result_of(out, ID), out.findings


def test_01_levels_10_to_50(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(l, msg=f"t{l}") for l in (10, 20, 30, 40, 50)])])
    assert res.status == "findings_reported" and res.findings_count == 3
    got = sorted((f.observed_value["level"], f.severity, f.severity_basis) for f in fs)
    assert got == [(30, "warning", "source_declared_level"), (40, "error", "source_declared_level"), (50, "critical", "source_declared_level")]
    assert all(f.anomaly_category == "log_severity" and f.timestamp_basis == "mcap_log_time" for f in fs)


def test_02_only_unrecognised_levels(tmp_path):
    vals = [29, 31, 39, 41, 49, 51, 0, 255]
    out, res, fs = go(tmp_path, [stream([log(v) for v in vals])])
    assert fs == [] and res.status == "insufficient_evidence"
    assert res.unrecognised_values == {str(v): 1 for v in sorted(vals, key=str)}


def test_02b_unrecognised_plus_one_warn(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(v) for v in (29, 31)] + [log(30)])])
    assert res.status == "findings_reported" and len(fs) == 1 and res.unrecognised_values == {"29": 1, "31": 1}


def test_03_thousand_identical_errors_one_finding(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(40, msg="same")] * 1000, step=1000)])
    (f,) = fs
    assert f.occurrence_count == 1000 and len(f.observations) == 5 and f.observations_truncated
    assert f.first_observed_at.value_ns == F.BASE and f.last_observed_at.value_ns == F.BASE + 999 * 1000
    assert any(n.code == "retained_observations_truncated" for n in f.uncertainty)


def test_04_and_26_different_logger_level_text_not_merged(tmp_path):
    ps = [log(40, "a", "x"), log(40, "b", "x"), log(30, "a", "x"), log(40, "a", "y")]
    out, res, fs = go(tmp_path, [stream(ps)])
    assert len(fs) == 4


def test_05_ros1_json_levels(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(l, msg=str(l)) for l in (1, 2, 4, 8, 16)])], typ=ROS1)
    assert sorted(f.observed_value["level"] for f in fs) == [4, 8, 16]
    assert sorted(f.observed_value["level_name"] for f in fs) == ["ERROR", "FATAL", "WARN"]
    assert all(any(n.code == "constants_from_builtin_table" for n in f.uncertainty) for f in fs)   # TA-LOG-15


def test_06_cross_type_trap_level_4(tmp_path):
    out, res, fs = go(tmp_path / "a", [stream([log(4)])], typ=RCL)
    assert fs == [] and res.unrecognised_values == {"4": 1}
    out, res, fs = go(tmp_path / "b", [stream([log(4)])], typ=ROS1)
    assert len(fs) == 1 and fs[0].observed_value["level_name"] == "WARN"


@pytest.mark.parametrize("bad", ["40", 40.0, True, None, [40]])
def test_07_malformed_level_values(tmp_path, bad):
    out, res, fs = go(tmp_path, [stream([log(bad, "nav")])])
    assert fs == [] and res.payload_malformed_field == 1 and res.status == "insufficient_evidence"


def test_08_missing_level(tmp_path):
    out, res, fs = go(tmp_path, [stream([{"name": "nav", "msg": "x"}, log(20)])])
    assert fs == [] and res.payload_malformed_field == 1 and res.status != "no_anomaly_observed"


def test_09_unparseable_payload_counted_others_evaluated(tmp_path):
    out, res, fs = go(tmp_path, [stream([b"{not json", log(40)])])
    assert res.messages_undecodable == 1 and len(fs) == 1 and res.status == "findings_reported"


@pytest.mark.parametrize("typ", ["rcl_interfaces/msg/log", "my_pkg/msg/Log", "std_msgs/msg/String"])
def test_10_11_near_miss_types_not_applicable(tmp_path, typ):
    out, res, fs = go(tmp_path, [stream([log(40)])], typ=typ, config=cfg({ID: {"enabled": True, "topics": ["/rosout"]}}))
    assert res.status == "not_applicable" and fs == []


def test_auto_without_supported_type_is_not_applicable(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(40)])], typ="std_msgs/msg/String")
    assert res.status == "not_applicable"


def test_12_only_info_debug_complete(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(10), log(20)])])
    assert res.status == "no_anomaly_observed" and fs == []


def test_13_incomplete_recording_is_insufficient(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/rosout": stream([log(20)])}, {"/rosout": stream([log(20)], start=F.BASE + 10**6)}], {"/rosout": RCL})
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"x")      # bytes no longer match the inventory digest
    from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    inv = inspect_evidence(bag)
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    d = RosLogDetector()
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), C))
    res = result_of(run(inv, ev, C, [d]), ID)
    assert not ev.complete and res.status == "insufficient_evidence" and any(r.code == "recording_incomplete" for r in res.reasons)


@needs_decoder
def test_14_embedded_constants_disagree_blind_spot(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    schema = cf.schema_text(RCL).replace(b"WARN=30", b"WARN=31")
    m = T[RCL](stamp=T["builtin_interfaces/msg/Time"](sec=1, nanosec=2), level=40, name="n", msg="m", file="f", function="g", line=1)
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/rosout", RCL, [(F.BASE, m)])]], schema_override={RCL: schema})
    out, ev, inv = run_detectors(bag, [RosLogDetector()], C)
    res = result_of(out, ID)
    assert res.status == "blind_spot" and out.findings == [] and res.reasons[0].code == "constants_mismatch"


def test_16_long_text_truncated_never_parsed(tmp_path):
    long = "x" * 500
    out, res, fs = go(tmp_path, [stream([log(40, msg=long), log(40, msg="a\u0000b")])])
    big = next(f for f in fs if len(f.observed_value["msg"].encode()) > 10)
    assert len(next(f for f in fs if f.observed_value["msg"].startswith("xxx")).observed_value["msg"].encode()) <= 64
    t = next(f for f in fs if f.observed_value["msg"].startswith("xxx"))
    assert any(n.code == "text_truncated" and n.params == {"original_length": 500} for n in t.uncertainty)
    assert any(f.observed_value["msg"] == "a\u0000b" for f in fs)


def test_17_causal_words_in_data_not_in_generated_text(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(40, msg="root cause: caused by the battery")])])
    (f,) = fs
    assert "root cause" in f.observed_value["msg"]
    assert find_forbidden(f.explanation) == []


def test_18_payload_stamp_secondary_only(tmp_path):
    p = log(40, stamp={"sec": 5, "nanosec": 9})
    out, res, fs = go(tmp_path, [stream([p])])
    (f,) = fs
    assert f.clock_domain == "mcap_log_time" and f.first_observed_at.value_ns == F.BASE
    ps = f.observations[0].payload_stamp
    assert ps.value_ns == 5_000_000_009 and ps.clock_domain == "payload_stamp:/rosout:stamp"


def test_20_three_empty_names_three_findings(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(40, "", "same")] * 3)])
    assert len(fs) == 3 and all(f.occurrence_count == 1 for f in fs)
    assert all(any(n.code == "identity_unknown" for n in f.uncertainty) for f in fs)
    assert all(f.observations[0].identity_state == "empty" for f in fs)
    assert res.identity_field_malformed is None


def test_21_known_group_merges_empty_names_do_not(tmp_path):
    ps = [log(40, "nav", "s"), log(40, "", "s"), log(40, "nav", "s"), log(40, "", "s"), log(40, "nav", "s")]
    out, res, fs = go(tmp_path, [stream(ps)])
    assert sorted(f.occurrence_count for f in fs) == [1, 1, 3]


def test_22_nav_vs_nav2(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(40, "nav", "s"), log(40, "nav2", "s")])])
    assert len(fs) == 2


def test_23_error_with_absent_name_is_reported(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(40, ..., "x")])])
    (f,) = fs
    assert f.severity == "error" and f.observations[0].identity_state == "absent" and res.identity_field_malformed == {"name": 1}
    assert f.observations[0].identity_raw == "<absent>"


def test_23b_23c_wrong_type_and_null_name(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(30, 7, "x"), log(50, None, "x")])])
    assert sorted(f.observations[0].identity_state for f in fs) == ["malformed", "malformed"]
    assert res.identity_field_malformed == {"name": 2}
    assert any("number: 7" == f.observations[0].identity_raw for f in fs)


def test_23d_23e_absent_names_never_merged(tmp_path):
    out, res, fs = go(tmp_path / "a", [stream([log(40, ..., "x")] * 3)])
    assert len(fs) == 3
    out, res, fs = go(tmp_path / "b", [stream([log(40, ..., "x"), log(40, "", "x"), log(40, 5, "x")])])
    assert len(fs) == 3


def test_23f_malformed_level_valid_name_not_finding(tmp_path):
    out, res, fs = go(tmp_path, [stream([log("40", "nav")])])
    assert fs == [] and res.payload_malformed_field == 1


def test_23g_info_with_absent_name_does_not_degrade(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(20, ...)])])
    assert fs == [] and res.identity_field_malformed == {"name": 1} and res.status == "no_anomaly_observed"


def test_23h_mixed_stream(tmp_path):
    out, res, fs = go(tmp_path, [stream([log(40, "nav", "m")] * 3 + [log(40, ..., "m")] * 2)])
    assert sorted(f.occurrence_count for f in fs) == [1, 1, 3] and sum(f.occurrence_count for f in fs) == 5


def test_23i_24_cap_counts_exact_and_severity_unchanged(tmp_path):
    ps = [log(40, "", "same")] * 5
    capped = cfg({ID: {"enabled": True}}, {"max_findings_per_detector": 2})
    out, res, fs = go(tmp_path / "a", [stream(ps)], config=capped)
    assert len(fs) == 2 and res.findings_truncated.suppressed_exact_count == 3 and res.findings_truncated.detector == ID
    out2, res2, fs2 = go(tmp_path / "b", [stream(ps)])
    assert len(fs2) == 5 and [f.severity for f in fs] == [f.severity for f in fs2[:2]] and res2.findings_truncated is None


def test_cap_with_known_groups_counts_distinct_suppressed(tmp_path):
    ps = [log(40, "a", "1"), log(40, "b", "1"), log(40, "c", "1"), log(40, "c", "1"), log(40, "d", "1")]
    out, res, fs = go(tmp_path, [stream(ps)], config=cfg({ID: {"enabled": True}}, {"max_findings_per_detector": 1}))
    assert len(fs) == 1 and res.findings_truncated.suppressed_exact_count == 3


def test_25_same_group_two_segments_source_lists_both(tmp_path):
    s1, s2 = stream([log(40, "nav", "s")]), stream([log(40, "nav", "s")], start=F.BASE + 10 * F.MS)
    out, res, fs = go(tmp_path, [s1, s2])
    (f,) = fs
    assert f.occurrence_count == 2 and len(f.source) == 2


def test_25_finding_id_changes_with_any_contributing_artifact(tmp_path):
    s1, s2 = PAD + stream([log(40, "nav", "s")], start=F.BASE + 100 * F.MS), PAD2 + stream([log(40, "nav", "s")], start=F.BASE + 300 * F.MS)
    base = go(tmp_path / "a", [s1, s2])[2][0]
    same = go(tmp_path / "b", [s1, s2])[2][0]
    alt2 = go(tmp_path / "c", [s1, s2], alternate={1})[2][0]
    alt1 = go(tmp_path / "d", [s1, s2], alternate={0})[2][0]
    assert base.finding_id == same.finding_id
    assert alt2.finding_id != base.finding_id and alt1.finding_id != base.finding_id and alt1.finding_id != alt2.finding_id
    assert alt2.occurrence_count == base.occurrence_count == 2


def test_unaffected_artifact_does_not_change_id(tmp_path):
    s1 = stream([log(40, "nav", "s")])
    s2 = PAD2      # holds no contributing occurrence
    a = go(tmp_path / "a", [s1, s2])[2][0]
    b = go(tmp_path / "b", [s1, s2], alternate={1})[2][0]
    assert a.finding_id == b.finding_id and len(a.source) == 1


def test_unsupported_encoding_blind_spot(tmp_path):
    bag = F.write_json_bag(tmp_path / "bag", [{"/rosout": stream([log(40)])}], encodings={"/rosout": "weird"})
    # a json-schema named synthetic/Sample is not a supported type: not applicable (never inferred from the topic name)
    out, ev, inv = run_detectors(bag, [RosLogDetector()], cfg({ID: {"enabled": True, "topics": ["/rosout"]}}))
    assert result_of(out, ID).status == "not_applicable"


@needs_decoder
def test_cdr_ros2_log_levels_and_embedded_constants(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    def mk(level, name="nav", msg="m"):
        return T[RCL](stamp=T["builtin_interfaces/msg/Time"](sec=1, nanosec=2), level=level, name=name, msg=msg, file="f", function="g", line=1)
    msgs = [(F.BASE + i * F.MS, mk(l, "nav" if l != 50 else "", f"t{l}")) for i, l in enumerate((10, 20, 30, 40, 50))]
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/rosout", RCL, msgs)]])
    out, ev, inv = run_detectors(bag, [RosLogDetector()], C)
    res = result_of(out, ID)
    fs = out.findings
    assert res.status == "findings_reported" and sorted(f.observed_value["level"] for f in fs) == [30, 40, 50]
    assert not any(n.code == "constants_from_builtin_table" for f in fs for n in f.uncertainty)      # embedded constants used
    fifty = next(f for f in fs if f.observed_value["level"] == 50)
    assert fifty.observations[0].identity_state == "empty"                                           # TA-LOG-23j
    assert fifty.observations[0].payload_stamp.value_ns == 1_000_000_002


@needs_decoder
def test_cdr_truncated_payload_counted_others_evaluated(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    good = T[RCL](stamp=T["builtin_interfaces/msg/Time"](sec=1, nanosec=2), level=40, name="n", msg="m", file="f", function="g", line=1)
    raw = cf.cdr(RCL, good)
    msgs = [(F.BASE, raw[:10]), (F.BASE + F.MS, good), (F.BASE + 2 * F.MS, raw + b"\x00\x00\x00\x00\x00\x00\x00\x00")]
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/rosout", RCL, msgs)]])
    out, ev, inv = run_detectors(bag, [RosLogDetector()], C)
    res = result_of(out, ID)
    assert res.messages_undecodable >= 1 and len(out.findings) >= 1


@needs_decoder
def test_cdr_payload_on_ros1_type_is_blind_spot(tmp_path):
    # ROS 1 serialisation is not CDR: rosgraph_msgs/Log is supported for JSON payloads only in v1 (DESIGN 3.1, TA-LOG-19)
    schema = b"byte DEBUG=1\nbyte INFO=2\nbyte WARN=4\nbyte ERROR=8\nbyte FATAL=16\nbyte level\nstring name\nstring msg\n"
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/rosout", ROS1, [(F.BASE, b"\x00\x01\x00\x00\x08\x00\x00\x00")])]], schema_override={ROS1: schema})
    out, ev, inv = run_detectors(bag, [RosLogDetector()], C)
    res = result_of(out, ID)
    assert res.status == "blind_spot" and res.reasons[0].code == "message_encoding_unsupported" and out.findings == []


def test_all_contributing_artifacts_listed_even_when_refs_are_capped(tmp_path):
    s1 = stream([log(40, "nav", "s")] * 3)
    s2 = stream([log(40, "nav", "s")], start=F.BASE + 50 * F.MS)
    out, res, fs = go(tmp_path, [s1, s2], config=cfg({ID: {"enabled": True}}, {"max_retained_observations": 1}))
    (f,) = fs
    assert f.occurrence_count == 4 and len(f.observations) == 1 and len(f.source) == 2


def test_in_scope_topic_without_messages_is_insufficient(tmp_path):
    out, res, fs = go(tmp_path, [[]])
    assert res.status == "insufficient_evidence" and any(r.code == "no_messages_on_in_scope_topic" for r in res.reasons)
