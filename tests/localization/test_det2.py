"""Step 9: ordering rules that make the output independent of discovery / hash order (targets of the MUT-DET family)."""
from __future__ import annotations

import json
import random

from physicore_collector.localization import blockers as B
from physicore_collector.localization import output as O
from physicore_collector.localization.precedence import build_lanes
from physicore_collector.localization.service import run_localization
from physicore_collector.localization.symptoms import apply_cap, symptom_id
from physicore_collector.localization.dependencies import parse_dependencies
from tests.localization import l2
from tests.localization.l1 import sym
from tests.localization.test_chn import pipe
from tests.localization.test_con import dep
from tests.localization.test_svc import DETS, deps

LOG = "mcap_log_time"


def test_cap_keeps_the_first_in_canonical_order_whatever_the_input_order():
    a = [sym("s-c", 5, 5), sym("s-a", 5, 5), sym("s-b", 5, 5)]                      # same interval: the id decides
    b = [sym("s-x", 5, 9), sym("s-z", 5, 7), sym("s-y", 5, 8)]                      # same lower: the upper bound decides before the id
    for pool, want in ((a, ["s-a"]), (b, ["s-z"])):
        rnd = random.Random(3)
        for _ in range(6):
            p = pool[:]
            rnd.shuffle(p)
            kept, suppressed = apply_cap(p, 1)
            assert [s.symptom_id for s in kept] == want and suppressed == 2
    kept, _ = apply_cap([sym("s-b", 9), sym("s-a", 9, 9)], 2)
    assert [s.symptom_id for s in kept] == ["s-a", "s-b"]


def test_symptom_id_does_not_depend_on_placement_order():
    p = [(LOG, 1, 2, "received_message"), ("stamp:x", 3, 4, "payload_stamp_value"), ("clock:b", 0, 9, "payload_stamp_value")]
    assert symptom_id("m3c_finding", "f1", p) == symptom_id("m3c_finding", "f1", p[::-1]) == symptom_id("m3c_finding", "f1", [p[1], p[2], p[0]])


def test_lanes_come_out_sorted_whatever_the_symptom_order():
    syms = [sym("s1", 1, 1, lane="zz"), sym("s2", 2, 2, lane="aa"), sym("s3", 3, 3, lane="mm"), sym("s4", 4, 4, lane="bb")]
    for order in (syms, syms[::-1], [syms[2], syms[0], syms[3], syms[1]]):
        lanes, _ = build_lanes(order, {})
        assert [l.domain for l in lanes] == ["aa", "bb", "mm", "zz"]


def test_blockers_of_a_lane_are_sorted():
    syms = [sym("s1", 1, 1)]
    lanes, _ = build_lanes(syms, {LOG: ["b-3", "b-1", "b-2", "b-1"]})
    assert lanes[0].earliest.blocker_ids == ["b-1", "b-2", "b-3"]


def test_endpoints_and_ancestors_are_sorted():
    names = [f"n{i:02d}" for i in range(30)]
    random.Random(1).shuffle(names)
    g = dep([(f"e{i}", names[i], names[(i + 1) % 30], None) for i in range(29)])
    assert g.endpoints == sorted(g.endpoints) and len(g.endpoints) == 30
    anc = g.ancestors(names[29])
    assert anc == sorted(anc) and len(anc) >= 5


def test_links_are_listed_by_source_node_then_target_node():
    kids = [f"c{i}" for i in range(9)]
    g = dep([(f"e{i}", "root", k, None) for i, k in enumerate(kids)])
    syms = [sym("r", 1, 1, topic="root")] + [sym(f"k{i}", 10 + i, 10 + i, topic=k) for i, k in enumerate(kids)]
    ana, _, res = pipe(syms, g)
    pairs = [(l.from_node, l.to_node) for l in res.links]
    assert pairs == sorted(pairs) and len(pairs) == 9


def test_links_of_a_pair_are_listed_lane_by_lane_in_sorted_lane_order():
    g = dep([("e1", "a", "b", None)])
    lanes = ["zz", "bb", "mm", "aa"]
    syms = [sym("x", 1, 1, lane=lanes[0], topic="a", secondary=[(l, 1, 1) for l in lanes[1:]]), sym("y", 10, 10, lane=lanes[0], topic="b", secondary=[(l, 10, 10) for l in lanes[1:]])]
    _, _, res = pipe(syms, g)
    assert [l.lane for l in res.links] == sorted(lanes)


def test_node_order_of_the_graph_walk_is_sorted():
    names = [f"n{i}" for i in range(8)]
    g = dep([(f"e{i}", names[i], "sink", None) for i in range(8)])
    syms = [sym(f"s{i}", 1 + i, 1 + i, topic=names[i]) for i in range(8)] + [sym("t", 100, 100, topic="sink")]
    _, _, res = pipe(syms, g)
    assert [l.from_node for l in res.links] == sorted(l.from_node for l in res.links)


def test_analysis_blockers_come_out_sorted_by_id():
    g = dep([("e1", "a", "b", 5), ("e2", "a", "m", 5), ("e3", "m", "b", 5)])
    syms = [sym(f"a{i}", i, i, topic="a") for i in range(4)] + [sym(f"b{i}", 100 + i, 100 + i, topic="b") for i in range(4)]
    seen = 0
    from physicore_collector.localization.consistency import analyse_links
    for n in range(1, 14):                                  # the suppressed count is part of the id, so the natural (cap, pair, depth) order is sometimes unsorted
        for lim in ({"links": n, "paths_per_pair": 1}, {"links": n, "paths_per_pair": 1, "path_depth": 1}):
            _, bl = analyse_links(syms, g, None, coverage={}, limits=lim)
            ids = [b.blocker_id for b in bl]
            assert ids == sorted(ids), (lim, ids)
            seen += len(ids) >= 2
    assert seen >= 3


def test_blocker_set_is_sorted_by_id_and_caveats_by_key(tmp_path):
    topics = {"/rosout": (l2.LOG, [(l2.at(i), l2.log(40)) for i in range(12)])}
    bag = l2.build_bag(tmp_path, topics)
    cfg = l2.write_config(tmp_path, {"tierA.ros_log": {"enabled": True}, "tierB.array_length": {"enabled": True, "rules": [{"topic": "/rosout", "path": "msg", "expected_length": 3}]}},
                          {"max_retained_observations": 3})
    rep = run_localization(bag, cfg)
    ids = [b.blocker_id for b in rep.blockers]
    assert len(ids) >= 2 and ids == sorted(ids)
    for n in (4, 6, 9, 12):                                 # build_blockers collects in source order; the set must be id-sorted whatever that order is
        sub = tmp_path / f"v{n}"
        sub.mkdir()
        b2 = l2.build_bag(sub, {"/rosout": (l2.LOG, [(l2.at(i), l2.log(40)) for i in range(n)])})
        c2 = l2.write_config(sub, {"tierA.ros_log": {"enabled": True}, "tierA.diagnostics": {"enabled": True, "topics": ["/missing"]},
                                   "tierB.array_length": {"enabled": True, "rules": [{"topic": "/rosout", "path": "msg", "expected_length": 3}]}}, {"max_retained_observations": 2})
        ids = [b.blocker_id for b in run_localization(b2, c2).blockers]
        assert ids == sorted(ids)


def test_blockers_by_lane_lists_are_sorted_and_unique():
    sy = [sym("s1", 1, 1), sym("s2", 2, 2, lane="zz")]
    bl = [B.make_blocker("recording_incomplete", B.SourceRecord(kind="inventory_record", ref="r", field="f"), count=1,
                         artifacts=[B.ArtifactDigest(artifact_ref="artifact:a.mcap", sha256="b" * 64)])]
    out = B.blockers_by_lane(bl, [LOG, "zz"])
    assert all(v == sorted(set(v)) for v in out.values())


def test_blocker_artifacts_are_sorted_by_reference():
    arts = [B.ArtifactDigest(artifact_ref=f"artifact:{n}.mcap", sha256="c" * 64) for n in ("z", "a", "m", "b")]
    b = B.make_blocker("recording_incomplete", B.SourceRecord(kind="inventory_record", ref="r", field="f"), count=1, artifacts=arts)
    assert [a.artifact_ref for a in b.artifacts] == sorted(a.artifact_ref for a in arts)


def test_identity_lines_are_sorted_by_kind():
    s = sym("s1", 1, 1, identities={"hardware_id": "h1", "diagnostic_name": "d1", "battery_serial": "b1", "logger_name": "l1"})
    ln = "\n".join(l for n, l in O.render_lines(_report_with(s)) if n == "symptom_identity")
    kinds = [l.split(": ")[1].split(" ")[0] for l in ln.splitlines()]
    assert kinds == sorted(kinds) and len(kinds) == 4


def _report_with(s):
    from physicore_collector.localization.service import run_localization as _r
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    bag = l2.build_bag(tmp, {"/rosout": (l2.LOG, [(l2.at(1), l2.log(40, "nav"))])})
    rep = _r(bag, l2.write_config(tmp, {"tierA.ros_log": {"enabled": True}}))
    return rep.model_copy(update={"symptoms": [s]})


def _bindings_doc(order):
    b = [{"node": "motor", "topic": "/diagnostics", "identity": {"name": "m1"}}, {"node": "arm", "topic": "/diagnostics", "identity": {"name": "a1"}},
         {"node": "arm", "topic": "/joint", "identity": {"hardware_id": "h"}}]
    return json.dumps({"dependency_schema_version": 1, "declared_by": "t", "nodes": [{"id": "motor"}, {"id": "arm"}], "bindings": [b[i] for i in order],
                       "edges": [{"id": "e1", "from": "motor", "to": "arm", "relation": "data_flow"}]}).encode()


def test_semantic_hash_ignores_binding_order():
    hs = {parse_dependencies(_bindings_doc(o), name="d.json").graph.semantic_hash for o in ((0, 1, 2), (2, 1, 0), (1, 2, 0))}
    assert len(hs) == 1


def test_detector_selection_order_does_not_change_the_parameters_hash(tmp_path):
    bag = l2.build_bag(tmp_path, {"/rosout": (l2.LOG, [(l2.at(1), l2.log(40))])})
    cfg = l2.write_config(tmp_path, DETS)
    a = run_localization(bag, cfg, detector_ids=["tierA.ros_log", "tierA.diagnostics"]).parameters_hash
    b = run_localization(bag, cfg, detector_ids=["tierA.diagnostics", "tierA.ros_log"]).parameters_hash
    assert a == b


def test_reported_symptoms_are_in_symptom_id_order(tmp_path):
    topics = {"/rosout": (l2.LOG, [(l2.at(i + 1), l2.log(40, f"logger{i}")) for i in range(8)])}
    bag = l2.build_bag(tmp_path, topics)
    rep = run_localization(bag, l2.write_config(tmp_path, {"tierA.ros_log": {"enabled": True}}))
    ids = [s.symptom_id for s in rep.symptoms]
    assert len(ids) >= 6 and ids == sorted(ids)


def test_blocker_set_is_id_sorted_when_collection_order_is_not(tmp_path):
    topics = {"/rosout": (l2.LOG, [(l2.at(i + 1), l2.log(40, f"logger{i}")) for i in range(9)])}
    bag = l2.build_bag(tmp_path, topics)
    for k, path in enumerate(("msg", "a", "b", "c", "d", "e", "f", "g")):    # ids are content hashes: vary both blockers until collection order is unsorted
        cfg = l2.write_config(tmp_path, {"tierA.ros_log": {"enabled": True}, "tierB.array_length": {"enabled": True, "rules": [{"topic": "/rosout", "path": path, "expected_length": 3}]}})
        ids = [b.blocker_id for b in run_localization(bag, cfg, limits={"symptoms": k + 1}).blockers]
        assert ids == sorted(ids) and len(ids) >= 2
