"""CON-01..20, ADV-01..04/09/12/13/17 (link part), UNC-05 (TEST_PLAN). Labels are checked against the independent brute-force oracle in oracle.py and
against hand-computed boundary values; chain-level halves of ADV-01..03/17 belong to Step 7."""
import json
import random

import pytest
from pydantic import ValidationError

from physicore_collector.localization import blockers as B
from physicore_collector.localization import consistency as C
from physicore_collector.localization.dependencies import parse_dependencies
from physicore_collector.localization.report import find_floats, find_forbidden_keys, find_forbidden_values
from tests.localization import l2, oracle as O
from tests.localization.l1 import LOG, sym

CAUSAL = "consistent_with_propagation"


def dep(edges, bindings=None):
    """edges: (id, from, to, window|None). Endpoints not starting with '/' become declared nodes."""
    nodes = sorted({e for ed in edges for e in ed[1:3] if not e.startswith("/")} | {b["node"] for b in (bindings or [])})
    doc = {"dependency_schema_version": 1, "declared_by": "tests",
           "edges": [{"id": i, "from": a, "to": b, "relation": "data_flow", **({"window_ns": w} if w is not None else {})} for i, a, b, w in edges]}
    if nodes:
        doc["nodes"] = [{"id": n} for n in nodes]
    if bindings:
        doc["bindings"] = bindings
    r = parse_dependencies(json.dumps(doc).encode(), name="d.json")
    assert r.graph is not None, r.problems
    return r.graph


def run(symptoms, graph, **kw):
    return C.analyse_links(symptoms, graph, None, coverage=kw.pop("coverage", {}), **kw)


def labels(res):
    return sorted((l.from_symptom, l.to_symptom, l.label) for l in res.links)


A, Bt = "/a", "/b"


def pair(xlo, xhi, ylo, yhi, window=None, **kw):
    return run([sym("x", xlo, xhi, topic=A), sym("y", ylo, yhi, topic=Bt)], dep([("e1", A, Bt, window)]), **kw)[0]


# ------------------------------------------------------------------------------------------------------------- CON-01..07

def test_CON_01_strictly_before_no_window():
    r = pair(10, 10, 20, 20)
    assert [(l.label, l.sep_min_ns, l.sep_max_ns, l.window_check.result) for l in r.links] == [(CAUSAL, 10, 10, "no_window")]


def test_CON_02_boundary_equal_to_window_is_consistent():
    r = pair(10, 10, 20, 20, window=10)
    assert [(l.label, l.window_check.result) for l in r.links] == [(CAUSAL, "within")]


def test_CON_03_one_past_the_window_is_outside():
    r = pair(10, 10, 21, 21, window=10)
    assert [(l.label, l.sep_min_ns, l.window_check.result) for l in r.links] == [("outside_declared_window", 11, "outside")]


def test_CON_03b_window_is_compared_with_the_minimum_separation():
    r = pair(0, 5, 20, 40, window=15)                 # sep_min 15 == window although sep_max is 40
    assert r.links[0].label == CAUSAL and (r.links[0].sep_min_ns, r.links[0].sep_max_ns) == (15, 40)
    assert pair(0, 5, 21, 40, window=15).links[0].label == "outside_declared_window"


def test_CON_04_downstream_strictly_before_upstream():
    r = pair(20, 20, 10, 10)
    assert [(l.label, l.window_check.result) for l in r.links] == [("inconsistent_order", "not_applicable")]
    assert (r.links[0].order.earlier.lower_ns, r.links[0].order.later.lower_ns, r.links[0].order.relation) == (10, 20, "before")


def test_CON_05_overlap_is_undetermined():
    assert [l.label for l in pair(10, 30, 20, 40).links] == ["undetermined"]


@pytest.mark.parametrize("x,y", [((10, 20), (20, 30)), ((10, 10), (10, 10)), ((20, 30), (10, 20))])
def test_CON_06_touching_and_equal_bounds_are_unordered(x, y):
    r = pair(*x, *y)
    assert [(l.label, l.order.relation) for l in r.links] == [("undetermined", "unordered")]


def test_CON_07_wide_gap_interval_is_undetermined_not_ordered():
    r = pair(0, 100, 50, 50)
    assert r.links[0].label == "undetermined"
    assert pair(0, 49, 50, 50).links[0].label == CAUSAL


# ------------------------------------------------------------------------------------------------------------- CON-08..14

def test_CON_08_different_lanes_never_linked_and_counted():
    xs = sym("x", 10, 10, topic=A)
    ys = sym("y", 20, 20, topic=Bt, lane="payload_stamp:/b:header.stamp")
    r, _ = run([xs, ys], dep([("e1", A, Bt, None)]))
    assert r.links == [] and r.cross_lane_pairs_not_compared == 1 and r.links_total == 0
    # a symptom with two lanes still links in the lane it shares
    both = sym("y", 20, 20, topic=Bt, secondary=[("payload_stamp:/b:header.stamp", 25, 25)])
    r2, _ = run([xs, both], dep([("e1", A, Bt, None)]))
    assert [l.lane for l in r2.links] == [LOG] and r2.cross_lane_pairs_not_compared == 0


def test_CON_09_two_edge_window_is_the_sum():
    g = dep([("e1", A, "/m", 100), ("e2", "/m", Bt, 200)])
    r, _ = run([sym("x", 0, 0, topic=A), sym("y", 300, 300, topic=Bt)], g)
    l = r.links[0]
    assert (l.window_ns, l.label, l.path_kind, l.edge_ids) == (300, CAUSAL, "via_nodes", ["e1", "e2"])
    r2, _ = run([sym("x", 0, 0, topic=A), sym("y", 301, 301, topic=Bt)], g)
    assert r2.links[0].label == "outside_declared_window"


def test_CON_10_one_missing_window_means_no_window():
    g = dep([("e1", A, "/m", 100), ("e2", "/m", Bt, None)])
    r, _ = run([sym("x", 0, 0, topic=A), sym("y", 10**12, 10**12, topic=Bt)], g)
    assert r.links[0].window_ns is None and r.links[0].label == CAUSAL and r.links[0].window_check.result == "no_window"


def test_CON_11_intermediate_symptom_between_is_listed():
    g = dep([("e1", A, "/m", None), ("e2", "/m", Bt, None)])
    syms = [sym("x", 0, 0, topic=A), sym("m1", 5, 5, topic="/m"), sym("m_early", -9, -9, topic="/m"), sym("m_late", 50, 50, topic="/m"),
            sym("m_touch", 0, 3, topic="/m"), sym("y", 20, 20, topic=Bt)]
    r, _ = run(syms, g)
    l = next(l for l in r.links if (l.from_symptom, l.to_symptom) == ("x", "y"))
    assert [(n.node, n.symptom_ids_between) for n in l.intermediates] == [("/m", ["m1"])] and "path_via_unobserved_nodes" not in l.notes


def test_CON_12_unobserved_uncovered_intermediate_is_not_covered_never_unaffected():
    g = dep([("e1", A, "/m", None), ("e2", "/m", Bt, None)])
    r, _ = run([sym("x", 0, 0, topic=A), sym("y", 20, 20, topic=Bt)], g, coverage={})
    n = r.links[0].intermediates[0]
    assert (n.coverage, n.symptom_ids_between, n.covering_detectors) == ("not_covered", [], [])
    assert r.links[0].notes == ["declared_edge_unverified", "path_via_unobserved_nodes"]


def test_CON_13_unobserved_but_covered_intermediate():
    g = dep([("e1", A, "/m", None), ("e2", "/m", Bt, None)])
    r, _ = run([sym("x", 0, 0, topic=A), sym("y", 20, 20, topic=Bt)], g, coverage={"/m": ("covered", ["tierB.non_finite"])})
    n = r.links[0].intermediates[0]
    assert (n.coverage, n.symptom_ids_between, n.covering_detectors) == ("covered", [], ["tierB.non_finite"])


def test_CON_14_undeclared_pairs_are_counted_not_linked():
    g = dep([("e1", A, Bt, None)])
    syms = [sym("x", 10, 10, topic=A), sym("y", 20, 20, topic=Bt), sym("u1", 15, 15, topic="/unrelated"), sym("u2", 30, 30, topic="/other")]
    r, _ = run(syms, g)
    assert [(l.from_symptom, l.to_symptom) for l in r.links] == [("x", "y")]
    # pairs without a declared path: x-u1, x-u2, y-u1, y-u2, u1-u2
    assert r.no_declared_relation_count == 5 == O.oracle_no_declared({"x": (A, LOG, (10, 10)), "y": (Bt, LOG, (20, 20)), "u1": ("/unrelated", LOG, (15, 15)),
                                                                       "u2": ("/other", LOG, (30, 30))}, [("e1", A, Bt, None)])


def test_CON_14b_no_inference_from_names_or_order():
    g = dep([("e1", "/motor_cmd", "/motor_state", None)])
    r, _ = run([sym("a", 1, 1, topic="/motor_state_filtered"), sym("b", 9, 9, topic="/motor_state_filtered_2")], g)
    assert r.links == [] and r.no_declared_relation_count == 1


def test_CON_15_reversing_the_edge_never_creates_consistency():
    rnd = random.Random(15)
    for _ in range(150):
        syms = [sym("x", *sorted((rnd.randint(0, 30), rnd.randint(0, 30))), topic=A), sym("y", *sorted((rnd.randint(0, 30), rnd.randint(0, 30))), topic=Bt)]
        w = rnd.choice([None, 5, 20])
        fwd = run(syms, dep([("e1", A, Bt, w)]))[0].links[0]
        rev = run(syms, dep([("e1", Bt, A, w)]))[0].links[0]
        if fwd.label == CAUSAL:
            assert rev.label == "inconsistent_order"            # x precedes y strictly, so against the reversed edge it is inconsistent
        if rev.label == CAUSAL:
            assert fwd.label == "inconsistent_order"


# ------------------------------------------------------------------------------------------------------------- CON-16 oracle (1,000 instances)

def _instance(rnd):
    nodes = [f"/n{i}" for i in range(rnd.randint(2, 4))]
    edges, seen = [], set()
    for k in range(rnd.randint(1, 5)):
        a, b = rnd.sample(nodes, 2)
        if (a, b) in seen:
            continue
        seen.add((a, b))
        edges.append((f"e{k}", a, b, rnd.choice([None, None, 3, 10, 25])))
    syms = {}
    for i in range(rnd.randint(2, 7)):
        lo = rnd.randint(0, 25)
        syms[f"s{i}"] = (rnd.choice(nodes), rnd.choice([LOG, "payload_stamp:/n0:header.stamp"]), (lo, lo + rnd.choice([0, 0, 1, 4, 9])))
    return edges, syms


def test_CON_16_agrees_with_the_naive_oracle_on_1000_instances():
    rnd = random.Random(20260416)
    checked = nonempty = 0
    for _ in range(1000):
        edges, syms = _instance(rnd)
        g = dep(edges)
        symptoms = [sym(i, v[0], v[1], lane=l, topic=n) for i, (n, l, v) in syms.items()]
        r, blk = run(symptoms, g)
        got = {(l.from_symptom, l.to_symptom, l.lane, tuple(l.edge_ids), l.label, l.sep_min_ns, l.sep_max_ns) for l in r.links}
        want = O.oracle_links(syms, edges)
        assert got == want and len(r.links) == len(got) == r.links_total and blk == []
        assert r.no_declared_relation_count == O.oracle_no_declared(syms, edges)
        checked += 1
        nonempty += bool(want)
    assert checked == 1000 and nonempty > 300


# ------------------------------------------------------------------------------------------------------------- CON-17..20

def test_CON_17_binding_homes_the_symptom_on_a_node():
    g = dep([("e1", "motor", Bt, None)], bindings=[{"node": "motor", "topic": "/diagnostics", "identity": {"name": "motor_driver"}}])
    x = sym("x", 10, 10, topic="/diagnostics", identities={"diagnostic_name": "motor_driver"})
    r, _ = run([x, sym("y", 20, 20, topic=Bt)], g)
    assert [(l.from_node, l.to_node, l.label) for l in r.links] == [("motor", Bt, CAUSAL)]
    assert [(a.node, a.binding) for a in r.assignments if a.symptom_id == "x"] == [("motor", "bound")]


@pytest.mark.parametrize("val", ["Motor_Driver", "motor_driver ", " motor_driver", "motor", "motor_driver2", ""])
def test_CON_18_near_miss_identity_is_not_bound(val):
    g = dep([("e1", "motor", Bt, None)], bindings=[{"node": "motor", "topic": "/diagnostics", "identity": {"name": "motor_driver"}}])
    x = sym("x", 10, 10, topic="/diagnostics", identities={"diagnostic_name": val})
    r, _ = run([x, sym("y", 20, 20, topic=Bt)], g)
    assert r.links == [] and [(a.node, a.binding) for a in r.assignments if a.symptom_id == "x"] == [("/diagnostics", "topic")]


def test_CON_18b_ambiguous_bindings_fail_closed_to_the_topic():
    g = dep([("e1", "n1", Bt, None), ("e2", "n2", Bt, None)],
            bindings=[{"node": "n1", "topic": "/diagnostics", "identity": {"name": "motor"}}, {"node": "n2", "topic": "/diagnostics", "identity": {"hardware_id": "hw1"}}])
    x = sym("x", 10, 10, topic="/diagnostics", identities={"diagnostic_name": "motor", "hardware_id": "hw1"})
    r, _ = run([x, sym("y", 20, 20, topic=Bt)], g)
    assert r.links == [] and [(a.node, a.binding) for a in r.assignments if a.symptom_id == "x"] == [("/diagnostics", "ambiguous")]


def test_CON_19_endpoint_without_symptoms_reports_coverage_and_no_link():
    g = dep([("e1", A, Bt, None)])
    r, _ = run([sym("x", 10, 10, topic=A)], g, coverage={A: ("covered", ["tierA.ros_log"])})
    assert r.links == [] and [(e.edge_id, e.status, e.from_symptom_count, e.to_symptom_count, e.from_coverage, e.to_coverage) for e in r.edge_coverage] == \
           [("e1", "no_symptom_on_endpoint", 1, 0, "covered", "not_covered")]


def test_CON_20_link_cap_exact_suppressed_count_and_blocker():
    g = dep([("e1", A, Bt, None)])
    syms = [sym(f"x{i}", i, i, topic=A) for i in range(5)] + [sym(f"y{i}", 100 + i, 100 + i, topic=Bt) for i in range(4)]
    full, _ = run(syms, g)
    assert full.links_total == 20 == len(full.links) and full.links_status == "complete"
    r, blk = run(syms, g, limits={"links": 7})
    assert (len(r.links), r.links_total, r.links_suppressed, r.links_status) == (7, 20, 13, "capped")
    assert [l.link_id for l in r.links] == [l.link_id for l in full.links[:7]]             # the deterministic prefix
    b = [x for x in blk if x.code == "link_cap_reached"]
    assert [(x.count, x.source.kind, x.source.ref, x.source.field, x.observations) for x in b] == [(13, "m3d_limit", "links", "limits.links", [])]
    assert b[0].artifacts[0].sha256 == g.sha256
    assert B.lanes_of(b[0], [LOG]) == []            # a link-level blocker does not downgrade the earliest-symptom claim of a lane


# ------------------------------------------------------------------------------------------------------------- paths and bounds

def _layered(layers, width):
    edges, k = [], 0
    for L in range(layers - 1):
        for i in range(width):
            for j in range(width):
                edges.append((f"e{k}", f"/L{L}n{i}", f"/L{L + 1}n{j}", None))
                k += 1
    return edges


def test_paths_pair_cap_and_depth_raise_blockers_with_exact_pair_counts():
    g = dep(_layered(4, 3))                                   # 3*3 = 9 paths between a first-layer and a last-layer node
    syms = [sym("x", 0, 0, topic="/L0n0"), sym("y", 50, 50, topic="/L3n0")]
    r, blk = run(syms, g, limits={"paths_per_pair": 5})
    assert r.links_total == 5 and r.links_status == "bounded_paths"
    assert [(b.code, b.source.field, b.count) for b in blk] == [("link_cap_reached", "limits.paths_per_pair", 1)]
    r2, blk2 = run(syms, g, limits={"path_depth": 2})
    assert r2.links_total == 0 and [(b.source.field, b.count) for b in blk2] == [("limits.path_depth", 1)]
    r3, blk3 = run(syms, g)
    assert r3.links_total == 9 and blk3 == [] and r3.links_status == "complete"


def test_paths_global_work_budget_bounds_everything_and_counts_unexamined_pairs():
    g = dep(_layered(4, 3))
    syms = [sym(f"s{i}", i, i, topic=f"/L{i // 3}n{i % 3}") for i in range(12)]
    full, _ = run(syms, g)
    assert full.path_pairs_related == full.path_pairs_examined and full.path_pairs_not_examined == 0
    r, blk = run(syms, g, limits={"path_work_limit": 10})
    assert r.links_status == "bounded_paths" and r.path_pairs_not_examined > 0 and r.path_pairs_examined + r.path_pairs_not_examined == r.path_pairs_related
    assert r.links_total < full.links_total and r.links_total == len(r.links)
    b = [x for x in blk if x.code == "path_work_limit_reached"]
    assert [x.count for x in b] == [r.path_pairs_not_examined] and b[0].source.field == "limits.path_work_limit"
    assert B.lanes_of(b[0], [LOG]) == []


def test_every_examined_pair_is_complete_when_the_budget_runs_out():
    """No half-searched pair contributes links: with budget B the links are exactly those of the fully examined pairs."""
    g = dep(_layered(3, 2))
    syms = [sym(f"s{i}", i, i, topic=f"/L{i // 2}n{i % 2}") for i in range(6)]
    full, _ = run(syms, g)
    for lim in (1, 2, 3, 5, 8, 13, 100):
        r, _ = run(syms, g, limits={"path_work_limit": lim})
        assert {l.link_id for l in r.links} <= {l.link_id for l in full.links}
        pairs = {(l.from_node, l.to_node) for l in r.links}
        for (a, b) in pairs:                                  # a node pair appears completely or not at all
            assert {l.link_id for l in full.links if (l.from_node, l.to_node) == (a, b)} <= {l.link_id for l in r.links}


def test_cycles_are_accepted_and_links_follow_declared_direction_only():
    g = dep([("e1", A, Bt, None), ("e2", Bt, A, None)])
    r, _ = run([sym("x", 10, 10, topic=A), sym("y", 20, 20, topic=Bt)], g)
    assert sorted((l.from_symptom, l.to_symptom, l.label) for l in r.links) == [("x", "y", CAUSAL), ("y", "x", "inconsistent_order")]
    assert r.no_declared_relation_count == 0


def test_two_paths_give_two_links_each_with_its_own_window():
    g = dep([("e1", A, Bt, 5), ("e2", A, "/m", 100), ("e3", "/m", Bt, 100)])
    r, _ = run([sym("x", 0, 0, topic=A), sym("y", 50, 50, topic=Bt)], g)
    assert sorted((tuple(l.edge_ids), l.window_ns, l.label) for l in r.links) == [(("e1",), 5, "outside_declared_window"), (("e2", "e3"), 200, CAUSAL)]


# ------------------------------------------------------------------------------------------------------------- ADV and metamorphic

def test_ADV_01_decoy_on_an_undeclared_node_has_no_link_and_is_unplaced_in_the_graph():
    g = dep([("e1", "/a", "/b", None)])
    syms = [sym("decoy", 1, 1, topic="/unrelated"), sym("x", 50, 50, topic="/a"), sym("y", 60, 60, topic="/b")]
    r, _ = run(syms, g)
    assert [(l.from_symptom, l.to_symptom) for l in r.links] == [("x", "y")]
    assert not any("decoy" in (l.from_symptom, l.to_symptom) for l in r.links)
    assert all(e.edge_id != "decoy" for e in r.edge_coverage) and "/unrelated" not in g.endpoints


def test_ADV_02_decoy_on_a_declared_node_later_than_the_cascade_adds_no_consistent_link_into_it():
    g = dep([("e1", "/a", "/b", None)])
    syms = [sym("x", 10, 10, topic="/a"), sym("y", 20, 20, topic="/b"), sym("late", 90, 90, topic="/a")]
    r, _ = run(syms, g)
    got = {(l.from_symptom, l.to_symptom): l.label for l in r.links}
    assert got == {("x", "y"): CAUSAL, ("late", "y"): "inconsistent_order"}


def test_ADV_03_reversed_declaration_is_inconsistent_not_consistent():
    r, _ = run([sym("a", 10, 10, topic="/a"), sym("b", 20, 20, topic="/b")], dep([("e1", "/b", "/a", None)]))
    assert [(l.from_symptom, l.to_symptom, l.label) for l in r.links] == [("b", "a", "inconsistent_order")]
    comps = C.components([sym("a", 10, 10, topic="/a"), sym("b", 20, 20, topic="/b")], r.links)
    assert [c.symptom_ids for c in comps] == [["a"], ["b"]] and all(c.link_ids == [] for c in comps)      # no consistent link: singletons only


def test_ADV_04_huge_gap_interval_spanning_cascade_members_is_undetermined():
    g = dep([("e1", "/a", "/b", None), ("e2", "/b", "/c", None)])
    syms = [sym("a", 10, 10, topic="/a"), sym("gap", 0, 1000, topic="/b"), sym("c", 500, 500, topic="/c")]
    got = {(l.from_symptom, l.to_symptom): l.label for l in run(syms, g)[0].links}
    assert got[("a", "gap")] == "undetermined" and got[("gap", "c")] == "undetermined" and got[("a", "c")] == CAUSAL


def test_ADV_09_numerically_interleaved_values_in_different_domains_are_never_compared():
    g = dep([("e1", "/a", "/b", None)])
    r, _ = run([sym("x", 10, 10, topic="/a", lane="header_stamp"), sym("y", 20, 20, topic="/b", lane=LOG)], g)
    assert r.links == [] and r.cross_lane_pairs_not_compared == 1


@pytest.mark.parametrize("a,b", [(0, 1), (1, 2), (2**62, 2**62 + 1), (2**63 - 2, 2**63 - 1), (2**64 - 2, 2**64 - 1), (0, 2**64 - 1)])
def test_ADV_12_extreme_timestamps_are_exact(a, b):
    r, _ = run([sym("x", a, a, topic="/a"), sym("y", b, b, topic="/b")], dep([("e1", "/a", "/b", 1)]))
    l = r.links[0]
    assert l.sep_min_ns == l.sep_max_ns == b - a and l.label == (CAUSAL if b - a == 1 else "outside_declared_window")
    assert find_floats(l.model_dump()) == []


def test_ADV_13_equal_onsets_are_not_ordered():
    r, _ = run([sym("x", 7, 7, topic="/a"), sym("y", 7, 7, topic="/b")], dep([("e1", "/a", "/b", None)]))
    assert [l.label for l in r.links] == ["undetermined"]


def test_ADV_17_a_declaration_that_fits_a_coincidence_is_labelled_declared_and_unverified():
    g = dep([("e1", "/lidar", "/battery", None)])
    r, _ = run([sym("x", 10, 10, topic="/lidar"), sym("y", 20, 20, topic="/battery")], g)
    l = r.links[0]
    assert l.label == CAUSAL and "declared_edge_unverified" in l.notes
    assert [(d.kind, d.edge_id, d.declared_by, d.dependencies_sha256) for d in l.declared] == [("declared", "e1", "tests", g.sha256)]
    assert [o.kind for o in l.observed] == ["observed", "observed"] and l.order.kind == "derived_order"


def test_UNC_05_removing_an_edge_window_never_creates_outside_declared_window():
    rnd = random.Random(505)
    for _ in range(300):
        syms = [sym("x", *sorted((rnd.randint(0, 40), rnd.randint(0, 40))), topic="/a"), sym("y", *sorted((rnd.randint(0, 40), rnd.randint(0, 40))), topic="/b")]
        w = rnd.randint(1, 20)
        with_w = run(syms, dep([("e1", "/a", "/b", w)]))[0].links[0].label
        without = run(syms, dep([("e1", "/a", "/b", None)]))[0].links[0].label
        assert without != "outside_declared_window"
        assert (with_w == "outside_declared_window") == (without == CAUSAL and with_w != CAUSAL)
        if with_w != "outside_declared_window":
            assert with_w == without


# ------------------------------------------------------------------------------------------------------------- components, models, determinism

def test_components_are_weakly_connected_components_of_consistent_links_only():
    g = dep([("e1", "/a", "/b", None), ("e2", "/b", "/c", None), ("e3", "/d", "/e", 5)])
    syms = [sym("a", 1, 1, topic="/a"), sym("b", 5, 5, topic="/b"), sym("c", 9, 9, topic="/c"), sym("d", 100, 100, topic="/d"), sym("e", 200, 200, topic="/e"),
            sym("solo", 7, 7, topic="/z")]
    r, _ = run(syms, g)
    comps = C.components(syms, r.links)
    assert [c.symptom_ids for c in comps] == [["a", "b", "c"], ["d"], ["e"], ["solo"]] and comps[0].defence == "defended_structure" and comps[0].lanes == [LOG]
    assert len(comps[0].link_ids) == 3 and comps[3].link_ids == [] and comps[3].lanes == [LOG]          # a-b, b-c, a-c (via_nodes a->c is also declared)
    assert next(l for l in r.links if l.from_symptom == "d").label == "outside_declared_window"     # not a component edge


def test_components_merge_through_shared_symptoms_and_ignore_direction():
    g = dep([("e1", "/a", "/b", None), ("e2", "/c", "/b", None)])
    syms = [sym("a", 1, 1, topic="/a"), sym("b", 5, 5, topic="/b"), sym("c", 2, 2, topic="/c")]
    comps = C.components(syms, run(syms, g)[0].links)
    assert [c.symptom_ids for c in comps] == [["a", "b", "c"]]


def test_component_defence_downgrades():
    g = dep([("e1", "/a", "/b", None)])
    syms = [sym("a", 1, 1, topic="/a"), sym("b", 5, 5, topic="/b", truncated=True)]
    r, _ = run(syms, g)
    assert C.components(syms, r.links)[0].defence == "not_defended"
    ok = [sym("a", 1, 1, topic="/a"), sym("b", 5, 5, topic="/b")]
    r2, _ = run(ok, g)
    assert C.components(ok, r2.links)[0].defence == "defended_structure"
    assert C.components(ok, r2.links, links_incomplete=True)[0].defence == "not_defended"
    blk = B.make_blocker("detector_blind_spot", B.SourceRecord(kind="detector_result", ref="d", field="status"), B.BlockerScope(lane=LOG))
    c = C.components(ok, r2.links, blockers_by_lane=B.blockers_by_lane([blk], [LOG]))[0]
    assert c.defence == "not_defended" and c.blocker_ids == [blk.blocker_id]
    assert C.components(ok, r2.links, blockers_by_lane={"other": ["b-1"]})[0].defence == "defended_structure"


def test_every_link_model_refuses_inconsistent_content():
    r = pair(10, 10, 20, 20, window=15)
    d = r.links[0].model_dump()
    muts = [lambda x: x.update(label="undetermined"), lambda x: x.update(sep_min_ns=11), lambda x: x.update(window_ns=5), lambda x: x.update(path_kind="via_nodes"),
            lambda x: x.update(notes=[]), lambda x: x.update(declared=[]), lambda x: x.update(observed=x["observed"][::-1]),
            lambda x: x["order"].update(relation="unordered"), lambda x: x["window_check"].update(result="outside"), lambda x: x.update(extra=1),
            lambda x: x["to_onset"].update(domain="other_domain"), lambda x: x.update(lane="other_domain")]
    for m in muts:
        bad = json.loads(json.dumps(d))
        m(bad)
        with pytest.raises(ValidationError):
            C.Link(**bad)
    C.Link(**json.loads(json.dumps(d)))


def test_links_are_deterministic_under_input_permutation_and_report_clean():
    rnd = random.Random(77)
    edges, syms = _instance(rnd)
    g = dep(edges)
    base = [sym(i, v[0], v[1], lane=l, topic=n) for i, (n, l, v) in syms.items()]
    ref = run(base, g)[0]
    for _ in range(10):
        sh = base[:]
        rnd.shuffle(sh)
        out = run(sh, g)[0]
        assert out.model_dump_json() == ref.model_dump_json()
    dumped = ref.model_dump()
    assert find_floats(dumped) == [] and find_forbidden_keys(dumped) == [] and find_forbidden_values(dumped) == []


def test_link_ids_are_unique_and_bind_the_path():
    g = dep([("e1", A, Bt, 5), ("e2", A, "/m", 100), ("e3", "/m", Bt, 100)])
    r, _ = run([sym("x", 0, 0, topic=A), sym("y", 50, 50, topic=Bt)], g)
    assert len({l.link_id for l in r.links}) == 2


def test_cross_lane_counter_matches_brute_force():
    rnd = random.Random(88)
    lanes = [LOG, "payload_stamp:/a:s", "payload_stamp:/b:s"]

    def mk(sid, t, topic):
        prim = rnd.choice(lanes)
        sec = [(l, t + 1, t + 1) for l in rnd.sample([x for x in lanes if x != prim], rnd.randint(0, 1))]
        return sym(sid, t, t, topic=topic, lane=prim, secondary=sec)

    for _ in range(100):
        xs = [mk(f"x{i}", 1, A) for i in range(rnd.randint(0, 4))]
        ys = [mk(f"y{i}", 5, Bt) for i in range(rnd.randint(0, 4))]
        want = sum(1 for x in xs for y in ys if not set(x.lanes) & set(y.lanes))
        got = run(xs + ys, dep([("e1", A, Bt, None)]))[0].cross_lane_pairs_not_compared
        assert got == want


# ------------------------------------------------------------------------------------------------------------- real detectors: coverage from an M3c report

def test_detector_coverage_comes_from_detectors_that_ran(tmp_path):
    topics = {"/rosout": (l2.LOG, l2.stream([l2.log(30)])), "/t": (l2.F64, l2.stream([{"data": 1.0}] * 3))}
    rep, bag, cfg = l2.m3c(tmp_path, topics, {"tierA.ros_log": {"enabled": True}, **l2.GAP})
    g = dep([("e1", "/rosout", "/t", None), ("e2", "/t", "/never_watched", None)])
    cov = C.detector_coverage(rep, g)
    assert cov["/rosout"] == ("covered", ["tierA.ros_log"])
    assert cov["/t"][0] == "not_covered"                       # the gap detector was insufficient_evidence: it did not cover the topic
    assert cov["/never_watched"] == ("not_covered", [])


def test_node_coverage_requires_every_bound_topic_to_be_covered(tmp_path):
    topics = {"/rosout": (l2.LOG, l2.stream([l2.log(30)]))}
    rep, bag, cfg = l2.m3c(tmp_path, topics, {"tierA.ros_log": {"enabled": True}})
    mk = lambda binds: C.detector_coverage(rep, dep([("e1", "n1", "/x", None)], bindings=binds))["n1"]       # noqa: E731
    assert mk([{"node": "n1", "topic": "/rosout", "identity": {"name": "nav"}}]) == ("covered", ["tierA.ros_log"])
    assert mk([{"node": "n1", "topic": "/rosout", "identity": {"name": "nav"}}, {"node": "n1", "topic": "/diagnostics", "identity": {"name": "m"}}])[0] == "not_covered"
    assert C.detector_coverage(rep, dep([("e1", "n1", "/x", None)]))["n1"] == ("not_covered", [])


def test_real_pipeline_links_two_detectors_end_to_end(tmp_path):
    from physicore_collector.localization import symptoms as S
    topics = {"/rosout": (l2.LOG, l2.stream([l2.log(30)])), "/diagnostics": (l2.DIAG, l2.stream([l2.diag(0), l2.diag(2)], start=l2.at(5)))}
    rep, bag, cfg = l2.m3c(tmp_path, topics, {"tierA.ros_log": {"enabled": True}, "tierA.diagnostics": {"enabled": True}})
    g = dep([("e1", "/rosout", "/diagnostics", 10_000_000)])
    syms = S.from_m3c(rep).symptoms
    r, blk = C.analyse_links(syms, g, rep)
    assert blk == [] and [(l.label, l.sep_min_ns) for l in r.links] == [(CAUSAL, 6_000_000)]
    assert [e.status for e in r.edge_coverage] == ["endpoints_have_symptoms"] and r.edge_coverage[0].from_coverage == "covered"
    comps = C.components(syms, r.links)
    assert [len(c.symptom_ids) for c in comps] == [1 + 1]


# ------------------------------------------------------------------------------------------------------------- Step 6 mutation-driven additions

def _fresh(r, **over):
    d = json.loads(json.dumps(r.model_dump()))
    d.update(over)
    return d


def test_link_model_each_check_is_independently_enforced():
    base = pair(10, 10, 20, 20, window=15).links[0]
    # a fully consistent alternative label, so only the label/separation check can object
    d = json.loads(json.dumps(base.model_dump()))
    d["label"] = "outside_declared_window"          # still 'before' (valid order statement) and 'outside' (matching window check)
    d["window_check"]["result"] = "outside"
    with pytest.raises(ValidationError, match="truth table"):
        C.Link(**d)
    d = json.loads(json.dumps(base.model_dump()))
    d["window_check"]["window_ns"] = 99
    with pytest.raises(ValidationError, match="window check must repeat"):
        C.Link(**d)
    d = json.loads(json.dumps(base.model_dump()))
    d["order"]["rule_id"] = "edge_consistency.declared_window"
    with pytest.raises(ValidationError, match="order statement"):
        C.Link(**d)
    d = json.loads(json.dumps(base.model_dump()))
    d["notes"] = ["path_via_unobserved_nodes"]
    with pytest.raises(ValidationError, match="declared_edge_unverified"):
        C.Link(**d)


def test_between_is_strictly_after_x_and_strictly_before_y_and_sorted():
    g = dep([("e1", A, "/m", None), ("e2", "/m", Bt, None)])
    syms = [sym("x", 0, 10, topic=A), sym("y", 40, 40, topic=Bt),
            sym("m2", 20, 21, topic="/m"), sym("m1", 22, 23, topic="/m"), sym("m3", 30, 31, topic="/m"),
            sym("in_x", 5, 6, topic="/m"),                   # inside X's own interval: not strictly after X
            sym("m_overlap_y", 35, 45, topic="/m"),          # reaches Y's lower bound: not strictly before Y
            sym("m_touch_y", 36, 40, topic="/m")]
    r, _ = run(syms, g)
    l = next(l for l in r.links if (l.from_symptom, l.to_symptom) == ("x", "y"))
    assert l.intermediates[0].symptom_ids_between == ["m1", "m2", "m3"]


def test_link_cap_spans_several_blocks_exactly():
    g = dep([("e1", A, Bt, 5), ("e2", A, "/m", 100), ("e3", "/m", Bt, 100)])
    syms = [sym("x0", 0, 0, topic=A), sym("x1", 1, 1, topic=A), sym("y0", 50, 50, topic=Bt), sym("y1", 60, 60, topic=Bt)]
    full, _ = run(syms, g)
    assert full.links_total == 8
    for cap in range(0, 10):
        r, blk = run(syms, g, limits={"links": cap})
        assert len(r.links) == min(cap, 8) and r.links_total == 8 and r.links_suppressed == max(0, 8 - cap)
        assert [l.link_id for l in r.links] == [l.link_id for l in full.links[:cap]]
        assert bool([b for b in blk if b.source.ref == "links"]) == (cap < 8)


def test_global_work_budget_is_shared_across_pairs():
    chain = [(f"e{i}", f"/c{i}", f"/c{i + 1}", None) for i in range(5)]
    g = dep(chain)
    syms = [sym(f"s{i}", i, i, topic=f"/c{i}") for i in range(6)]
    full, _ = run(syms, g)
    assert full.path_pairs_related == 15 == full.path_pairs_examined and full.links_status == "complete"
    r, blk = run(syms, g, limits={"path_work_limit": 8})        # each pair needs at most 5 steps; all pairs need more than 8 together
    assert r.links_status == "bounded_paths" and 0 < r.path_pairs_examined < 15 and r.path_pairs_not_examined == 15 - r.path_pairs_examined
    assert [b.count for b in blk if b.code == "path_work_limit_reached"] == [r.path_pairs_not_examined]


def test_components_come_out_in_canonical_order():
    names = ["a", "c", "e", "g", "i", "k"]
    g = dep([("e1", "/p", "/q", None)])
    syms = []
    for i, n in enumerate(names):
        syms += [sym(n, 10 * i, 10 * i, topic="/p"), sym(n + "z", 10 * i + 5, 10 * i + 5, topic="/q")]
    r, _ = run(syms, g)
    # every x on /p links to every later y on /q, so the whole set merges; use windows to separate the pairs instead
    g2 = dep([("e1", "/p", "/q", 6)])
    r2, _ = run(syms, g2)
    comps = C.components(syms, r2.links)
    assert [c.symptom_ids[0] for c in comps] == sorted(c.symptom_ids[0] for c in comps)


def test_component_model_rules():
    ok = dict(component_id="c-1", symptom_ids=["a", "b"], lanes=[LOG], link_ids=["l-1"], defence="defended_structure")
    C.Component(**ok)
    for bad in ({"symptom_ids": []}, {"symptom_ids": ["b", "a"]}, {"symptom_ids": ["a", "a"]}, {"blocker_ids": ["b-1"]}, {"defence": "tied"}):
        with pytest.raises(ValidationError):
            C.Component(**{**ok, **bad})
