"""GAP-* (TEST_PLAN 5). Synthetic recordings; an independent oracle recomputes the expected intervals."""
import math
from fractions import Fraction

import pytest

from physicore_collector.anomaly.detectors.timing import ceil_fraction, lower_median
from physicore_collector.anomaly.detectors.tier_b_gap import PublishingGapDetector
from physicore_collector.anomaly.findings import find_forbidden
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

ID = "tierB.publishing_gap"
TYPE = "sensor_msgs/msg/LaserScan"
P = 100 * F.MS


def conf(topics=("/t",), min_intervals=10, multiplier="3", floor=1, limits=None):
    return cfg({ID: {"enabled": True, "topics": list(topics), "min_intervals": min_intervals, "multiplier": multiplier, "absolute_floor_ns": floor}}, limits)


def msgs(times):
    return [(t, {"x": i}) for i, t in enumerate(times)]


def regular(n, p=P, start=F.BASE):
    return [start + i * p for i in range(n)]


def go(tmp_path, segments, *, config=None, alternate=None, extra=None):
    segs = []
    for s in segments:
        d = {"/t": msgs(s)}
        d.update(extra or {})
        segs.append(d)
    types = {"/t": TYPE, **{k: TYPE for k in (extra or {})}}
    bag = X.write_typed_bag(tmp_path / "bag", segs, types, alternate=alternate)
    out, ev, inv = run_detectors(bag, [PublishingGapDetector()], config or conf())
    return out, result_of(out, ID), out.findings


def oracle(times, multiplier, floor, min_intervals):
    """Independent re-statement of DESIGN 6.1 (own median, own ceil)."""
    ts = sorted(times)
    d = [b - a for a, b in zip(ts, ts[1:])]
    if len(d) < min_intervals:
        return "too_few", []
    B = sorted(d)[(len(d) - 1) // 2]
    if B == 0:
        return "zero", []
    G = max(math.ceil(Fraction(multiplier) * B), floor)
    return "ok", [(ts[i], ts[i + 1], x) for i, x in enumerate(d) if x > G]


def test_helpers_lower_median_and_ceil():
    assert lower_median([1, 2, 3, 4]) == 2 and lower_median([4, 1, 3, 2, 9]) == 3 and lower_median([7]) == 7
    assert lower_median([10, 10, 10, 10, 30, 30, 30, 30]) == 10              # GAP-09: lower, not upper (30)
    assert ceil_fraction(Fraction(5, 2)) == 3 and ceil_fraction(Fraction(4, 2)) == 2 and ceil_fraction(Fraction(1, 3)) == 1


def test_01_regular_stream(tmp_path):
    out, res, fs = go(tmp_path, [regular(50)])
    assert res.status == "no_anomaly_observed" and fs == []


def test_02_one_gap_exact_endpoints(tmp_path):
    t = regular(20) + regular(20, start=F.BASE + 19 * P + 10 * P)
    out, res, fs = go(tmp_path, [t])
    (f,) = fs
    assert f.observed_value["interval_ns"] == 10 * P and f.observed_value["threshold_ns"] == 3 * P and f.observed_value["baseline_interval_ns"] == P
    assert f.first_observed_at.value_ns == F.BASE + 19 * P and f.last_observed_at.value_ns == F.BASE + 29 * P
    assert [o.message_index for o in f.observations] == [19, 20] and f.occurrence_count == 1
    assert f.severity == "notice" and f.severity_basis == "detector_policy" and f.anomaly_category == "publishing_gap"
    assert f.expected_value == P and f.expected_source == "recording_baseline" and f.threshold.name == "gap_threshold_ns"
    codes = {n.code for n in f.uncertainty}
    assert {"baseline_from_recording", "capture_loss_indistinguishable_from_silence"} <= codes and f.timestamp_basis == "mcap_log_time"


def with_interval(d, n=30, p=P):
    t = regular(n, p)
    return t[:15] + [x + (d - p) for x in t[15:]]


def test_03_boundary_exactly_threshold_is_not_a_finding(tmp_path):
    out, res, fs = go(tmp_path / "a", [with_interval(3 * P)])
    assert fs == []
    out, res, fs = go(tmp_path / "b", [with_interval(3 * P + 1)])
    assert len(fs) == 1 and fs[0].observed_value["interval_ns"] == 3 * P + 1


def test_04_fractional_multiplier_ceil_exact(tmp_path):
    p = 1001                                                    # odd baseline; 2.5 x 1001 = 2502.5 -> G = 2503
    c = conf(multiplier="2.5", floor=1)
    out, res, fs = go(tmp_path / "a", [with_interval(2503, p=p)], config=c)
    assert fs == []
    out, res, fs = go(tmp_path / "b", [with_interval(2504, p=p)], config=c)
    assert len(fs) == 1 and fs[0].observed_value["threshold_ns"] == 2503
    out, res, fs = go(tmp_path / "c", [with_interval(2502, p=p)], config=c)
    assert fs == []


def test_05_floor_dominates(tmp_path):
    c = conf(multiplier="2", floor=500)                         # multiplier x B = 200 < floor 500
    out, res, fs = go(tmp_path / "a", [with_interval(400, p=100)], config=c)
    assert fs == []
    out, res, fs = go(tmp_path / "b", [with_interval(500, p=100)], config=c)
    assert fs == []
    out, res, fs = go(tmp_path / "c", [with_interval(501, p=100)], config=c)
    assert len(fs) == 1 and fs[0].observed_value["threshold_ns"] == 500


@pytest.mark.parametrize("n,status", [(11, "no_anomaly_observed"), (10, "insufficient_evidence")])
def test_06_min_intervals_boundary(tmp_path, n, status):
    out, res, fs = go(tmp_path, [regular(n)])
    assert res.status == status
    if status != "no_anomaly_observed":
        assert any(r.code == "too_few_intervals" for r in res.reasons)


@pytest.mark.parametrize("n", [0, 1, 2])
def test_07_tiny_streams(tmp_path, n):
    out, res, fs = go(tmp_path, [regular(n)])
    assert res.status == "insufficient_evidence" and fs == []


def test_08_median_zero_no_silent_fallback(tmp_path):
    t = [F.BASE] * 7 + [F.BASE + 5 * P, F.BASE + 6 * P, F.BASE + 30 * P, F.BASE + 31 * P, F.BASE + 40 * P, F.BASE + 41 * P]
    out, res, fs = go(tmp_path, [t], config=conf(min_intervals=5))
    assert res.status == "insufficient_evidence" and any(r.code == "degenerate_baseline_zero" for r in res.reasons) and fs == []


def test_09_lower_median_end_to_end(tmp_path):
    iv = [10, 10, 10, 10, 30, 30, 30, 30]
    t = [F.BASE]
    for d in iv:
        t.append(t[-1] + d)
    out, res, fs = go(tmp_path, [t], config=conf(min_intervals=8, multiplier="2", floor=1))
    assert len(fs) == 4 and all(f.observed_value["baseline_interval_ns"] == 10 and f.observed_value["threshold_ns"] == 20 for f in fs)
    assert all(any(n.code == "baseline_contaminated_possible" for n in f.uncertainty) for f in fs)           # 4/8 >= 25 %


def test_10_two_gaps_ordered(tmp_path):
    t = regular(20)
    t2 = [x + 10 * P for x in t[10:]]
    t3 = [x + 30 * P for x in t[10:]]
    times = t[:10] + t2[:5] + [x + 20 * P for x in t3[5:]]
    out, res, fs = go(tmp_path, [times])
    o = oracle(times, "3", 1, 10)[1]
    assert [(f.first_observed_at.value_ns, f.last_observed_at.value_ns, f.observed_value["interval_ns"]) for f in fs] == o and len(fs) == 2


def test_11_12_boundary_gaps_never_emitted(tmp_path):
    t = regular(30, start=F.BASE + 50 * P)                                         # first message "late", last "early"
    out, res, fs = go(tmp_path, [t], extra={"/other": msgs(regular(100))})
    assert fs == [] and res.status == "no_anomaly_observed"
    assert any(r.code == "boundary_gaps_not_evaluated" for r in res.reasons)


def test_13_contamination_flag_threshold(tmp_path):
    base = regular(40)
    t = base[:20] + [x + 50 * P for x in base[20:]]       # one gap only: 1/39 intervals
    out, res, fs = go(tmp_path / "a", [t])
    assert not any(n.code == "baseline_contaminated_possible" for n in fs[0].uncertainty)
    # many gaps: every 4th interval is a gap -> 25 % of intervals exceed
    t, cur = [F.BASE], F.BASE
    for i in range(40):
        cur += 10 * P if i % 4 == 3 else P
        t.append(cur)
    out, res, fs = go(tmp_path / "b", [t])
    assert len(fs) == 10 and all(any(n.code == "baseline_contaminated_possible" for n in f.uncertainty) for f in fs)


def test_14_bursty_fixture_matches_oracle(tmp_path):
    t = []
    cur = F.BASE
    for burst in range(8):
        for j in range(10):
            t.append(cur)
            cur += F.MS
        cur += 500 * F.MS
    c = conf(min_intervals=5, multiplier="3", floor=F.MS)
    out, res, fs = go(tmp_path, [t], config=c)
    status, want = oracle(t, "3", F.MS, 5)
    assert status == "ok"
    assert [(f.first_observed_at.value_ns, f.last_observed_at.value_ns) for f in fs] == [(a, b) for a, b, _ in want]
    assert fs and all("capture_loss_indistinguishable_from_silence" in {n.code for n in f.uncertainty} for f in fs)


def test_15_incomplete_recording(tmp_path):
    from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    gap = regular(20) + regular(20, start=F.BASE + 29 * P)
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": msgs(gap)}, {"/t": msgs(regular(5, start=F.BASE + 100 * P))}], {"/t": TYPE})
    inv = inspect_evidence(bag)
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    d = PublishingGapDetector()
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), conf()))
    out = run(inv, ev, conf(), [d])
    res = result_of(out, ID)
    assert not ev.complete and res.status == "findings_reported" and out.findings
    assert all(f.coverage.topic_coverage == "incomplete" and f.coverage.incomplete_reasons for f in out.findings)
    # without findings the same incompleteness makes "no anomaly observed" impossible
    out2, res2, fs2 = go(tmp_path / "ok", [regular(30)])
    assert res2.status == "no_anomaly_observed"


def test_15b_incomplete_without_findings_is_insufficient(tmp_path):
    from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": msgs(regular(30))}, {"/t": msgs(regular(5, start=F.BASE + 100 * P))}], {"/t": TYPE})
    inv = inspect_evidence(bag)
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    d = PublishingGapDetector()
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), conf()))
    res = result_of(run(inv, ev, conf(), [d]), ID)
    assert res.status == "insufficient_evidence" and any(r.code == "recording_incomplete" for r in res.reasons)


def test_16_segment_order_does_not_change_the_result(tmp_path):
    t = regular(20) + regular(20, start=F.BASE + 19 * P + 10 * P)
    a, b = t[:20], t[20:]                                    # the gap straddles the segment boundary
    o1, r1, f1 = go(tmp_path / "a", [a, b])
    o2, r2, f2 = go(tmp_path / "b", [b, a])                  # later times in segment 0
    key = lambda fs: [(f.first_observed_at.value_ns, f.last_observed_at.value_ns, f.observed_value["interval_ns"]) for f in fs]
    assert key(f1) == key(f2) == [(F.BASE + 19 * P, F.BASE + 29 * P, 10 * P)] and r1.status == r2.status


def test_16b_equal_times_follow_segment_then_index(tmp_path):
    t = regular(30)
    out, res, fs = go(tmp_path, [t[:15] + [t[15]] * 2, t[15:]])          # duplicate times across the segment boundary
    assert res.status in ("no_anomaly_observed", "findings_reported")


def test_17_mixed_types_on_topic_blind_spot_and_merge(tmp_path):
    import io
    import json
    from mcap.writer import CompressionType, Writer
    from tests import make_fixtures as mf
    from tests.bagtools import metadata_dict
    import yaml

    def bag(types):
        root = tmp_path / ("m" + "".join(t[-1] for t in types))
        root.mkdir()
        out = io.BytesIO()
        w = Writer(out, chunk_size=2048, compression=CompressionType.NONE, use_chunking=True, enable_crcs=True, enable_data_crcs=True)
        w.start(profile="", library=mf.LIBRARY)
        ev = []
        for ci, ty in enumerate(types):
            sid = w.register_schema(name=ty, encoding="jsonschema", data=b'{"type":"object"}')
            cid = w.register_channel(topic="/t", message_encoding="json", schema_id=sid)
            for i in range(ci, 40, 2):
                ev.append((F.BASE + i * P, ci, i, cid, json.dumps({"i": i}).encode()))
        for t_, _c, i, cid, data in sorted(ev):
            w.add_message(channel_id=cid, log_time=t_, publish_time=t_, sequence=i, data=data)
        w.finish()
        (root / f"{root.name}_0.mcap").write_bytes(out.getvalue())
        (root / "metadata.yaml").write_text(yaml.safe_dump(metadata_dict([(f"{root.name}_0.mcap", [mf.StreamSpec("/t", tuple(F.BASE + i * P for i in range(40)))])]), sort_keys=False))
        return root
    out, ev, inv = run_detectors(bag(["a/msg/A", "a/msg/B"]), [PublishingGapDetector()], conf())
    r = result_of(out, ID)
    assert r.status == "blind_spot" and r.reasons[0].code == "mixed_types_on_topic"
    out, ev, inv = run_detectors(bag(["a/msg/A", "a/msg/A"]), [PublishingGapDetector()], conf())
    assert result_of(out, ID).status == "no_anomaly_observed"            # two channels, one type: merged by the deterministic order


def test_18_wording(tmp_path):
    out, res, fs = go(tmp_path, [regular(20) + regular(20, start=F.BASE + 29 * P)])
    (f,) = fs
    assert find_forbidden(f.explanation) == []
    for w in ("stopped", "died", "dropped", "lost", "crash"):
        assert w not in f.explanation.lower()
    assert "No message was captured between" in f.explanation


def test_19_cap_exact_total(tmp_path):
    t, cur = [F.BASE], F.BASE
    for i in range(60):
        cur += 10 * P if i % 10 == 9 else P
        t.append(cur)
    out, res, fs = go(tmp_path, [t], config=conf(limits={"max_findings_per_detector": 2}))
    assert len(fs) == 2 and res.findings_truncated.suppressed_exact_count == 4 and res.findings_count == 2


def test_not_recorded_topic_is_not_applicable(tmp_path):
    out, res, fs = go(tmp_path, [regular(30)], config=conf(topics=("/nope",)))
    assert res.status == "not_applicable" and res.reasons[0].code == "topic_not_recorded"


def test_baseline_dependent_id_covers_every_segment(tmp_path):
    # CS-04e: the gap sits in segment 0; segment 1 also holds messages of the topic that entered the baseline.
    s1 = regular(20) + regular(20, start=F.BASE + 29 * P)
    s2 = regular(60, start=F.BASE + 49 * P)
    ids = {}
    for n, alt in (("a", None), ("b", None), ("c", {1}), ("d", {0})):
        out, res, fs = go(tmp_path / n, [s1, s2], alternate=alt)
        (f,) = fs
        ids[n] = f.finding_id
        assert len(f.source) == 2
    assert ids["a"] == ids["b"] and len({ids["a"], ids["c"], ids["d"]}) == 3


def test_endpoints_in_two_segments(tmp_path):
    out, res, fs = go(tmp_path, [regular(25), regular(20, start=F.BASE + 24 * P + 10 * P)])
    (f,) = fs
    assert f.observations[0].order == 0 and f.observations[1].order == 1 and f.observations[0].message_index == 24 and f.observations[1].message_index == 0


def test_runs_deterministically(tmp_path):
    t = regular(20) + regular(20, start=F.BASE + 29 * P)
    a = go(tmp_path / "a", [t])[2][0].model_dump(mode="json")
    b = go(tmp_path / "b", [t])[2][0].model_dump(mode="json")
    assert a == b


def test_other_topics_do_not_affect_result(tmp_path):
    t = regular(20) + regular(20, start=F.BASE + 29 * P)
    a = go(tmp_path / "a", [t])[2][0]
    b = go(tmp_path / "b", [t], extra={"/zzz": msgs(regular(7, start=F.BASE - 5 * P))})[2][0]
    strip = lambda v: {k: x for k, x in v.items() if k not in ("earlier", "later")}
    assert strip(a.observed_value) == strip(b.observed_value)       # message_index is a position in the file, which holds other topics too
    assert a.observed_value["earlier"]["log_time_ns"] == b.observed_value["earlier"]["log_time_ns"]
