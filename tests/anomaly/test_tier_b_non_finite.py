"""NF-* (TEST_PLAN 9): tierB.non_finite."""
import math
import struct

import pytest


class _LazyNp:
    def __getattr__(self, n):
        import numpy
        return getattr(numpy, n)


np = _LazyNp()

from physicore_collector.anomaly import bitinfo
from physicore_collector.anomaly.access import DecodeCounter, MessageView
from physicore_collector.anomaly.detectors.tier_b_non_finite import NonFiniteDetector, classify, compile_glob
from physicore_collector.anomaly.findings import find_forbidden
from physicore_collector.investigator import cdr as cdrmod
from physicore_collector.investigator.cdr import decoder_info
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

ID = "tierB.non_finite"
F32, F64, SCAN = "std_msgs/msg/Float32", "std_msgs/msg/Float64", "sensor_msgs/msg/LaserScan"
needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")
NAN, INF = float("nan"), float("inf")



def conf(topics=("/t",), include=(), exclude=(), limits=None):
    return cfg({ID: {"enabled": True, "topics": list(topics), "include_paths": list(include), "exclude_paths": list(exclude)}}, limits)


def raw32(bits):
    return b"\x00\x01\x00\x00" + struct.pack("<I", bits)


def raw64(bits):
    return b"\x00\x01\x00\x00" + struct.pack("<Q", bits)


def cdr_run(tmp_path, items, typ, *, config=None, topic="/t"):
    bag = X.write_cdr_bag(tmp_path / "bag", [[(topic, typ, [(F.BASE + i * F.MS, p) for i, p in enumerate(items)])]])
    out, ev, inv = run_detectors(bag, [NonFiniteDetector()], config or conf())
    return out, result_of(out, ID), out.findings


def json_run(tmp_path, items, *, config=None, typ=F64, extra=None):
    streams = {"/t": [(F.BASE + i * F.MS, p) for i, p in enumerate(items)], **(extra or {})}
    bag = X.write_typed_bag(tmp_path / "bag", [streams], {t: typ for t in streams})
    out, ev, inv = run_detectors(bag, [NonFiniteDetector()], config or conf())
    return out, result_of(out, ID), out.findings


def scan(ranges, t=F.BASE):
    from tests import cdr_fixtures as cf
    ts = cf.typestore()
    return ts.types[SCAN](header=cf._hdr(t, "l"), angle_min=0.0, angle_max=1.0, angle_increment=0.1, time_increment=0.0, scan_time=0.1, range_min=0.0,
                          range_max=10.0, ranges=np.array(ranges, dtype=np.float32), intensities=np.array([], dtype=np.float32))


# ------------------------------------------------------------------------------------------------ unit
def test_classify():
    assert classify(NAN) == "nan" and classify(INF) == "inf"
    assert classify(-float('inf')) == "-inf" and classify(1.5) is None and classify(0) is None and classify(True) is None and classify("nan") is None
    assert classify(None) is None


def test_glob():
    assert compile_glob("ranges").match("ranges[12]") and compile_glob("ranges[*]").match("ranges[3]") and not compile_glob("ranges[3]").match("ranges[4]")
    assert compile_glob("poses[*].position").match("poses[3].position.x") and not compile_glob("range").match("ranges[1]")
    assert not compile_glob("a.b").match("a.bc") and compile_glob("a.b").match("a.b")


# ------------------------------------------------------------------------------------------------ NF-01 .. NF-02f (CDR)
@needs_decoder
def test_01_float32_nan_and_inf(tmp_path):
    out, res, fs = cdr_run(tmp_path, [raw32(0x7FC00000), raw32(0x7F800000), raw32(0xFF800000)], F32)
    assert res.status == "findings_reported" and len(fs) == 3
    by = {f.observed_value["value_class"]: f for f in fs}
    assert set(by) == {"nan", "inf", "-inf"} and len({f.finding_id for f in fs}) == 3
    nan = by["nan"].observations[0]
    assert nan.value == "nan" and nan.bit_info.sign == 0 and nan.bit_info.bits_hex is None and nan.bit_info.bits_basis == "not_guaranteed"
    assert "nan_payload_bits_not_guaranteed" in {n.code for n in by["nan"].uncertainty}
    assert by["inf"].observations[0].bit_info.bits_hex == "7f800000" and by["-inf"].observations[0].bit_info.bits_hex == "ff800000"
    assert by["-inf"].observations[0].bit_info.sign == 1 and by["-inf"].observations[0].value == "-inf"
    assert "nan_payload_bits_not_guaranteed" not in {n.code for n in by["inf"].uncertainty}


F64_PATTERNS = [0x7FF8000000000000, 0xFFF8000000000000, 0x7FF8000000000001, 0x7FF0000000000001, 0xFFF0000000000001, 0x7FF4000000000000,
                0x7FF0000000000000, 0xFFF0000000000000]


@needs_decoder
def test_02_float64_patterns_exact(tmp_path):
    items = []
    for b in F64_PATTERNS:
        items += [raw64(b), raw64(0x3FF0000000000000)]          # a clean message between them splits the runs
    out, res, fs = cdr_run(tmp_path, items, F64)
    assert len(fs) == len(F64_PATTERNS)
    for f, b in zip(sorted(fs, key=lambda f: f.first_observed_at.value_ns), F64_PATTERNS):
        bi = f.observations[0].bit_info
        assert bi.bits_hex == f"{b:016x}" and bi.bits_basis == "reencoded_from_decoded_float64" and bi.width == 64
        assert bi.sign == (b >> 63) and "nan_payload_bits_not_guaranteed" not in {n.code for n in f.uncertainty}


@needs_decoder
def test_02b_float32_signalling_nan(tmp_path):
    pats = [0x7F800001, 0xFF800001, 0x7FA00000]
    items = []
    for b in pats:
        items += [raw32(b), raw32(0x3F800000)]
    out, res, fs = cdr_run(tmp_path, items, F32)
    assert len(fs) == 3
    for f, b in zip(sorted(fs, key=lambda f: f.first_observed_at.value_ns), pats):
        o = f.observations[0]
        assert o.value == "nan" and o.bit_info.sign == (b >> 31) and o.bit_info.bits_hex is None and o.bit_info.bits_basis == "not_guaranteed"
        assert "nan_payload_bits_not_guaranteed" in {n.code for n in f.uncertainty}
        assert "payload" not in (f.explanation + str(f.observed_value)).lower().replace("nan_payload", "")


@needs_decoder
def test_02c_decoder_characterisation(tmp_path):
    """Pins DESIGN 6.6.1: float32 signalling NaNs come out of the decoder quiet; float64 patterns survive. Not a feature test: if this changes,
    the design must be reviewed."""
    f32 = {0x7F800001: 0x7FC00001, 0xFF800001: 0xFFC00001, 0x7FA00000: 0x7FE00000}
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/a", F32, [(F.BASE + i * F.MS, raw32(b)) for i, b in enumerate(f32)]),
                                              ("/b", F64, [(F.BASE + i * F.MS, raw64(b)) for i, b in enumerate(F64_PATTERNS)])]])
    inv, ev = X.load(bag, {"/a", "/b"})
    c = DecodeCounter()
    got32 = [struct.unpack(">I", struct.pack(">f", MessageView(m, ev, c).root().data))[0] for m in sorted(ev.messages["/a"], key=lambda m: m.log_time)]
    assert got32 == list(f32.values())
    got64 = [struct.unpack(">Q", struct.pack(">d", MessageView(m, ev, c).root().data))[0] for m in sorted(ev.messages["/b"], key=lambda m: m.log_time)]
    assert got64 == F64_PATTERNS


@needs_decoder
def test_02d_self_check_failure_withholds_bits(tmp_path, monkeypatch):
    monkeypatch.setattr(bitinfo, "_f64_to_bits", lambda x: 0)
    assert bitinfo.self_check() is False
    out, res, fs = cdr_run(tmp_path, [raw64(0x7FF8000000000000)], F64)
    bi = fs[0].observations[0].bit_info
    assert bi.bits_hex is None and bi.bits_basis == "not_reported" and bi.sign == 0


def test_02d2_self_check_passes_normally():
    assert bitinfo.self_check() is True


@needs_decoder
def test_02e_nan_sign(tmp_path):
    out, res, fs = cdr_run(tmp_path / "a", [raw32(0xFFC00000)], F32)
    assert fs[0].observations[0].bit_info.sign == 1
    out, res, fs = cdr_run(tmp_path / "b", [raw64(0xFFF8000000000000)], F64)
    assert fs[0].observations[0].bit_info.sign == 1 and fs[0].observations[0].bit_info.bits_hex == "fff8000000000000"


def test_02f_json_tokens_have_no_bit_info(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": NAN}])
    assert len(fs) == 1 and fs[0].observations[0].bit_info is None and fs[0].observations[0].value == "nan"


# ------------------------------------------------------------------------------------------------ NF-03 .. NF-05
@needs_decoder
def test_03_exact_paths(tmp_path):
    r = [1.0] * 20
    r[12] = NAN
    out, res, fs = cdr_run(tmp_path, [scan(r)], SCAN)
    assert len(fs) == 1 and fs[0].observations[0].field_path == "ranges[12]" and fs[0].field_path_template == "ranges[*]"
    assert fs[0].field_path is None


@needs_decoder
def test_03b_nested_struct_and_array_of_structs(tmp_path):
    from tests import cdr_fixtures as cf
    ts = cf.typestore()
    T = ts.types

    def pose(x):
        return T["geometry_msgs/msg/Pose"](position=T["geometry_msgs/msg/Point"](x=x, y=0.0, z=0.0),
                                           orientation=T["geometry_msgs/msg/Quaternion"](x=0.0, y=0.0, z=0.0, w=1.0))
    m = T["geometry_msgs/msg/PoseArray"](header=cf._hdr(F.BASE, "m"), poses=[pose(0.0), pose(1.0), pose(2.0), pose(NAN)])
    out, res, fs = cdr_run(tmp_path, [m], "geometry_msgs/msg/PoseArray")
    assert len(fs) == 1 and fs[0].observations[0].field_path == "poses[3].position.x" and fs[0].field_path_template == "poses[*].position.x"
    assert fs[0].observations[0].bit_info.width == 64 and fs[0].observations[0].bit_info.bits_basis == "reencoded_from_decoded_float64"


@needs_decoder
def test_03c_nested_covariance(tmp_path):
    from tests import cdr_fixtures as cf
    m = cf.odometry(F.BASE, 0.0, 0.0, 0.0, 0.0)
    m.pose.covariance[7] = NAN
    out, res, fs = cdr_run(tmp_path, [m], "nav_msgs/msg/Odometry")
    assert [o.field_path for o in fs[0].observations] == ["pose.covariance[7]"]


@needs_decoder
def test_04_both_widths_and_no_conversion_artefacts(tmp_path):
    flt_max = float(np.finfo(np.float32).max)
    out, res, fs = cdr_run(tmp_path / "a", [scan([1.0, NAN, flt_max, -flt_max, INF])], SCAN)
    assert len(fs) == 2 and {f.observed_value["value_class"] for f in fs} == {"nan", "inf"}
    assert all(f.observations[0].bit_info.width == 32 for f in fs)
    out, res, fs = cdr_run(tmp_path / "b", [raw64(0x7FF8000000000000)], F64)
    assert fs[0].observations[0].bit_info.width == 64


@needs_decoder
def test_05_extremes_are_not_findings(tmp_path):
    dbl = [1.7976931348623157e308, -1.7976931348623157e308, 5e-324, -0.0, 2.2250738585072014e-308]
    out, res, fs = cdr_run(tmp_path / "a", [raw64(struct.unpack("<Q", struct.pack("<d", v))[0]) for v in dbl], F64)
    assert fs == [] and res.status == "no_anomaly_observed"
    flt = [float(np.finfo(np.float32).max), float(np.finfo(np.float32).tiny) / 2, -0.0]
    out, res, fs = cdr_run(tmp_path / "b", [scan(flt)], SCAN)
    assert fs == [] and res.status == "no_anomaly_observed"


# ------------------------------------------------------------------------------------------------ NF-06 .. NF-09 (JSON)
def test_06_json_tokens(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": NAN}, {"data": INF}, {"data": -INF}])
    assert {f.observed_value["value_class"] for f in fs} == {"nan", "inf", "-inf"} and res.messages_undecodable == 0
    assert any(r.code == "json_nonfinite_tokens_parsed" and r.count == 3 for r in res.reasons)


def test_06b_raw_token_text(tmp_path):
    out, res, fs = json_run(tmp_path, [b'{"data": NaN, "other": [1.5, Infinity, -Infinity]}'])
    assert sorted((f.observed_value["value_class"], f.observations[0].field_path) for f in fs) == [("-inf", "other[2]"), ("inf", "other[1]"), ("nan", "data")]


def test_06c_still_invalid_json_is_undecodable(tmp_path):
    out, res, fs = json_run(tmp_path, [b"{not json", {"data": 1.5}])
    assert res.messages_undecodable == 1 and fs == [] and res.status == "insufficient_evidence"


def test_07_json_null_is_not_a_finding(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": None}, {"data": 1.5}])
    assert fs == [] and res.status == "no_anomaly_observed" and any(r.code == "json_null_not_inspected" and r.count == 1 for r in res.reasons)


def test_08_numeric_string_is_not_a_finding(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": "nan"}, {"data": "-Infinity"}, {"data": "1.5"}])
    assert fs == [] and any(r.code == "numeric_string_not_interpreted" and r.count == 2 for r in res.reasons)


def test_09_integer_bool_string_fields_ignored(tmp_path):
    out, res, fs = json_run(tmp_path, [{"a": 5, "b": True, "c": "NaN text", "d": "hello nan", "e": 0}])
    assert fs == [] and res.status == "no_anomaly_observed"


# ------------------------------------------------------------------------------------------------ NF-10 .. NF-17
@needs_decoder
def test_10_exact_counts_beyond_retention(tmp_path):
    out, res, fs = cdr_run(tmp_path, [scan([NAN] * 1000)], SCAN, config=conf(limits={"max_retained_observations": 5}))
    f = fs[0]
    assert f.occurrence_count == 1000 and f.observed_value["offending_leaves"] == 1000 and f.observed_value["messages_with_offenders"] == 1
    assert len(f.observations) == 5 and f.observations_truncated and "retained_observations_truncated" in {n.code for n in f.uncertainty}


@needs_decoder
def test_10b_three_messages(tmp_path):
    out, res, fs = cdr_run(tmp_path, [scan([NAN] * 1000, F.BASE + i * F.MS) for i in range(3)], SCAN, config=conf(limits={"max_retained_observations": 5}))
    assert len(fs) == 1 and fs[0].occurrence_count == 3000 and fs[0].observed_value["messages_with_offenders"] == 3 and len(fs[0].observations) == 5


def test_10c_json_counts_exact(tmp_path):
    out, res, fs = json_run(tmp_path, [{"v": [NAN] * 300 + [1.0] * 5} for _ in range(2)], config=conf(limits={"max_retained_observations": 3}))
    assert fs[0].occurrence_count == 600 and len(fs[0].observations) == 3 and [o.field_path for o in fs[0].observations] == ["v[0]", "v[1]", "v[2]"]


def test_10d_no_early_exit(tmp_path):
    # the offender is the LAST leaf of the message: an early-exit walker would miss it
    out, res, fs = json_run(tmp_path, [{"a": [1.5] * 500, "z": NAN}])
    assert fs and fs[0].observations[0].field_path == "z"


def test_11_run_grouping(tmp_path):
    items = [{"data": NAN}] * 50 + [{"data": 1.0}] * 5 + [{"data": NAN}] * 3
    out, res, fs = json_run(tmp_path, items)
    assert [f.occurrence_count for f in fs] == [50, 3] and len({f.finding_id for f in fs}) == 2


def test_11b_classes_group_separately(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": NAN}, {"data": INF}, {"data": NAN}])
    assert sorted(f.observed_value["value_class"] for f in fs) == ["inf", "nan", "nan"]


def test_12_include_exclude(tmp_path):
    msg = {"a": {"x": NAN, "y": NAN}, "arr": [NAN, 1.0, NAN]}
    def paths(**kw):
        out, res, fs = json_run(tmp_path / str(len(list(tmp_path.iterdir()))), [msg], config=conf(**kw))
        return sorted(o.field_path for f in fs for o in f.observations)
    assert paths() == ["a.x", "a.y", "arr[0]", "arr[2]"]
    assert paths(exclude=["a.y"]) == ["a.x", "arr[0]", "arr[2]"]
    assert paths(include=["arr"]) == ["arr[0]", "arr[2]"]
    assert paths(include=["arr[*]"], exclude=["arr[2]"]) == ["arr[0]"]
    assert paths(include=["a"], exclude=["a.x", "a.y"]) == []


def test_13_only_opted_in_topics_are_decoded(tmp_path):
    other = [(F.BASE + i * F.MS, {"data": NAN}) for i in range(7)]
    out, res, fs = json_run(tmp_path, [{"data": 1.0}] * 3, extra={"/other": other})
    assert out.decode_counter.total == 3 and out.messages_fed == 3 and fs == []


@needs_decoder
def test_14_opaque_bytes_not_scanned(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    nan_bytes = struct.pack("<f", NAN) * 8
    m = T["std_msgs/msg/UInt8MultiArray"](layout=T["std_msgs/msg/MultiArrayLayout"](dim=[], data_offset=0), data=np.frombuffer(nan_bytes, dtype=np.uint8))
    out, res, fs = cdr_run(tmp_path, [m], "std_msgs/msg/UInt8MultiArray")
    assert fs == [] and res.status == "no_anomaly_observed"


@needs_decoder
def test_14b_image_payloads_are_out_of_scope_of_the_accepted_decoder(tmp_path):
    """Characterisation: M3a refuses image / point-cloud payloads, so they are undecodable (counted), never silently scanned."""
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    img = T["sensor_msgs/msg/Image"](header=cf._hdr(F.BASE, "c"), height=1, width=8, encoding="32FC1", is_bigendian=0, step=32,
                                     data=np.frombuffer(struct.pack("<f", NAN) * 8, dtype=np.uint8))
    out, res, fs = cdr_run(tmp_path, [img], "sensor_msgs/msg/Image")
    assert fs == [] and res.messages_undecodable == 1 and res.status == "insufficient_evidence"


@needs_decoder
def test_15_payload_too_large_is_counted(tmp_path, monkeypatch):
    out0, _, _ = cdr_run(tmp_path / "x", [raw64(0x3FF0000000000000)], F64)
    monkeypatch.setattr(cdrmod, "MAX_CDR_MESSAGE_BYTES", 8)
    out, res, fs = cdr_run(tmp_path / "y", [raw64(0x7FF8000000000000), raw64(0x3FF0000000000000)], F64)
    assert res.messages_undecodable == 2 and res.status == "insufficient_evidence" and fs == []
    assert any(r.code == "payload_too_large" and r.count == 2 for r in res.reasons)


def test_16_clean_complete(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": 1.0 + i} for i in range(5)])
    assert fs == [] and res.status == "no_anomaly_observed"


def test_16b_topic_not_recorded(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": 1.0}], config=conf(topics=("/nope",)))
    assert res.status == "not_applicable" and res.reasons[0].code == "topic_not_recorded"


def test_17_explanation_has_no_cause_words(tmp_path):
    out, res, fs = json_run(tmp_path, [{"data": NAN}])
    e = fs[0].explanation
    assert not find_forbidden(e) and "non-finite" in e and "intentional" in e
    for word in ("sensor failure", "fault", "broken", "because", "caused", "malfunction"):
        assert word not in e.lower()


def test_18_findings_cap_exact(tmp_path):
    items = []
    for i in range(10):
        items += [{"data": NAN}, {"data": 1.0}]
    out, res, fs = json_run(tmp_path, items, config=conf(limits={"max_findings_per_detector": 4}))
    assert len(fs) == 4 and res.findings_truncated.suppressed_exact_count == 6


def test_18b_suppressed_run_is_not_recounted(tmp_path):
    items = [{"data": 1.0}] + [{"data": NAN}] * 30
    out, res, fs = json_run(tmp_path, items, config=conf(limits={"max_findings_per_detector": 1}))
    assert len(fs) == 1 and fs[0].occurrence_count == 30
    items = [{"a": NAN, "b": NAN}] * 20
    out, res, fs = json_run(tmp_path / "b", items, config=conf(limits={"max_findings_per_detector": 1}))
    assert len(fs) == 1 and res.findings_truncated.suppressed_exact_count == 1


def test_19_incomplete_recording(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": [(F.BASE, {"data": 1.0})]}, {"/t": [(F.BASE + 99 * F.MS, {"data": 1.0})]}], {"/t": F64})
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    out, ev, inv = run_detectors(bag, [NonFiniteDetector()], conf())
    res = result_of(out, ID)
    assert not ev.complete and res.status == "insufficient_evidence" and any(r.code == "recording_incomplete" for r in res.reasons)


def test_20_cross_segment_run_lists_both_artifacts(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": [(F.BASE, {"data": NAN})]}, {"/t": [(F.BASE + F.MS, {"data": NAN})]}], {"/t": F64})
    out, ev, inv = run_detectors(bag, [NonFiniteDetector()], conf())
    assert len(out.findings) == 1 and len(out.findings[0].source) == 2 and out.findings[0].occurrence_count == 2
