"""CS-04a..04j (TEST_PLAN 1, Rev 2): finding_id covers EVERY contributing artifact, proved with FX-ART alternates (valid artifacts with genuinely
different bytes and a digest the real loader recomputes). The CS-04j mutants (tools/mutants_step8.py) must each fail at least one of CS-04b/04c/04i."""
import pytest

from physicore_collector.anomaly.detectors.tier_a_log import RosLogDetector
from physicore_collector.anomaly.detectors.tier_b_array_length import ArrayLengthDetector
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors
from tests.anomaly.test_tier_a_log import PAD, PAD2, log, stream

ID = "tierA.ros_log"
RCL = "rcl_interfaces/msg/Log"
PAD3 = stream([log(20, "pad", f"info3 {i}") for i in range(40)], start=F.BASE + 600 * F.MS)


def conf(retain=5):
    return cfg({ID: {"enabled": True}}, {"max_retained_observations": retain})


def go(root, segments, *, alternate=None, config=None, order=None):
    bag = X.write_typed_bag(root / "bag", [{"/rosout": s} for s in segments], {"/rosout": RCL}, alternate=alternate)
    out, ev, inv = run_detectors(bag, [RosLogDetector()], config or conf())
    return out.findings, bag


def segs(extra_s3=False):
    s1 = PAD + stream([log(40, "nav", "boom")] * 2, start=F.BASE + 100 * F.MS)           # first occurrences, in S1
    s2 = PAD2 + stream([log(40, "nav", "boom")] * 2, start=F.BASE + 300 * F.MS)          # later occurrences, in S2
    return [s1, s2, PAD3] if extra_s3 else [s1, s2]


def key(f):
    return [(o.topic, o.message_index, o.order, o.log_time_ns, o.field_path, o.value) for o in f.observations]


def test_CS_04a_two_artifacts_listed_and_in_the_id(tmp_path):
    (f,), bag = go(tmp_path / "a", segs())
    assert f.occurrence_count == 4 and len(f.source) == 2
    assert [(s.artifact_ref, s.sha256) for s in f.source] == sorted((s.artifact_ref, s.sha256) for s in f.source)
    shas = set(X.file_digests(bag).values())
    assert {s.sha256 for s in f.source} == shas                     # the real digests, recomputed from the files
    (g,), _ = go(tmp_path / "b", segs())
    assert g.finding_id == f.finding_id


def test_CS_04b_replacing_S1_changes_the_id(tmp_path):
    (base,), _ = go(tmp_path / "base", segs())
    (alt,), _ = go(tmp_path / "alt", segs(), alternate={0})
    assert key(alt) == key(base) and alt.occurrence_count == base.occurrence_count
    assert alt.finding_id != base.finding_id
    assert [s.sha256 for s in alt.source] != [s.sha256 for s in base.source]


def test_CS_04c_replacing_S2_changes_the_id_differently(tmp_path):
    (base,), _ = go(tmp_path / "base", segs())
    (a1,), _ = go(tmp_path / "a1", segs(), alternate={0})
    (a2,), _ = go(tmp_path / "a2", segs(), alternate={1})
    assert key(a2) == key(base)
    assert a2.finding_id != base.finding_id and a2.finding_id != a1.finding_id


def test_CS_04d_unrelated_third_segment_does_not_change_the_id(tmp_path):
    (base,), _ = go(tmp_path / "base", segs(True))
    (alt,), _ = go(tmp_path / "alt", segs(True), alternate={2})
    assert alt.finding_id == base.finding_id and len(alt.source) == 2


def test_CS_04e_baseline_dependent_finding_tracks_baseline_only_segments(tmp_path):
    """Array-length with a mode baseline: segment 1 holds messages that entered the baseline but are not in the finding."""
    typ = "sensor_msgs/msg/LaserScan"
    c = cfg({"tierB.array_length": {"enabled": True, "rules": [{"topic": "/t", "path": "ranges", "baseline": "mode", "min_observations": 5, "min_share": "0.5"}]}})

    def scans(lengths, start):
        return [(start + i * F.MS, {"ranges": [1.0] * n}) for i, n in enumerate(lengths)]
    s0 = scans([5] * 20 + [4], F.BASE)
    s1 = scans([5] * 60, F.BASE + 100 * F.MS)

    def ids(n, alt):
        bag = X.write_typed_bag(tmp_path / n / "bag", [{"/t": s0}, {"/t": s1}], {"/t": typ}, alternate=alt)
        out, ev, inv = run_detectors(bag, [ArrayLengthDetector()], c)
        (f,) = out.findings
        assert len(f.source) == 2
        return f.finding_id
    a, b, c1, c0 = ids("a", None), ids("b", None), ids("c", {1}), ids("d", {0})
    assert a == b and len({a, c1, c0}) == 3


def test_CS_04f_discovery_order_does_not_matter(tmp_path):
    (f,), bag = go(tmp_path / "a", segs())
    from physicore_collector.anomaly.runner import topic_schema_names, wanted_topics
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    from physicore_collector.anomaly.runner import run
    inv = inspect_evidence(bag)
    d = RosLogDetector()
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), conf()))
    # present the same messages with their segment lists reversed
    for t in ev.messages:
        ev.messages[t] = list(reversed(ev.messages[t]))
    out = run(inv, ev, conf(), [d])
    (g,) = out.findings
    assert g.finding_id == f.finding_id and [s.model_dump() for s in g.source] == [s.model_dump() for s in f.source]


def test_CS_04i_id_tracks_artifacts_of_unretained_occurrences(tmp_path):
    c = conf(retain=1)
    (base,), _ = go(tmp_path / "base", segs(), config=c)
    assert base.observations_truncated and len(base.observations) == 1 and base.occurrence_count == 4
    (alt,), _ = go(tmp_path / "alt", segs(), config=c, alternate={1})           # only S2 replaced; every retained ref lies in S1
    assert alt.observations_truncated and key(alt) == key(base)
    assert alt.finding_id != base.finding_id
    assert len(alt.source) == 2


def test_CS_04i_retained_refs_come_from_the_first_segment_only(tmp_path):
    (f,), _ = go(tmp_path / "a", segs(), config=conf(retain=1))
    assert len({o.artifact_ref for o in f.observations}) == 1 and f.occurrence_count == 4


def test_CS_04z_alternate_has_identical_messages(tmp_path):
    from tests.anomaly.fixtures import fingerprint, load
    s = [{"/rosout": x} for x in segs()]
    a = X.write_bag(tmp_path / "a" / "bag", s)
    b = X.write_bag(tmp_path / "b" / "bag", s, alternate={0})
    ia, ea = load(a, {"/rosout"})
    ib, eb = load(b, {"/rosout"})
    assert ea.complete and eb.complete and fingerprint(ea, "/rosout") == fingerprint(eb, "/rosout")
