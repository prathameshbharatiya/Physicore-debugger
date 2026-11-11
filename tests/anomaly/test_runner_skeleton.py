"""Step 2 gate: X-01 (determinism), X-02 (detector order), X-10 (decode once), X-11 (selective decode) on the test-only probe."""
import json
import os
import subprocess
import sys

from physicore_collector.anomaly.runner import ordered, run, topic_schema_names
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.runner_support import Probe, config

IDS = ["tierA.ros_log", "tierA.diagnostics", "tierB.non_finite", "tierB.stamp_order"]
FULL = {
    "tierA.ros_log": {"enabled": True}, "tierA.diagnostics": {"enabled": True},
    "tierB.non_finite": {"enabled": True, "topics": ["/t"]},
    "tierB.stamp_order": {"enabled": True, "topics": ["/t"], "fields": ["header.stamp"]},
}


def bag_two_segments(tmp_path, swap=False):
    a = {"/t": [(F.BASE + 5, {"v": 1}), (F.BASE + 5, {"v": 2}), (F.BASE + 1, {"v": 0})]}
    b = {"/t": [(F.BASE + 5, {"v": 3}), (F.BASE + 9, {"v": 4})]}
    return X.write_bag(tmp_path / "bag", [b, a] if swap else [a, b])


def test_ordered_is_time_then_segment_then_index(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    keys = [(m.log_time - F.BASE, m.order, m.index) for m in ordered(ev.messages["/t"])]
    assert keys == sorted(keys)
    assert [k[0] for k in keys] == [1, 5, 5, 5, 9]
    ties = [k for k in keys if k[0] == 5]
    assert ties == sorted(ties) and len({(k[1], k[2]) for k in ties}) == 3


def test_ordering_independent_of_arrival_order(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    shuffled = list(reversed(ev.messages["/t"]))
    assert [(m.log_time, m.order, m.index) for m in ordered(shuffled)] == [(m.log_time, m.order, m.index) for m in ordered(ev.messages["/t"])]


def test_x10_decode_once_with_four_detectors(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    ds = [Probe(i, ["/t"], decode=True) for i in IDS]
    out = run(inv, ev, config(FULL), ds)
    n = len(ev.messages["/t"])
    assert out.messages_fed == n
    assert out.decode_counter.total == n and out.decode_counter.max_per_message() == 1     # X-10
    for d in ds:
        assert len(d.seen) == n


def test_x11_no_decode_when_no_detector_asks(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    ds = [Probe("tierB.non_finite", ["/t"]), Probe("tierB.stamp_order", ["/t"])]
    out = run(inv, ev, config({k: FULL[k] for k in ("tierB.non_finite", "tierB.stamp_order")}), ds)
    assert out.decode_counter.total == 0                                                  # X-11
    assert out.messages_fed == len(ev.messages["/t"])


def test_x02_detector_order_irrelevant(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    r1 = run(inv, ev, config(FULL), [Probe(i, ["/t"]) for i in IDS])
    r2 = run(inv, ev, config(FULL), [Probe(i, ["/t"]) for i in reversed(IDS)])
    assert [r.model_dump(mode="json") for r in r1.results] == [r.model_dump(mode="json") for r in r2.results]
    assert [r.detector_id for r in r1.results] == sorted(IDS)


def test_not_enabled_detector_is_not_configured_and_sees_nothing(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    p = Probe("tierA.ros_log", ["/t"])
    out = run(inv, ev, config({"tierB.non_finite": FULL["tierB.non_finite"]}), [p, Probe("tierB.non_finite", ["/t"])])
    res = {r.detector_id: r for r in out.results}
    assert res["tierA.ros_log"].status == "not_configured" and p.seen == []


def test_unbound_detector_reports_its_status(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    p = Probe("tierB.non_finite", ["/absent"])
    out = run(inv, ev, config(), [p])
    assert p.seen == [] and out.results[0].detector_id == "tierB.non_finite"


def test_x01_equal_time_ties_use_segment_then_index_regardless_of_file_order(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    p = Probe("tierB.non_finite", ["/t"])
    run(inv, ev, config(), [p])
    assert [(t, o, i) for _, t, o, i in p.seen] == sorted([(t, o, i) for _, t, o, i in p.seen])


def test_topic_schema_names_from_inventory(tmp_path):
    inv, ev = X.load(bag_two_segments(tmp_path), {"/t"})
    assert topic_schema_names(inv) == {"/t": {"synthetic/Sample"}}      # the exact name the channel recorded, nothing inferred


def test_x01_repeated_runs_byte_identical_across_hashseed_and_cwd(tmp_path):
    bag = bag_two_segments(tmp_path)
    code = (
        "import json,sys\n"
        "from tests.anomaly import fixtures as X\n"
        "from tests.anomaly.runner_support import Probe, config\n"
        "from physicore_collector.anomaly.runner import run\n"
        "inv, ev = X.load(__import__('pathlib').Path(sys.argv[1]), {'/t'})\n"
        "ds=[Probe(i,['/t'],decode=True) for i in ['tierB.non_finite','tierA.ros_log']]\n"
        "o=run(inv,ev,config({'tierB.non_finite':{'enabled':True,'topics':['/t']},'tierA.ros_log':{'enabled':True}}),ds)\n"
        "print(json.dumps([[r.model_dump(mode='json') for r in o.results],[d.seen for d in ds],o.messages_fed],sort_keys=True))\n"
    )
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    outs = []
    for seed, cwd in (("0", root), ("1", root), ("12345", root), ("random", root), ("7", root)):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": root}
        outs.append(subprocess.run([sys.executable, "-c", code, str(bag)], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout)
    assert len(set(outs)) == 1 and json.loads(outs[0])


def test_time_wins_over_segment_order(tmp_path):
    late_first = {"/t": [(F.BASE + 9, {"v": "late-in-seg0"})]}
    early_second = {"/t": [(F.BASE + 1, {"v": "early-in-seg1"})]}
    inv, ev = X.load(X.write_bag(tmp_path / "b", [late_first, early_second]), {"/t"})
    got = [(m.log_time - F.BASE, m.order) for m in ordered(ev.messages["/t"])]
    assert got == [(1, 1), (9, 0)]
