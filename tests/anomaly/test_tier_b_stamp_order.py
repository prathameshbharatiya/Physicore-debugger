"""STAMP-* (TEST_PLAN 7)."""
import inspect
import random
from decimal import Decimal

import pytest

from physicore_collector.anomaly import access
from physicore_collector.anomaly.access import DecodeCounter, MessageView
from physicore_collector.anomaly.clock import ClockDomainError, ClockedValue, LOG_TIME_DOMAIN
from physicore_collector.anomaly.detectors import tier_b_stamp_order as mod
from physicore_collector.anomaly.detectors.tier_b_stamp_order import StampOrderDetector
from physicore_collector.anomaly.findings import find_forbidden
from physicore_collector.investigator import observations as M3B
from physicore_collector.investigator.cdr import decoder_info
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

ID = "tierB.stamp_order"
HDR = "std_msgs/msg/Header"
NS = 1_000_000_000
needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")


def conf(fields=("header.stamp",), partition=(), topics=("/t",), limits=None):
    return cfg({ID: {"enabled": True, "topics": list(topics), "fields": list(fields), "partition_by": list(partition)}}, limits)


def st(sec, nsec=0, frame="a"):
    return {"header": {"stamp": {"sec": sec, "nanosec": nsec}, "frame_id": frame}}


def stamps(values, frames=None, step=F.MS):
    return [(F.BASE + i * step, st(v // NS, v % NS, frames[i] if frames else "a")) for i, v in enumerate(values)]


def go(tmp_path, msgs=None, *, config=None, segments=None, alternate=None, extra=None, typ=HDR):
    segs = segments if segments is not None else [msgs]
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": s, **(extra or {})} for s in segs], {"/t": typ, **{k: typ for k in (extra or {})}}, alternate=alternate)
    out, ev, inv = run_detectors(bag, [StampOrderDetector()], config or conf())
    return out, result_of(out, ID), out.findings


def test_01_increasing(tmp_path):
    out, res, fs = go(tmp_path, stamps([1, 2, 3, 4, 5]))
    assert res.status == "no_anomaly_observed" and fs == []


def test_02_one_backward_step(tmp_path):
    out, res, fs = go(tmp_path, stamps([10 * NS, 11 * NS, 9 * NS + 5, 12 * NS]))
    (f,) = fs
    assert f.anomaly_category == "timestamp_regression" and f.observed_value["previous_stamp_ns"] == 11 * NS
    assert f.observed_value["stamp_ns"] == 9 * NS + 5 and f.observed_value["magnitude_ns"] == 2 * NS - 5
    assert f.clock_domain == "payload_stamp:/t:header.stamp" and f.timestamp_basis == "payload_stamp"
    assert f.first_observed_at.value_ns == 9 * NS + 5 and f.first_observed_at.clock_domain == f.clock_domain
    assert [o.message_index for o in f.observations] == [1, 2] and all(o.log_time_ns for o in f.observations)
    assert f.severity == "notice" and f.severity_basis == "detector_policy"


@pytest.mark.parametrize("n", [2, 50])
def test_03_duplicate_runs(tmp_path, n):
    out, res, fs = go(tmp_path, stamps([5, 6] + [7] * n + [8]))
    (f,) = fs
    assert f.anomaly_category == "timestamp_duplicate" and f.occurrence_count == n and f.observed_value["messages_in_run"] == n and f.observed_value["repeats"] == n - 1
    assert f.observed_value["stamp_ns"] == 7 and len(f.observations) <= 5


def test_03_separate_runs(tmp_path):
    out, res, fs = go(tmp_path, stamps([1, 1, 2, 3, 3, 3]))
    assert sorted(f.occurrence_count for f in fs) == [2, 3]


def test_04_prev_reset_after_regression(tmp_path):
    out, res, fs = go(tmp_path, stamps([100, 200, 50, 51, 52, 53]))
    assert len(fs) == 1 and fs[0].observed_value["stamp_ns"] == 50


def test_05_all_zero(tmp_path):
    out, res, fs = go(tmp_path, stamps([0, 0, 0, 0]))
    (f,) = fs
    assert f.anomaly_category == "timestamp_duplicate" and f.occurrence_count == 4
    assert any(n.code == "zero_stamp_valid_not_assumed_unset" for n in f.uncertainty) and "unset" not in f.explanation.lower()


def test_06_single_zero_among_nonzero(tmp_path):
    out, res, fs = go(tmp_path, stamps([7, 8, 0, 9]))
    (f,) = fs
    assert f.observed_value["previous_stamp_ns"] == 8 and f.observed_value["stamp_ns"] == 0


def test_07_valid_boundaries_exact(tmp_path):
    vals = [(0, 0), (0, 1), (1, 0), (1, 999_999_999), (2, 0), (2147483647, 999_999_999)]
    msgs = [(F.BASE + i * F.MS, st(s, n)) for i, (s, n) in enumerate(vals)]
    out, res, fs = go(tmp_path, msgs)
    assert fs == [] and res.status == "no_anomaly_observed"
    bag = X.write_typed_bag(tmp_path / "x", [{"/t": msgs}], {"/t": HDR})
    inv, ev = X.load(bag, {"/t"})
    got = [MessageView(m, ev, DecodeCounter()).stamp("header.stamp")[0] for m in ev.messages["/t"]]
    assert got == [s * NS + n for s, n in vals]


@pytest.mark.parametrize("bad", [(-1, 5), (5, -1), (5, 1_000_000_000), (5, 4_294_967_295)])
def test_08_invalid_stamps_not_evaluated(tmp_path, bad):
    msgs = [(F.BASE, st(1, 0)), (F.BASE + F.MS, st(*bad)), (F.BASE + 2 * F.MS, st(3, 0))]
    out, res, fs = go(tmp_path, msgs)
    assert fs == [] and res.status == "blind_spot"
    r = next(r for r in res.reasons if r.code == "invalid_stamps")
    assert r.count == 1 and "artifact:" in r.detail and "sec=" in r.detail


@pytest.mark.parametrize("bad", ["1", True, 1.5, None])
def test_10_json_invalid_parts(tmp_path, bad):
    msgs = [(F.BASE, st(1)), (F.BASE + F.MS, {"header": {"stamp": {"sec": bad, "nanosec": 0}}}), (F.BASE + 2 * F.MS, st(3))]
    out, res, fs = go(tmp_path, msgs)
    assert res.status == "blind_spot" and next(r for r in res.reasons if r.code == "invalid_stamps").count == 1


def test_10_missing_keys_and_float_two(tmp_path):
    msgs = [(F.BASE, st(1)), (F.BASE + F.MS, {"header": {"stamp": {"nanosec": 0}}}), (F.BASE + 2 * F.MS, {"header": {}}), (F.BASE + 3 * F.MS, {}),
            (F.BASE + 4 * F.MS, {"header": {"stamp": {"sec": 2.0, "nanosec": 0}}}), (F.BASE + 5 * F.MS, st(3))]
    out, res, fs = go(tmp_path, msgs)
    assert next(r for r in res.reasons if r.code == "invalid_stamps").count == 3        # 2.0 s is valid exactly as M3b
    assert fs == []


def test_11_clock_separation(tmp_path):
    msgs = [(F.BASE + i * F.MS, st(10 - i)) for i in range(5)]                         # stamps DEcreasing, log times increasing
    out, res, fs = go(tmp_path / "a", msgs)
    assert len(fs) == 4 and all(f.anomaly_category == "timestamp_regression" for f in fs)
    msgs = [(F.BASE + (9 - i) * F.MS, st(i)) for i in range(5)]                        # log times decreasing in index order but file order is time order
    out, res, fs = go(tmp_path / "b", [(t, p) for t, p in sorted(msgs)])
    assert len(fs) == 4                                                                # receipt order is log-time order: stamps decrease along it
    msgs = [(F.BASE + i * F.MS, st(i)) for i in range(5)]
    out, res, fs = go(tmp_path / "c", msgs)                                            # only log-time/other-order change: no finding
    assert fs == []


def test_12_two_topics_no_cross_finding(tmp_path):
    a = stamps([1, 2, 3, 4])
    b = stamps([1, 2, 3, 4])
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": a, "/u": b}], {"/t": HDR, "/u": HDR})
    out, ev, inv = run_detectors(bag, [StampOrderDetector()], conf(topics=("/t", "/u")))
    assert out.findings == []
    bag = X.write_typed_bag(tmp_path / "bag2", [{"/t": stamps([1, 5, 9]), "/u": stamps([2, 6, 10])}], {"/t": HDR, "/u": HDR})
    out, ev, inv = run_detectors(bag, [StampOrderDetector()], conf(topics=("/t", "/u")))
    assert out.findings == []


def test_13_partition(tmp_path):
    vals = [10, 1, 11, 2, 12, 3]
    frames = ["a", "b", "a", "b", "a", "b"]
    msgs = stamps(vals, frames)
    out, res, fs = go(tmp_path / "a", msgs)
    assert len(fs) == 3                                                                # interleaved publishers look like regressions
    out, res, fs = go(tmp_path / "b", msgs, config=conf(partition=("header.frame_id",)))
    assert fs == [] and res.status == "no_anomaly_observed"
    mixed = stamps([10, 11, 9, 1, 2], ["a", "a", "a", "b", "b"])
    out, res, fs = go(tmp_path / "c", mixed, config=conf(partition=("header.frame_id",)))
    assert len(fs) == 1 and fs[0].observed_value["partition"] == {"header.frame_id": "a"}


def test_13_missing_partition_value_counted(tmp_path):
    msgs = [(F.BASE, st(1)), (F.BASE + F.MS, {"header": {"stamp": {"sec": 2, "nanosec": 0}}}), (F.BASE + 2 * F.MS, st(3)), (F.BASE + 3 * F.MS, st(4))]
    out, res, fs = go(tmp_path, msgs, config=conf(partition=("header.frame_id",)))
    assert res.payload_malformed_field == 1 and fs == []


def test_14_stamp_equal_to_log_time_no_special_case(tmp_path):
    msgs = [(F.BASE, st(F.BASE // NS, F.BASE % NS)), (F.BASE + F.MS, st(F.BASE // NS, F.BASE % NS + F.MS))]
    out, res, fs = go(tmp_path, msgs)
    assert fs == [] and res.status == "no_anomaly_observed"


@pytest.mark.parametrize("n,status", [(1, "insufficient_evidence"), (2, "no_anomaly_observed")])
def test_15_minimum_two_valid(tmp_path, n, status):
    out, res, fs = go(tmp_path, stamps(list(range(1, n + 1))))
    assert res.status == status


def test_16_seq_is_not_used(tmp_path):
    msgs = [(F.BASE + i * F.MS, {"header": {"seq": 100 - i, "stamp": {"sec": i, "nanosec": 0}}}) for i in range(5)]
    out, res, fs = go(tmp_path, msgs)
    assert fs == []


def test_17_cross_domain_helper_raises():
    with pytest.raises(ClockDomainError):
        ClockedValue(1, "payload_stamp:/t:header.stamp").delta_ns(ClockedValue(2, LOG_TIME_DOMAIN))
    with pytest.raises(ClockDomainError):
        ClockedValue(1, "payload_stamp:/t:header.stamp").delta_ns(ClockedValue(2, "payload_stamp:/u:header.stamp"))


def test_18_incomplete_recording(tmp_path):
    from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": stamps([1, 2, 3])}, {"/t": [(F.BASE + 99 * F.MS, st(9))]}], {"/t": HDR})
    inv = inspect_evidence(bag)
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    d = StampOrderDetector()
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), conf()))
    res = result_of(run(inv, ev, conf(), [d]), ID)
    assert res.status == "insufficient_evidence" and any(r.code == "recording_incomplete" for r in res.reasons)


def test_19_static_no_second_implementation():
    src = inspect.getsource(mod)
    assert "1_000_000_000" not in src and "10**9" not in src and "1000000000" not in src and '"nanosec"' not in src and '"sec"' not in src
    assert "header_stamp_ns(" in inspect.getsource(access)           # the single call site of the accepted M3b function
    assert "header_stamp_ns" not in src or "view.stamp" in src


def oracle_ns(sec, nsec):
    """Independent statement of the M3b rule for JSON (integral numbers only)."""
    def integral(x):
        if isinstance(x, bool) or x is None or isinstance(x, str):
            return None
        if isinstance(x, int):
            return x
        if isinstance(x, float) and x == int(x):
            return int(x)
        return None
    s, n = integral(sec), integral(nsec)
    if s is None or n is None or s < 0 or not 0 <= n < NS:
        return None
    return s * NS + n


def test_19_parity_with_m3b_random_corpus_json(tmp_path):
    rnd = random.Random(20260101)
    pool = [0, 1, -1, 5, 2**31 - 1, 2**31, 10**9, 10**9 - 1, 10**9 + 1, 4294967295, "1", True, False, 1.5, 2.0, None, 3.0, -0.0, 123456789]
    cases = [(rnd.choice(pool), rnd.choice(pool)) for _ in range(2200)]
    msgs = [(F.BASE + i * 1000, {"header": {"stamp": {"sec": s, "nanosec": n}}}) for i, (s, n) in enumerate(cases)]
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": msgs}], {"/t": HDR})
    inv, ev = X.load(bag, {"/t"})
    c = DecodeCounter()
    for m, (s, n) in zip(sorted(ev.messages["/t"], key=lambda m: (m.log_time, m.order, m.index)), cases):
        v = MessageView(m, ev, c)
        mine = v.stamp("header.stamp")[0]
        accepted = M3B.header_stamp_ns(v.root(), "header.stamp", None, None)
        assert mine == accepted == oracle_ns(s, n), (s, n)


@needs_decoder
def test_19_parity_with_m3b_random_corpus_cdr(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    rnd = random.Random(7)
    cases = [(rnd.choice([-5, -1, 0, 1, 2, 2**31 - 1]), rnd.choice([0, 1, 999_999_999, 10**9, 4294967295, 12345])) for _ in range(2100)]
    msgs = [(F.BASE + i * 1000, T[HDR](stamp=T["builtin_interfaces/msg/Time"](sec=s, nanosec=n), frame_id="x")) for i, (s, n) in enumerate(cases)]
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/t", HDR, msgs)]])
    inv, ev = X.load(bag, {"/t"})
    c = DecodeCounter()
    for m, (s, n) in zip(sorted(ev.messages["/t"], key=lambda m: (m.log_time, m.order, m.index)), cases):
        v = MessageView(m, ev, c)
        mine, label = v.stamp("stamp")
        d = v.decoded()
        accepted = M3B.header_stamp_ns(d.root, "stamp", d.types, d.root_type)
        want = None if s < 0 or not 0 <= n < NS else s * NS + n
        assert mine == accepted == want and label.startswith("schema_verified"), (s, n)


def test_20_invalid_between_valid_compares_to_last_valid(tmp_path):
    msgs = [(F.BASE, st(5)), (F.BASE + F.MS, st(6)), (F.BASE + 2 * F.MS, st(-1)), (F.BASE + 3 * F.MS, st(4))]
    out, res, fs = go(tmp_path, msgs)
    (f,) = fs
    assert f.observed_value["previous_stamp_ns"] == 6 * NS and f.observed_value["stamp_ns"] == 4 * NS
    assert any(n.code == "invalid_stamps_excluded" and n.params == {"excluded_messages_between": 1} for n in f.uncertainty)
    r = next(r for r in res.reasons if r.code == "invalid_stamps")
    assert res.status == "findings_reported" and r.count == 1


def test_21_valid_invalid_valid_duplicate(tmp_path):
    msgs = [(F.BASE, st(5)), (F.BASE + F.MS, st(-1)), (F.BASE + 2 * F.MS, st(5))]
    out, res, fs = go(tmp_path, msgs)
    (f,) = fs
    assert f.anomaly_category == "timestamp_duplicate" and f.occurrence_count == 2
    assert any(n.code == "invalid_stamps_excluded" for n in f.uncertainty)


def test_22_zero_then_zero(tmp_path):
    out, res, fs = go(tmp_path, stamps([0, 0]))
    (f,) = fs
    assert any(n.code == "zero_stamp_valid_not_assumed_unset" for n in f.uncertainty) and "unset" not in f.explanation.lower()


def test_23_all_invalid(tmp_path):
    out, res, fs = go(tmp_path, [(F.BASE + i * F.MS, st(-1 - i)) for i in range(4)])
    assert res.status == "blind_spot" and fs == [] and next(r for r in res.reasons if r.code == "invalid_stamps").count == 4


def test_24_zero_compared_as_ordinary_value(tmp_path):
    out, res, fs = go(tmp_path, stamps([0, 1, 0]))
    (f,) = fs
    assert f.anomaly_category == "timestamp_regression" and f.observed_value["previous_stamp_ns"] == 1 and f.observed_value["stamp_ns"] == 0


def test_invalid_retention_is_capped_and_counted_exactly(tmp_path):
    out, res, fs = go(tmp_path, [(F.BASE + i * F.MS, st(-1 - i)) for i in range(30)] + [(F.BASE + 99 * F.MS, st(1))])
    r = next(r for r in res.reasons if r.code == "invalid_stamps")
    assert r.count == 30 and r.detail.count("artifact:") == 5


def test_09_structure_only_label_for_json_with_note(tmp_path):
    out, res, fs = go(tmp_path, stamps([2, 1]))
    assert any(n.code == "structure_only_stamp_type" for n in fs[0].uncertainty)
    assert fs[0].observations[0].payload_stamp.stamp_type_check.startswith("structure_only")


@needs_decoder
def test_09_schema_verified_and_blind_labels_cdr(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    def hdr(s):
        return T[HDR](stamp=T["builtin_interfaces/msg/Time"](sec=s, nanosec=0), frame_id="x")
    msgs = [(F.BASE + i * F.MS, hdr(v)) for i, v in enumerate([3, 2, 1])]
    bag = X.write_cdr_bag(tmp_path / "ok", [[("/t", HDR, msgs)]])
    out, ev, inv = run_detectors(bag, [StampOrderDetector()], conf(fields=("stamp",)))
    assert len(out.findings) == 2 and not any(n.code == "structure_only_stamp_type" for f in out.findings for n in f.uncertainty)
    assert out.findings[0].observations[0].payload_stamp.stamp_type_check.startswith("schema_verified")
    bag = X.write_cdr_bag(tmp_path / "rej", [[("/t", HDR, msgs)]])
    out, ev, inv = run_detectors(bag, [StampOrderDetector()], conf(fields=("frame_id",)))
    res = result_of(out, ID)
    assert res.status == "blind_spot" and res.reasons[0].code == "rejected_type" and out.findings == []
    bag = X.write_cdr_bag(tmp_path / "nof", [[("/t", HDR, msgs)]])
    out, ev, inv = run_detectors(bag, [StampOrderDetector()], conf(fields=("nope",)))
    res = result_of(out, ID)
    assert res.status == "blind_spot" and res.reasons[0].code == "field_not_in_schema"


@needs_decoder
def test_09_payload_not_decoded_label(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    m = T[HDR](stamp=T["builtin_interfaces/msg/Time"](sec=3, nanosec=0), frame_id="x")
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/t", HDR, [(F.BASE, m), (F.BASE + F.MS, m)])]], schema_override={HDR: b"\x00garbage not a schema"})
    out, ev, inv = run_detectors(bag, [StampOrderDetector()], conf(fields=("stamp",)))
    res = result_of(out, ID)
    assert res.status == "blind_spot" and res.reasons[0].code in ("payload_not_decoded", "schema_unresolved", "rejected_type", "field_not_in_schema")


def test_09_mixed_payloads_label(tmp_path):
    import io, json
    import yaml
    from mcap.writer import CompressionType, Writer
    from tests import make_fixtures as mf
    from tests.bagtools import metadata_dict
    root = tmp_path / "mix"
    root.mkdir()
    out = io.BytesIO()
    w = Writer(out, chunk_size=2048, compression=CompressionType.NONE, use_chunking=True, enable_crcs=True, enable_data_crcs=True)
    w.start(profile="", library=mf.LIBRARY)
    s1 = w.register_schema(name=HDR, encoding="jsonschema", data=b"{}")
    s2 = w.register_schema(name=HDR, encoding="ros2msg", data=b"builtin_interfaces/Time stamp\nstring frame_id\n" + b"=" * 80 + b"\nMSG: builtin_interfaces/Time\nint32 sec\nuint32 nanosec\n")
    c1 = w.register_channel(topic="/t", message_encoding="json", schema_id=s1)
    c2 = w.register_channel(topic="/t", message_encoding="cdr", schema_id=s2)
    for i in range(4):
        w.add_message(channel_id=c1, log_time=F.BASE + 2 * i * F.MS, publish_time=0, sequence=i, data=json.dumps(st(i)).encode())
        w.add_message(channel_id=c2, log_time=F.BASE + (2 * i + 1) * F.MS, publish_time=0, sequence=i, data=b"\x00\x01\x00\x00" + bytes(16))
    w.finish()
    (root / "mix_0.mcap").write_bytes(out.getvalue())
    (root / "metadata.yaml").write_text(yaml.safe_dump(metadata_dict([("mix_0.mcap", [mf.StreamSpec("/t", tuple(F.BASE + i * F.MS for i in range(8)))])]), sort_keys=False))
    out, ev, inv = run_detectors(root, [StampOrderDetector()], conf())
    res = result_of(out, ID)
    assert res.status in ("blind_spot",) and res.reasons[0].code in ("mixed_payloads", "mixed_types_on_topic") or res.status == "blind_spot"


def test_two_fields_independent_clock_domains(tmp_path):
    msgs = [(F.BASE + i * F.MS, {"header": {"stamp": {"sec": i, "nanosec": 0}}, "other": {"sec": 10 - i, "nanosec": 0}}) for i in range(4)]
    out, res, fs = go(tmp_path, msgs, config=conf(fields=("header.stamp", "other")))
    # `other` is not schema-typed in JSON: structure-only check applies to both fields
    assert {f.clock_domain for f in fs} == {"payload_stamp:/t:other"} and len(fs) == 3


def test_segments_and_ids(tmp_path):
    PAD = [(F.BASE + i * F.MS, st(100 + i)) for i in range(40)]
    PAD2 = [(F.BASE + (200 + i) * F.MS, st(500 + i)) for i in range(40)]
    s1 = PAD + [(F.BASE + 100 * F.MS, st(90))]                       # regression inside segment 0 (prev and cur both there)
    s2 = PAD2
    ids = {}
    for n, alt in (("a", None), ("b", None), ("c", {0}), ("d", {1})):
        out, res, fs = go(tmp_path / n, segments=[s1, s2], alternate=alt, config=conf())
        reg = [f for f in fs if f.anomaly_category == "timestamp_regression"]
        assert len(reg) >= 1
        ids[n] = reg[0].finding_id
    assert ids["a"] == ids["b"] and ids["a"] != ids["c"]
    assert ids["a"] == ids["d"]                                       # direct finding: an artifact holding no occurrence does not change the id


def test_cap_exact(tmp_path):
    out, res, fs = go(tmp_path, stamps([9, 8, 7, 6, 5, 4]), config=conf(limits={"max_findings_per_detector": 2}))
    assert len(fs) == 2 and res.findings_truncated.suppressed_exact_count == 3


def test_cap_suppressed_duplicate_run_counted_once(tmp_path):
    out, res, fs = go(tmp_path, stamps([1, 1, 1, 1, 2, 2, 2]), config=conf(limits={"max_findings_per_detector": 1}))
    assert len(fs) == 1 and res.findings_truncated.suppressed_exact_count == 1


def test_wording(tmp_path):
    out, res, fs = go(tmp_path, stamps([5, 4, 4, 4]))
    assert all(find_forbidden(f.explanation) == [] for f in fs)


def test_run_closed_by_regression(tmp_path):
    out, res, fs = go(tmp_path, stamps([5, 5, 3, 3]))
    assert sorted((f.anomaly_category, f.occurrence_count) for f in fs) == [("timestamp_duplicate", 2), ("timestamp_duplicate", 2), ("timestamp_regression", 1)]


def test_bool_partition_value_is_ill_typed(tmp_path):
    msgs = [(F.BASE + i * F.MS, {"header": {"stamp": {"sec": i, "nanosec": 0}, "frame_id": True}}) for i in range(4)]
    out, res, fs = go(tmp_path, msgs, config=conf(partition=("header.frame_id",)))
    assert res.payload_malformed_field == 4 and fs == []


def test_regression_across_segments_covers_both_artifacts(tmp_path):
    s1 = [(F.BASE + i * F.MS, st(100 + i)) for i in range(40)]
    s2 = [(F.BASE + (100 + i) * F.MS, st(50 + i)) for i in range(40)]
    ids = {}
    for n, alt in (("a", None), ("b", None), ("c", {0}), ("d", {1})):
        out, res, fs = go(tmp_path / n, segments=[s1, s2], alternate=alt)
        (f,) = fs
        ids[n] = f.finding_id
        assert len(f.source) == 2
    assert ids["a"] == ids["b"] and len({ids["a"], ids["c"], ids["d"]}) == 3


def test_label_is_per_field_not_per_topic(tmp_path):
    msgs = [(F.BASE + i * F.MS, {"header": {"stamp": {"sec": 5 - i, "nanosec": 0}}, "other": {"sec": 5 - i, "nanosec": 0}}) for i in range(3)]
    out, res, fs = go(tmp_path, msgs, config=conf(fields=("header.stamp", "other")))
    assert len(fs) == 4
    for f in fs:
        fld = f.observed_value["field"]
        assert f"'{fld}.sec'" in f.observations[0].payload_stamp.stamp_type_check
