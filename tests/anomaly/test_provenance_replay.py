"""X-03 / X-05 provenance replay, X-15 independence, X-10/X-11 decode counts, X-06 coverage, X-07 insufficient observations, X-17/X-18."""
import json
from pathlib import Path

import pytest

from physicore_collector.anomaly import service
from physicore_collector.anomaly.report import new_report
from physicore_collector.anomaly.runner import run
from physicore_collector.investigator.cdr import decoder_info
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, run_detectors
from tests.anomaly.tools.replay_verifier import verify_report

needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")
NS = 10**9
P = 100 * F.MS
TYPES = {"/rosout": "rcl_interfaces/msg/Log", "/scan": "sensor_msgs/msg/LaserScan", "/hdr": "std_msgs/msg/Header", "/tick": "std_msgs/msg/Empty",
         "/val": "std_msgs/msg/Float64"}
DETS = {
    "tierA.ros_log": {"enabled": True, "topics": "auto"},
    "tierB.publishing_gap": {"enabled": True, "topics": ["/tick"], "min_intervals": 10, "multiplier": "3", "absolute_floor_ns": 1},
    "tierB.stamp_order": {"enabled": True, "topics": ["/hdr"], "fields": ["header.stamp"], "partition_by": []},
    "tierB.array_length": {"enabled": True, "rules": [{"topic": "/scan", "path": "ranges", "expected_length": 4}]},
    "tierB.non_finite": {"enabled": True, "topics": ["/val", "/scan"], "include_paths": [], "exclude_paths": []},
}


def streams():
    nan = float("nan")
    tick, t = [], F.BASE
    for i in range(30):
        t += P * (12 if i == 15 else 1)
        tick.append((t, {}))
    return {
        "/rosout": [(F.BASE + i * F.MS, {"level": 40 if i % 2 else 20, "name": "nav", "msg": f"m{i}"}) for i in range(6)],
        "/scan": [(F.BASE + i * F.MS, {"ranges": [1.0] * (4 if i != 3 else 2)}) for i in range(6)],
        "/hdr": [(F.BASE + i * F.MS, {"header": {"stamp": {"sec": 10 - (i % 3), "nanosec": 5}, "frame_id": "a"}}) for i in range(6)],
        "/tick": tick,
        "/val": [(F.BASE + i * F.MS, {"data": nan if i in (1, 2) else 1.5}) for i in range(5)],
    }


def build(tmp):
    bag = X.write_typed_bag(tmp / "bag", [streams()], TYPES)
    config = cfg(DETS)
    out, ev, inv = run_detectors(bag, service.all_detectors(), config)
    return bag, config, out, ev, inv


def as_report(out):
    return new_report(tool_version="t", exit_code=0, exit_reasons=[], results=out.results, findings=out.findings)


def test_X03_every_finding_and_observation_replays_from_the_files(tmp_path):
    bag, config, out, ev, inv = build(tmp_path)
    fired = {f.detector_id for f in out.findings}
    assert fired == {"tierA.ros_log", "tierB.publishing_gap", "tierB.stamp_order", "tierB.array_length", "tierB.non_finite"}
    rep = as_report(out)
    assert sum(len(f.observations) for f in rep.findings) >= 8
    assert verify_report(rep, bag) == []


def test_X05_the_verifier_itself_fails_on_corrupted_references(tmp_path):
    bag, config, out, ev, inv = build(tmp_path)
    base = as_report(out)
    assert verify_report(base, bag) == []
    for what in ("message_index", "log_time_ns", "topic", "sha256", "value"):
        rep = base.model_copy(deep=True)
        f = next(f for f in rep.findings if f.detector_id == "tierA.ros_log")
        o = f.observations[0]
        if what == "message_index":
            o.message_index += 1
        elif what == "log_time_ns":
            o.log_time_ns += 1
        elif what == "topic":
            o.topic = "/other"
        elif what == "sha256":
            o.sha256 = "0" * 64
        else:
            o.value = 99
        assert verify_report(rep, bag), what
    rep = base.model_copy(deep=True)
    f = next(f for f in rep.findings if f.detector_id == "tierA.ros_log")
    f.observations[0].message_index = 10_000
    assert any("no message at that index" in p for p in verify_report(rep, bag))
    rep = base.model_copy(deep=True)
    f = next(f for f in rep.findings if f.detector_id == "tierB.stamp_order")
    f.observations[0].payload_stamp.value_ns += 1
    assert any("stamp" in p for p in verify_report(rep, bag))


@needs_decoder
def test_X03_cdr_findings_replay_with_the_independent_serializer(tmp_path):
    from tests import cdr_fixtures as cf
    import numpy as np
    ts = cf.typestore()
    T = ts.types

    def scan(n, nan=False, t=F.BASE):
        r = [1.0] * n
        if nan and n:
            r[0] = float("nan")
        return T["sensor_msgs/msg/LaserScan"](header=cf._hdr(t, "l"), angle_min=0.0, angle_max=1.0, angle_increment=0.1, time_increment=0.0, scan_time=0.1,
                                              range_min=0.0, range_max=10.0, ranges=np.array(r, dtype=np.float32), intensities=np.array([], dtype=np.float32))
    msgs = [(F.BASE + i * F.MS, scan(4 if i != 2 else 1, nan=(i == 3), t=F.BASE + i * F.MS)) for i in range(5)]
    logs = [(F.BASE + i * F.MS, T["rcl_interfaces/msg/Log"](stamp=cf._t(F.BASE), level=40, name="nav", msg="boom", file="f", function="g", line=1)) for i in range(3)]
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/scan", "sensor_msgs/msg/LaserScan", msgs), ("/rosout", "rcl_interfaces/msg/Log", logs)]])
    out, ev, inv = run_detectors(bag, service.all_detectors(), cfg({**{k: DETS[k] for k in ("tierA.ros_log", "tierB.array_length")},
                                                                    "tierB.non_finite": {"enabled": True, "topics": ["/scan"], "include_paths": [], "exclude_paths": []}}))
    assert {f.detector_id for f in out.findings} == {"tierA.ros_log", "tierB.array_length", "tierB.non_finite"}
    assert verify_report(as_report(out), bag) == []


# ---------------------------------------------------------------------------------------------------- X-15 independence
def test_X15_detectors_do_not_interfere(tmp_path):
    bag, config, out, ev, inv = build(tmp_path)
    allf = {f.finding_id for f in out.findings}
    alone = set()
    for d in service.all_detectors():
        o, e, i = run_detectors(bag, [d], config)
        alone |= {f.finding_id for f in o.findings}
    assert alone == allf


# ---------------------------------------------------------------------------------------------------- X-10 / X-11 decode counts
def test_X11_timing_only_configuration_decodes_nothing(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [streams()], TYPES)
    config = cfg({"tierB.publishing_gap": DETS["tierB.publishing_gap"]})
    out, ev, inv = run_detectors(bag, service.all_detectors(), config)
    assert out.decode_counter.total == 0


def test_X10_each_message_decoded_at_most_once_with_four_decoding_detectors(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/rosout": streams()["/rosout"]}], {"/rosout": TYPES["/rosout"]})
    dets = {"tierA.ros_log": DETS["tierA.ros_log"], "tierB.non_finite": {"enabled": True, "topics": ["/rosout"], "include_paths": [], "exclude_paths": []},
            "tierB.array_length": {"enabled": True, "rules": [{"topic": "/rosout", "path": "msg", "expected_length": 1}]},
            "tierB.stamp_order": {"enabled": True, "topics": ["/rosout"], "fields": ["stamp"], "partition_by": []}}
    out, ev, inv = run_detectors(bag, service.all_detectors(), cfg(dets))
    assert out.decode_counter.total == 6 and out.decode_counter.max_per_message() == 1


# ---------------------------------------------------------------------------------------------------- X-06 coverage of incomplete recordings
def truncate(path): path.write_bytes(path.read_bytes() + b"y")
def drop(path): path.unlink()


@pytest.mark.parametrize("damage", [truncate, drop])
def test_X06_incomplete_recordings_keep_findings_and_downgrade_absence(tmp_path, damage):
    s = streams()
    bag = X.write_typed_bag(tmp_path / "bag", [{"/rosout": s["/rosout"]}, {"/rosout": [(F.BASE + 99 * F.MS, {"level": 20, "name": "n", "msg": "x"})]}],
                            {"/rosout": TYPES["/rosout"]})
    damage(bag / "bag_1.mcap")
    out, ev, inv = run_detectors(bag, service.all_detectors(), cfg({"tierA.ros_log": DETS["tierA.ros_log"]}))
    res = next(r for r in out.results if r.detector_id == "tierA.ros_log")
    assert not ev.complete and res.status == "findings_reported" and out.findings
    assert all(f.coverage.topic_coverage == "incomplete" and f.coverage.incomplete_reasons for f in out.findings)
    clean = X.write_typed_bag(tmp_path / "c" / "bag", [{"/rosout": [(F.BASE, {"level": 20, "name": "n", "msg": "x"})]}, {"/rosout": [(F.BASE + F.MS, {"level": 20, "name": "n", "msg": "x"})]}],
                              {"/rosout": TYPES["/rosout"]})
    damage(clean / "bag_1.mcap")
    out2, ev2, inv2 = run_detectors(clean, service.all_detectors(), cfg({"tierA.ros_log": DETS["tierA.ros_log"]}))
    assert next(r for r in out2.results if r.detector_id == "tierA.ros_log").status == "insufficient_evidence"


# ---------------------------------------------------------------------------------------------------- X-07 insufficient observations
@pytest.mark.parametrize("n", [0, 1, 9])
def test_X07_gap_with_too_few_intervals_is_insufficient_never_no_anomaly(tmp_path, n):
    msgs = [(F.BASE + i * P, {}) for i in range(n + 1 if n else 0)]
    streams_ = {"/tick": msgs} if msgs else {"/tick": [], "/other": [(F.BASE, {})]}
    bag = X.write_typed_bag(tmp_path / "bag", [streams_], {t: "std_msgs/msg/Empty" for t in streams_})
    out, ev, inv = run_detectors(bag, service.all_detectors(), cfg({"tierB.publishing_gap": DETS["tierB.publishing_gap"]}))
    res = next(r for r in out.results if r.detector_id == "tierB.publishing_gap")
    assert res.status in ("insufficient_evidence", "not_applicable") and res.status != "no_anomaly_observed"


def test_X07_gap_at_the_minimum_is_evaluated(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/tick": [(F.BASE + i * P, {}) for i in range(11)]}], {"/tick": "std_msgs/msg/Empty"})
    out, ev, inv = run_detectors(bag, service.all_detectors(), cfg({"tierB.publishing_gap": DETS["tierB.publishing_gap"]}))
    assert next(r for r in out.results if r.detector_id == "tierB.publishing_gap").status == "no_anomaly_observed"


# ---------------------------------------------------------------------------------------------------- X-17 / X-18 decoder extra
@pytest.mark.skipif(decoder_info().available, reason="needs an environment WITHOUT the decoder extra")
def test_X17_X18_without_the_decoder_json_still_works_and_cdr_is_a_blind_spot(tmp_path):
    bag, config, out, ev, inv = build(tmp_path)                       # JSON everywhere: never needs the decoder (X-18)
    assert {f.detector_id for f in out.findings} == {"tierA.ros_log", "tierB.publishing_gap", "tierB.stamp_order", "tierB.array_length", "tierB.non_finite"}
    example = Path(__file__).resolve().parents[2] / "examples" / "m3a" / "cdr_example.mcap"
    out2, ev2, inv2 = run_detectors(example, service.all_detectors(), cfg({"tierB.non_finite": {"enabled": True, "topics": sorted({t.topic for t in inv_topics(example)}),
                                                                                                "include_paths": [], "exclude_paths": []}}))
    res = next(r for r in out2.results if r.detector_id == "tierB.non_finite")
    assert res.status in ("insufficient_evidence", "blind_spot") and res.messages_undecodable >= 1


def inv_topics(path):
    from physicore_collector.inventory import inspect_evidence
    return inspect_evidence(path).topics


def test_X01_findings_are_totally_ordered_whatever_the_discovery_order(tmp_path):
    bag, config, out, ev, inv = build(tmp_path)
    a = new_report(tool_version="t", exit_code=0, exit_reasons=[], results=out.results, findings=out.findings)
    b = new_report(tool_version="t", exit_code=0, exit_reasons=[], results=list(reversed(out.results)), findings=list(reversed(out.findings)))
    assert [f.finding_id for f in a.findings] == [f.finding_id for f in b.findings] and len(a.findings) > 5
    key = [(f.observations[0].log_time_ns if f.observations else f.first_observed_at.value_ns, f.detector_id, f.topic) for f in a.findings]
    assert key == sorted(key)
