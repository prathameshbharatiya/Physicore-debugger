"""ARR-* (TEST_PLAN 8): tierB.array_length."""
import pytest


class _LazyNp:
    def __getattr__(self, n):
        import numpy
        return getattr(numpy, n)


np = _LazyNp()

from physicore_collector.anomaly.config import validate_config
from physicore_collector.anomaly.detectors.tier_b_array_length import ArrayLengthDetector, walk
from physicore_collector.anomaly.findings import find_forbidden
from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator.cdr import decoder_info
from physicore_collector.investigator.observations import load_evidence
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

ID = "tierB.array_length"
SCAN = "sensor_msgs/msg/LaserScan"
DIAG = "diagnostic_msgs/msg/DiagnosticArray"
needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")


def conf(rules, limits=None):
    return cfg({ID: {"enabled": True, "rules": rules}}, limits)


def rule(path="ranges", n=None, topic="/t", base=None):
    if base:
        return {"topic": topic, "path": path, "baseline": "mode", "min_observations": base[0], "min_share": base[1]}
    return {"topic": topic, "path": path, "expected_length": n}


def scans(lengths, start=F.BASE):
    return [(start + i * F.MS, {"ranges": [1.0] * n}) for i, n in enumerate(lengths)]


def go(tmp_path, msgs=None, rules=None, *, segments=None, typ=SCAN, limits=None, alternate=None):
    segs = segments if segments is not None else [msgs]
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": s} for s in segs], {"/t": typ}, alternate=alternate)
    out, ev, inv = run_detectors(bag, [ArrayLengthDetector()], conf(rules or [rule(n=360)], limits))
    return out, result_of(out, ID), out.findings


def laser(n, t=F.BASE):
    from tests import cdr_fixtures as cf
    ts = cf.typestore()
    return ts.types[SCAN](header=cf._hdr(t, "l"), angle_min=0.0, angle_max=1.0, angle_increment=0.1, time_increment=0.0, scan_time=0.1, range_min=0.0,
                          range_max=10.0, ranges=np.array([1.0] * n, dtype=np.float32), intensities=np.array([], dtype=np.float32))


def test_01_all_expected(tmp_path):
    out, res, fs = go(tmp_path, scans([360] * 5))
    assert res.status == "no_anomaly_observed" and fs == []


def test_02_one_short(tmp_path):
    out, res, fs = go(tmp_path, scans([360, 360, 359, 360, 360]))
    assert res.status == "findings_reported" and len(fs) == 1
    f = fs[0]
    assert f.observed_value["length"] == 359 and f.expected_value == 360 and f.expected_source == "config"
    assert f.occurrence_count == 1 and f.anomaly_category == "array_length_change" and f.field_path == "ranges"
    assert f.first_observed_at.value_ns == F.BASE + 2 * F.MS and f.last_observed_at is None
    assert f.severity == "notice" and f.severity_basis == "detector_policy"
    assert not any(n.code == "baseline_from_recording" for n in f.uncertainty)


def test_03_run_of_zero_length(tmp_path):
    out, res, fs = go(tmp_path, scans([360] * 3 + [0] * 10 + [360] * 3))
    assert len(fs) == 1 and fs[0].occurrence_count == 10 and fs[0].observed_value["length"] == 0
    assert fs[0].last_observed_at.value_ns == F.BASE + 12 * F.MS and fs[0].observations[0].value == 0


def test_03b_two_separate_runs_are_two_findings(tmp_path):
    out, res, fs = go(tmp_path, scans([360, 5, 5, 360, 5, 360]))
    assert [f.occurrence_count for f in fs] == [2, 1] and len({f.finding_id for f in fs}) == 2


def test_03c_changing_wrong_lengths_are_separate_runs(tmp_path):
    out, res, fs = go(tmp_path, scans([360, 5, 6, 360]))
    assert [f.observed_value["length"] for f in fs] == [5, 6]


@needs_decoder
def test_04_fixed_size_array_not_applicable(tmp_path):
    from tests import cdr_fixtures as cf
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/t", "nav_msgs/msg/Odometry", [(F.BASE, cf.odometry(F.BASE, 0, 0, 0, 0))])]])
    out, ev, inv = run_detectors(bag, [ArrayLengthDetector()], conf([rule("pose.covariance", 36)]))
    res = result_of(out, ID)
    assert res.status == "not_applicable" and any(r.code == "fixed_size_array" for r in res.reasons) and out.findings == []


def diag(lengths):
    return {"status": [{"values": [{"key": "k", "value": "v"}] * n} for n in lengths]}


def test_05_nested_wildcard_paths(tmp_path):
    msgs = [(F.BASE, diag([3, 3])), (F.BASE + F.MS, diag([3, 2])), (F.BASE + 2 * F.MS, diag([3, 2])), (F.BASE + 3 * F.MS, diag([3, 3]))]
    out, res, fs = go(tmp_path, msgs, [rule("status[*].values", 3)], typ=DIAG)
    assert len(fs) == 1
    f = fs[0]
    assert f.field_path == "status[1].values" and f.field_path_template == "status[*].values" and f.occurrence_count == 2
    assert [o.field_path for o in f.observations] == ["status[1].values"] * 2


def test_05b_walk_expansion():
    root = {"a": [{"b": [1, 2]}, {"b": [1]}, {"x": 1}]}
    got = walk(root, "a[*].b")
    assert [(p, s) for p, s, _ in got] == [("a[0].b", "ok"), ("a[1].b", "ok"), ("a[2].b", "absent")]
    assert [p for p, s, _ in walk(root, "a[1].b")] == ["a[1].b"]
    assert walk(root, "a[7].b")[0][1] == "absent"


def test_06_baseline_mode(tmp_path):
    out, res, fs = go(tmp_path, scans([5] * 9 + [4]), [rule(base=(10, "0.8"))])
    assert len(fs) == 1 and fs[0].expected_value == 5 and fs[0].expected_source == "recording_baseline"
    assert "baseline_from_recording" in {n.code for n in fs[0].uncertainty}
    assert fs[0].observed_value["baseline_share"] == "9/10" and fs[0].source


def test_06b_baseline_finding_lists_every_artifact(tmp_path):
    out, res, fs = go(tmp_path, segments=[scans([5] * 5), scans([4] + [5] * 4, start=F.BASE + 100 * F.MS)], rules=[rule(base=(5, "0.5"))])
    assert len(fs) == 1 and len(fs[0].source) == 2


def test_07_baseline_tie(tmp_path):
    out, res, fs = go(tmp_path, scans([3] * 5 + [4] * 5), [rule(base=(1, "0.1"))])
    assert res.status == "insufficient_evidence" and any(r.code == "baseline_tie" for r in res.reasons) and fs == []


def test_08_min_observations_boundary(tmp_path):
    out, res, fs = go(tmp_path / "a", scans([5] * 9 + [4]), [rule(base=(10, "0.8"))])
    assert res.status == "findings_reported"                       # observations == min_observations passes
    out, res, fs = go(tmp_path / "b", scans([5] * 9 + [4]), [rule(base=(11, "0.8"))])
    assert res.status == "insufficient_evidence" and any(r.code == "too_few_observations" for r in res.reasons) and fs == []


def test_08b_min_share_boundary_equal_passes(tmp_path):
    out, res, fs = go(tmp_path / "a", scans([5] * 8 + [1, 2]), [rule(base=(5, "0.8"))])
    assert res.status == "findings_reported" and len(fs) == 2
    out, res, fs = go(tmp_path / "b", scans([5] * 7 + [1, 2, 3]), [rule(base=(5, "0.8"))])
    assert res.status == "insufficient_evidence" and any(r.code == "baseline_share_below_min" for r in res.reasons) and fs == []


def test_09_no_rules_is_an_invalid_configuration():
    for det in ({"enabled": True}, {"enabled": True, "rules": []}, {"enabled": True, "rules": [{"topic": "/t", "path": "p"}]}):
        lc = validate_config({"anomaly_config_version": 1, "limits": {"max_retained_observations": 5, "max_text_bytes": 64, "max_findings_per_detector": 10},
                              "detectors": {ID: det}})
        assert lc.config is None and lc.problems


def test_10_json_path_not_an_array_is_blind(tmp_path):
    msgs = [(F.BASE + i * F.MS, {"ranges": "not an array"}) for i in range(3)]
    out, res, fs = go(tmp_path, msgs)
    assert res.status == "blind_spot" and any(r.code == "path_not_an_array" and r.count == 3 for r in res.reasons) and fs == []


@needs_decoder
def test_10b_cdr_path_problems_are_blind(tmp_path):
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/t", SCAN, [(F.BASE, laser(3))])]])
    for path, code in (("no_such", "field_not_in_schema"), ("range_max", "path_not_an_array"), ("ranges[*]", "path_not_an_array"), ("ranges.x", "path_invalid")):
        out, ev, inv = run_detectors(bag, [ArrayLengthDetector()], conf([rule(path, 3)]))
        res = result_of(out, ID)
        assert res.status == "blind_spot" and res.reasons[0].code == code and out.findings == [], (path, res.status, res.reasons)


def test_11_absent_optional_is_not_length_zero(tmp_path):
    msgs = [(F.BASE, {"ranges": [1.0] * 3}), (F.BASE + F.MS, {}), (F.BASE + 2 * F.MS, {"ranges": None}), (F.BASE + 3 * F.MS, {"ranges": [1.0] * 3})]
    out, res, fs = go(tmp_path, msgs, [rule(n=3)])
    assert fs == [] and res.status == "no_anomaly_observed"


def test_11b_absence_breaks_a_run(tmp_path):
    msgs = [(F.BASE, {"ranges": []}), (F.BASE + F.MS, {}), (F.BASE + 2 * F.MS, {"ranges": []})]
    out, res, fs = go(tmp_path, msgs, [rule(n=3)])
    assert [f.occurrence_count for f in fs] == [1, 1]


def test_12_variable_diagnostics_is_a_documented_false_positive(tmp_path):
    msgs = [(F.BASE + i * F.MS, {"status": [{"values": []}] * (3 if i != 4 else 5)}) for i in range(10)]
    out, res, fs = go(tmp_path, msgs, [rule("status", base=(5, "0.5"))], typ=DIAG)
    assert len(fs) == 1
    txt = fs[0].explanation
    assert "not by itself evidence of a fault" in txt and not find_forbidden(txt)


def test_13_malformed_payloads(tmp_path):
    msgs = [(F.BASE, {"ranges": [1.0] * 3}), (F.BASE + F.MS, b"{not json"), (F.BASE + 2 * F.MS, {"ranges": {"a": 1}}), (F.BASE + 3 * F.MS, {"ranges": [1.0] * 3})]
    out, res, fs = go(tmp_path, msgs, [rule(n=3)])
    assert res.messages_undecodable == 1 and res.payload_malformed_field == 1 and res.status == "insufficient_evidence"
    assert fs == []


@needs_decoder
def test_13b_truncated_cdr(tmp_path):
    from tests import cdr_fixtures as cf
    good = cf.cdr(SCAN, laser(3))
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/t", SCAN, [(F.BASE, good), (F.BASE + F.MS, good[:20]), (F.BASE + 2 * F.MS, good)])]])
    out, ev, inv = run_detectors(bag, [ArrayLengthDetector()], conf([rule(n=3)]))
    res = result_of(out, ID)
    assert res.messages_undecodable == 1 and res.status == "insufficient_evidence" and out.findings == []


@needs_decoder
def test_14_json_cdr_parity(tmp_path):
    lengths = [3, 3, 2, 2, 2, 3, 0, 3]
    jb = X.write_typed_bag(tmp_path / "j", [{"/t": scans(lengths)}], {"/t": SCAN})
    cb = X.write_cdr_bag(tmp_path / "c", [[("/t", SCAN, [(F.BASE + i * F.MS, laser(n, F.BASE + i * F.MS)) for i, n in enumerate(lengths)])]])
    got = []
    for bag in (jb, cb):
        out, ev, inv = run_detectors(bag, [ArrayLengthDetector()], conf([rule(n=3)]))
        got.append([(f.observed_value["length"], f.occurrence_count, f.first_observed_at.value_ns, f.last_observed_at and f.last_observed_at.value_ns,
                     f.field_path, f.expected_value) for f in out.findings])
    assert got[0] == got[1] == [(2, 3, F.BASE + 2 * F.MS, F.BASE + 4 * F.MS, "ranges", 3), (0, 1, F.BASE + 6 * F.MS, None, "ranges", 3)]


def test_15_incomplete_recording(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": scans([360, 5, 360])}, {"/t": scans([360] * 3, start=F.BASE + 100 * F.MS)}], {"/t": SCAN})
    inv = inspect_evidence(bag)
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    d, c = ArrayLengthDetector(), conf([rule(n=360)])
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), c))
    out = run(inv, ev, c, [d])
    res = result_of(out, ID)
    assert not ev.complete and res.status == "findings_reported" and all(f.coverage.topic_coverage == "incomplete" for f in out.findings)
    bag2 = X.write_typed_bag(tmp_path / "bag2", [{"/t": scans([360] * 3)}, {"/t": scans([360] * 3, start=F.BASE + 100 * F.MS)}], {"/t": SCAN})
    inv2 = inspect_evidence(bag2)
    (bag2 / "bag2_1.mcap").write_bytes((bag2 / "bag2_1.mcap").read_bytes() + b"y")
    d2 = ArrayLengthDetector()
    ev2 = load_evidence(inv2, wanted_topics([d2], topic_schema_names(inv2), c))
    res2 = result_of(run(inv2, ev2, c, [d2]), ID)
    assert res2.status == "insufficient_evidence" and any(r.code == "recording_incomplete" for r in res2.reasons)


def test_16_topic_not_recorded_and_mixed(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": scans([3])}], {"/t": SCAN})
    out, ev, inv = run_detectors(bag, [ArrayLengthDetector()], conf([rule(topic="/other", n=3)]))
    res = result_of(out, ID)
    assert res.status == "not_applicable" and res.reasons[0].code == "topic_not_recorded"


def test_17_findings_cap_is_exact(tmp_path):
    out, res, fs = go(tmp_path, scans([360, 1] * 10), limits={"max_findings_per_detector": 4})
    assert len(fs) == 4 and res.findings_truncated.suppressed_exact_count == 6


def test_18_observation_retention_cap(tmp_path):
    out, res, fs = go(tmp_path, scans([1] * 20), limits={"max_retained_observations": 5})
    f = fs[0]
    assert f.occurrence_count == 20 and len(f.observations) == 5 and f.observations_truncated
    assert "retained_observations_truncated" in {n.code for n in f.uncertainty}


def test_19_finding_ids_stable_and_ordered(tmp_path):
    a = go(tmp_path / "a", scans([360, 1, 360, 2]))[2]
    b = go(tmp_path / "b", scans([360, 1, 360, 2]))[2]
    assert [f.finding_id for f in a] == [f.finding_id for f in b] and len({f.finding_id for f in a}) == 2


def test_11c_single_present_observation_is_enough_for_a_config_rule(tmp_path):
    out, res, fs = go(tmp_path, [(F.BASE, {}), (F.BASE + F.MS, {"ranges": [1.0] * 3})], [rule(n=3)])
    assert res.status == "no_anomaly_observed" and fs == []
    out, res, fs = go(tmp_path / "n", [(F.BASE, {}), (F.BASE + F.MS, {})], [rule(n=3)])
    assert res.status == "insufficient_evidence" and any(r.code == "no_observations" for r in res.reasons)
