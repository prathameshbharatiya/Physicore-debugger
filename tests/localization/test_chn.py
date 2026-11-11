"""CHN-01..30, ADV-14..16/18/26..30, SCL-03/04/09/10 (TEST_PLAN). The path counts and orders are checked against the recursive brute-force oracle, never
against chains.py itself."""
import ast
import json
import random
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from physicore_collector.localization import blockers as B
from physicore_collector.localization import chains as H
from physicore_collector.localization import consistency as C
from physicore_collector.localization.dependencies import WorkBudget
from physicore_collector.localization.report import find_floats, find_forbidden_keys, find_forbidden_values
from physicore_collector.localization.vocabulary import DISCRIMINATING_EVIDENCE, HYPOTHESIS_TEMPLATES, find_forbidden
from tests.localization import oracle as O
from tests.localization.l1 import LOG, sym
from tests.localization.test_con import dep, run

CAUSAL = "consistent_with_propagation"


def pipe(syms, g, *, limits=None, coverage=None, blockers=(), caveats=(), budget_limit=None):
    """Real Step 6 + Step 7 on L1 symptoms. -> (analysis, all blockers, consistency result)."""
    lim = {**(limits or {})}
    budget = WorkBudget(budget_limit if budget_limit is not None else 1_000_000)
    r, bl = C.analyse_links(syms, g, None, coverage=coverage or {}, limits=lim, budget=budget)
    ns = C.apply_assignments(syms, r.assignments)
    ana, nb = H.analyse_chains(ns, r.links, r, g, blockers=[*blockers, *bl], caveats=caveats, coverage=coverage or {}, budget=budget, limits=lim)
    return ana, [*bl, *nb], r


def paths(ana):
    return [tuple(c.symptom_ids) for c in ana.chains]


def at(name, t, topic):
    return sym(name, t, t, topic=topic)


# ------------------------------------------------------------------------------------------------------------- CHN-01..08

def test_CHN_01_linear():
    g = dep([("e1", "/a", "/b", None), ("e2", "/b", "/c", None)])
    ana, bl, r = pipe([at("a", 1, "/a"), at("b", 5, "/b"), at("c", 9, "/c")], g)
    assert paths(ana) == [("a", "b", "c")] and [c.symptom_ids for c in ana.components] == [["a", "b", "c"]] and ana.chains[0].head == "a"
    assert (ana.chains_total, ana.chains_total_status, ana.chains_truncated_by) == (1, "exact", None) and bl == []


def test_CHN_02_fan_out_shares_the_head():
    g = dep([("e1", "/a", "/b", None), ("e2", "/a", "/c", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b"), at("c", 9, "/c")], g)
    assert paths(ana) == [("a", "b"), ("a", "c")] and len(ana.components) == 1


def test_CHN_03_diamond_two_chains_d_once_in_components():
    g = dep([("e1", "/a", "/b", None), ("e2", "/a", "/c", None), ("e3", "/b", "/d", None), ("e4", "/c", "/d", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b"), at("c", 6, "/c"), at("d", 20, "/d")], g)
    assert paths(ana) == [("a", "b", "d"), ("a", "c", "d")] and [c.symptom_ids for c in ana.components] == [["a", "b", "c", "d"]]


def test_CHN_04_feedback_cycle_in_dependencies_terminates():
    g = dep([("e1", "/a", "/b", None), ("e2", "/b", "/a", None)])
    ana, bl, r = pipe([at("a1", 1, "/a"), at("b", 5, "/b"), at("a2", 9, "/a")], g)
    assert paths(ana) == [("a1", "b", "a2")] and ana.chains_truncated_by is None and ana.chains_total == 1


def test_CHN_05_two_independent_incidents():
    g = dep([("e1", "/a", "/b", 5), ("e2", "/c", "/d", 5)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 3, "/b"), at("c", 100, "/c"), at("d", 102, "/d")], g)
    assert [c.symptom_ids for c in ana.components] == [["a", "b"], ["c", "d"]] and paths(ana) == [("a", "b"), ("c", "d")]


def test_CHN_06_singleton_is_its_own_component_and_unlocalized_without_edges():
    g = dep([("e1", "/a", "/b", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b"), at("lonely", 3, "/lonely")], g)
    assert ["lonely"] in [c.symptom_ids for c in ana.components] and all("lonely" not in p for p in paths(ana))
    assert [(u.symptom_id, u.node) for u in ana.localization.unlocalized] == [("lonely", "/lonely")]
    # a singleton on a node WITH edges is a component but not unlocalized
    ana2, *_ = pipe([at("a", 1, "/a")], g)
    assert [c.symptom_ids for c in ana2.components] == [["a"]] and ana2.localization.unlocalized == [] and ana2.chains == []


def test_CHN_07_head_not_origin_anywhere():
    g = dep([("e1", "/a", "/b", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b")], g)
    d = ana.model_dump()
    assert "head" in d["chains"][0] and "origin" not in json.dumps(d).replace("common_or_independent_origin_not_excluded", "").lower()
    assert find_forbidden_values(d) == [] and find_forbidden_keys(d) == []


def _arc_dag(lane, arcs):
    """A LaneDag over arbitrary arcs; vertex keys follow a topological rank (so that a successor always has the larger key, as real onsets do)."""
    out, indeg = {}, {}
    for a, b in arcs:
        out.setdefault(a, []).append(b)
        indeg[b] = indeg.get(b, 0) + 1
    vs = sorted({v for ab in arcs for v in ab})
    ready = sorted(v for v in vs if indeg.get(v, 0) == 0)
    rank = {}
    while ready:
        v = ready.pop(0)
        rank[v] = len(rank)
        for w in sorted(out.get(v, ())):
            indeg[w] -= 1
            if indeg[w] == 0:
                ready.append(w)
        ready.sort()
    assert len(rank) == len(vs), "test DAG has a cycle"
    key = {v: (rank[v], rank[v], v) for v in vs}
    d = H.LaneDag(lane, key, {})
    for a, b in sorted(arcs):
        d.arc_links.setdefault((a, b), [f"{a}>{b}"])
        d.succ.setdefault(a, []).append(b)
        d.pred_count[b] = d.pred_count.get(b, 0) + 1
    for a in d.succ:
        d.succ[a].sort(key=lambda v: key[v])
    return d


def _chain_dag(n, lane=LOG):
    names = [f"v{i:07d}" for i in range(n)]
    return _arc_dag(lane, {(names[i], names[i + 1]) for i in range(n - 1)}), names


def test_CHN_08_ten_thousand_deep_chain_is_iterative_and_correct():
    d, names = _chain_dag(10_000)
    en = H.enumerate_chains([d], WorkBudget(10**9), 1000, 10**9)
    assert en.total == 1 and en.truncated_by is None and en.kept == [(LOG, tuple(names))]
    assert H.count_maximal_paths(d) == 1


def test_CHN_08b_a_deep_chain_through_the_full_pipeline():
    n = 120
    g = dep([(f"e{i}", f"/n{i:03d}", f"/n{i + 1:03d}", None) for i in range(n - 1)])
    syms = [at(f"s{i:03d}", 10 * i, f"/n{i:03d}") for i in range(n)]
    ana, bl, r = pipe(syms, g, limits={"links": 10**7, "path_depth": 500}, budget_limit=10**8)
    assert paths(ana) == [tuple(f"s{i:03d}" for i in range(n))] and bl == []
    assert ana.chains[0].defence == "defended_structure" and [c.symptom_ids[0] for c in ana.components] == ["s000"]


# ------------------------------------------------------------------------------------------------------------- CHN-09, 16, 17, 23..30

def _fan(n):
    """n independent two-symptom chains on separate node pairs (n components) or one head with n successors."""
    g = dep([(f"e{i}", "/h", f"/t{i:02d}", None) for i in range(n)])
    return g, [at("h", 0, "/h")] + [at(f"t{i:02d}", 10 + i, f"/t{i:02d}") for i in range(n)]


def test_CHN_09_report_limit_with_exact_total_and_truncation_blocker():
    g, syms = _fan(10)
    ana, bl, _ = pipe(syms, g, limits={"chains_reported": 3})
    assert paths(ana) == [("h", "t00"), ("h", "t01"), ("h", "t02")]
    assert (ana.chains_total, ana.chains_total_status, ana.chains_truncated_by, ana.chains_lower_bound) == (10, "exact", "report_limit", 3)
    b = [x for x in bl if x.code == "chains_truncated"]
    assert [(x.count, x.source.kind, x.source.field) for x in b] == [(7, "m3d_limit", "limits.chains_reported")]


def test_CHN_09b_exactly_full_report_is_not_truncated():
    g, syms = _fan(4)
    ana, bl, _ = pipe(syms, g, limits={"chains_reported": 4})
    assert ana.chains_truncated_by is None and not [b for b in bl if b.code == "chains_truncated"] and ana.chains_total == 4
    ana2, bl2, _ = pipe(syms, g, limits={"chains_reported": 4, "count_work_limit": 1})       # total unknown: the extra chain is still found, not guessed
    assert ana2.chains_truncated_by is None and ana2.chains_total is None
    ana3, bl3, _ = pipe(syms, g, limits={"chains_reported": 3, "count_work_limit": 1})
    assert ana3.chains_truncated_by == "report_limit" and [b.count for b in bl3 if b.code == "chains_truncated"] == [None]


def test_CHN_10_only_three_templates_exist():
    assert HYPOTHESIS_TEMPLATES == ("propagation_along_declared_edge", "common_or_independent_origin_not_excluded", "observation_or_timing_artifact_not_excluded")
    g = dep([("e1", "/a", "/b", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b")], g)
    h = ana.chains[0].hypotheses[0].model_dump()
    for bad in ("propagation", "root_cause_found", "propagation_along_declared_edge"):       # template 1 is link-level, never a chain hypothesis
        with pytest.raises(ValidationError):
            H.Hypothesis(**{**h, "template": bad})
    assert {x.template for x in ana.link_hypotheses} == {"propagation_along_declared_edge"}


FIXTURES = {
    "linear": ([("e1", "/a", "/b", None), ("e2", "/b", "/c", None)], [("a", 1, "/a"), ("b", 5, "/b"), ("c", 9, "/c")]),
    "fan": ([("e1", "/a", "/b", None), ("e2", "/a", "/c", None)], [("a", 1, "/a"), ("b", 5, "/b"), ("c", 9, "/c")]),
    "with_ancestor": ([("e0", "/z", "/a", 3), ("e1", "/a", "/b", None)], [("a", 10, "/a"), ("b", 15, "/b"), ("z", 1, "/z")]),
    "ancestor_after": ([("e0", "/z", "/a", 3), ("e1", "/a", "/b", None)], [("a", 10, "/a"), ("b", 15, "/b"), ("z", 50, "/z")]),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_CHN_11_templates_2_and_3_are_not_excluded_on_every_chain(name):
    edges, ss = FIXTURES[name]
    ana, *_ = pipe([at(*s) for s in ss], dep(edges))
    assert ana.chains
    for c in ana.chains:
        assert [(h.template, h.status) for h in c.hypotheses] == [("common_or_independent_origin_not_excluded", "not_excluded"),
                                                                  ("observation_or_timing_artifact_not_excluded", "not_excluded")]


def test_CHN_12_hypotheses_are_ordered_by_template_name_only():
    edges, ss = FIXTURES["with_ancestor"]
    ana, *_ = pipe([at(*s) for s in ss], dep(edges))
    c = ana.chains[0]
    d = c.model_dump()
    d["hypotheses"] = d["hypotheses"][::-1]
    with pytest.raises(ValidationError):
        H.Chain(**d)
    rnd = random.Random(12)
    for _ in range(20):
        sh = [at(*s) for s in rnd.sample(ss, len(ss))]
        got = pipe(sh, dep(edges))[0].chains[0]
        assert [h.template for h in got.hypotheses] == sorted(h.template for h in got.hypotheses)


def test_CHN_13_template_1_exclusion_shows_both_intervals_edge_and_window():
    g = dep([("e1", "/a", "/b", 5)])
    ana, *_ = pipe([sym("a", 20, 25, topic="/a"), sym("b", 1, 2, topic="/b")], g)
    (h,) = ana.link_hypotheses
    assert (h.status, h.label, h.edge_ids, h.window_ns) == ("excluded_by_declared_edge_timing", "inconsistent_order", ["e1"], 5)
    assert (h.from_onset.lower_ns, h.from_onset.upper_ns, h.to_onset.lower_ns, h.to_onset.upper_ns) == (20, 25, 1, 2)
    ana2, *_ = pipe([at("a", 1, "/a"), at("b", 99, "/b")], g)                       # outside the window: the same exclusion, same facts
    assert [(x.status, x.label) for x in ana2.link_hypotheses] == [("excluded_by_declared_edge_timing", "outside_declared_window")]
    ana3, *_ = pipe([sym("a", 1, 10, topic="/a"), sym("b", 5, 20, topic="/b")], g)    # overlap: neither supported nor excluded
    assert [x.status for x in ana3.link_hypotheses] == ["not_excluded"]
    ana4, *_ = pipe([at("a", 1, "/a"), at("b", 3, "/b")], g)
    assert [x.status for x in ana4.link_hypotheses] == ["consistent_with"]


def test_CHN_14_template_2_facts_are_exact():
    edges, ss = FIXTURES["with_ancestor"]
    cov = {"/z": ("covered", ["tierA.ros_log"])}
    ana, *_ = pipe([at(*s) for s in ss], dep(edges), coverage=cov)
    c = next(c for c in ana.chains if c.head == "a")
    f = c.hypotheses[0].common_ancestors
    assert (f.ancestors_total, [(a.node, a.symptom_ids_before_head, a.symptom_count_before_head, a.coverage, a.covering_detectors) for a in f.ancestors]) == \
           (1, [("/z", ["z"], 1, "covered", ["tierA.ros_log"])])
    after = pipe([at(*s) for s in FIXTURES["ancestor_after"][1]], dep(FIXTURES["ancestor_after"][0]))[0]
    a2 = next(c for c in after.chains if c.head == "a").hypotheses[0].common_ancestors.ancestors[0]
    assert (a2.symptom_ids_before_head, a2.symptom_count_before_head, a2.coverage, a2.covering_detectors) == ([], 0, "not_covered", [])


def test_CHN_15_undeclared_ancestor_is_not_listed_and_the_missing_edge_is_asked_for():
    g = dep([("e1", "/a", "/b", None)])
    ana, *_ = pipe([at("a", 10, "/a"), at("b", 15, "/b"), at("z_undeclared", 1, "/z")], g)
    h = ana.chains[0].hypotheses[0]
    assert h.common_ancestors.ancestors_total == 0 and h.common_ancestors.ancestors == []
    assert "a declared edge for the ancestor" in h.discriminating_evidence_needed


def test_CHN_16_chain_with_capped_links_is_not_defended_with_blocker_ids():
    g = dep([("e1", "/a", "/b", None)])
    syms = [at(f"a{i}", i, "/a") for i in range(4)] + [at(f"b{i}", 100 + i, "/b") for i in range(4)]
    ana, bl, r = pipe(syms, g, limits={"links": 5})
    assert r.links_status == "capped"
    lb = [b.blocker_id for b in bl if b.code == "link_cap_reached"]
    assert lb and ana.chains and all(c.defence == "not_defended" and set(lb) <= set(c.blocker_ids) for c in ana.chains)
    assert all(c.defence == "not_defended" for c in ana.components)


def test_CHN_17_lane_blocker_makes_chains_and_components_not_defended():
    g = dep([("e1", "/a", "/b", None)])
    blk = B.make_blocker("detector_blind_spot", B.SourceRecord(kind="detector_result", ref="d", field="status"), B.BlockerScope(lane=LOG))
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b")], g, blockers=[blk])
    assert ana.chains[0].defence == "not_defended" and blk.blocker_id in ana.chains[0].blocker_ids and ana.components[0].defence == "not_defended"
    other = B.make_blocker("detector_blind_spot", B.SourceRecord(kind="detector_result", ref="d", field="status"), B.BlockerScope(lane="other"))
    ana2, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b")], g, blockers=[other])
    assert ana2.chains[0].defence == "defended_structure" and ana2.chains[0].blocker_ids == []


def test_CHN_18_locus_frontier_unlocalized_for_a_three_layer_component():
    g = dep([("e1", "/a", "/b", None), ("e2", "/b", "/c", None), ("e3", "/a", "/d", None)])
    syms = [at("a", 1, "/a"), at("a2", 2, "/a2_undeclared"), at("b", 10, "/b"), at("c", 20, "/c"), at("d", 11, "/d")]
    ana, *_ = pipe(syms, g)
    comp = next(c for c in ana.components if "a" in c.symptom_ids)
    loc = next(l for l in ana.localization.components if l.component_id == comp.component_id)
    assert [(x.lane, x.locus_nodes, x.frontier_nodes) for x in loc.lanes] == [(LOG, ["/a"], ["/c"])]
    assert [(u.symptom_id, u.node) for u in ana.localization.unlocalized] == [("a2", "/a2_undeclared")]
    assert "fault" in ana.localization.statement and find_forbidden(ana.localization.statement) == []


def test_CHN_18b_without_a_dependency_file_symptoms_are_localized_to_topics_and_layers():
    syms = [at("a", 1, "/a"), at("b", 5, "/b"), at("c", 5, "/c"), at("d", 9, "/a")]
    tl = H.topic_layers(syms)
    assert [(t.lane, t.topic, t.layer, t.symptom_ids) for t in tl] == [(LOG, "/a", 0, ["a"]), (LOG, "/a", 2, ["d"]), (LOG, "/b", 1, ["b"]), (LOG, "/c", 1, ["c"])]


def test_CHN_19_discriminating_evidence_strings_come_from_the_fixed_vocabulary():
    blk = B.make_blocker("recording_incomplete", B.SourceRecord(kind="inventory_record", ref="r", field="f"))
    for name, (edges, ss) in FIXTURES.items():
        for ss2 in (ss, [(n, t, tp) for n, t, tp in ss]):
            ana, *_ = pipe([at(*s) for s in ss2], dep(edges), blockers=[blk])
            for c in ana.chains:
                for h in c.hypotheses:
                    assert set(h.discriminating_evidence_needed) <= set(DISCRIMINATING_EVIDENCE)
                    assert h.discriminating_evidence_needed == [e for e in DISCRIMINATING_EVIDENCE if e in h.discriminating_evidence_needed]
    with pytest.raises(ValidationError):
        H.Hypothesis(**{**ana.chains[0].hypotheses[0].model_dump(), "discriminating_evidence_needed": ["ask the operator"]})


def test_CHN_19b_artifact_template_asks_for_what_the_situation_lacks():
    g = dep([("e1", "/a", "/b", None)])
    blk = B.make_blocker("recording_incomplete", B.SourceRecord(kind="inventory_record", ref="r", field="f"))
    ana, *_ = pipe([sym("a", 1, 3, topic="/a"), at("b", 9, "/b")], g, blockers=[blk], caveats=[B.Caveat(code="receipt_order_not_occurrence_order", detail=B.CAVEAT_DETAIL["receipt_order_not_occurrence_order"])])
    h3 = ana.chains[0].hypotheses[1]
    assert h3.discriminating_evidence_needed == ["an intervention or a repeat recording", "recorder-side drop counters (not available in this format)",
                                                 "a recording with the missing segments", "a narrower onset interval from a higher-rate topic"]
    assert h3.artifact_facts.caveat_codes == ["receipt_order_not_occurrence_order"] and h3.artifact_facts.recording_blocker_ids == [blk.blocker_id]
    clean, *_ = pipe([at("a", 1, "/a"), at("b", 9, "/b")], g)
    assert clean.chains[0].hypotheses[1].discriminating_evidence_needed == ["an intervention or a repeat recording", "recorder-side drop counters (not available in this format)"]


def _random_arcs(rnd, n, p):
    names = [f"s{i:02d}" for i in range(n)]
    return {(names[i], names[j]) for i in range(n) for j in range(i + 1, n) if rnd.random() < p}


def test_CHN_20_link_dag_is_acyclic_over_1000_random_instances():
    rnd = random.Random(20)
    for _ in range(1000):
        n = rnd.randint(2, 6)
        edges = [(f"e{k}", f"/n{a}", f"/n{b}", rnd.choice([None, 5])) for k, (a, b) in enumerate({tuple(rnd.sample(range(4), 2)) for _ in range(rnd.randint(1, 6))})]
        syms = [sym(f"s{i}", (lo := rnd.randint(0, 30)), lo + rnd.choice([0, 2]), topic=f"/n{rnd.randint(0, 3)}") for i in range(n)]
        r, _ = run(syms, dep(edges))
        dags = H.build_dags(r.links)             # raises if any consistent link is not strictly forward
        for d in dags:
            en = H.enumerate_chains([d], WorkBudget(10**7), 10**6, 10**9)
            assert all(len(set(p)) == len(p) for _, p in en.kept)


def test_CHN_21_chains_are_maximal_and_not_subpaths_of_each_other():
    rnd = random.Random(21)
    for _ in range(300):
        arcs = _random_arcs(rnd, rnd.randint(2, 7), 0.4)
        if not arcs:
            continue
        d = _arc_dag(LOG, arcs)
        got = [p for _, p in H.enumerate_chains([d], WorkBudget(10**7), 10**6, 10**9).kept]
        for p in got:
            assert d.pred_count.get(p[0], 0) == 0 and p[-1] not in d.succ
            for q in got:
                if p != q:
                    assert not any(q[i:i + len(p)] == p for i in range(len(q) - len(p) + 1))


def test_CHN_22_every_arc_lies_on_a_chain():
    rnd = random.Random(22)
    for _ in range(300):
        arcs = _random_arcs(rnd, rnd.randint(2, 7), 0.4)
        if not arcs:
            continue
        d = _arc_dag(LOG, arcs)
        got = [p for _, p in H.enumerate_chains([d], WorkBudget(10**7), 10**6, 10**9).kept]
        on = {(p[i], p[i + 1]) for p in got for i in range(len(p) - 1)}
        assert on == arcs


def test_CHN_23_enumeration_is_deterministic_under_shuffles():
    rnd = random.Random(23)
    g = dep([("e1", "/a", "/b", None), ("e2", "/a", "/c", None), ("e3", "/b", "/d", None), ("e4", "/c", "/d", None), ("e5", "/a", "/d", None)])
    syms = [at("a", 1, "/a"), at("b", 5, "/b"), at("c", 6, "/c"), at("d", 20, "/d"), at("b2", 7, "/b")]
    ref = pipe(syms, g)[0].model_dump_json()
    for _ in range(15):
        sh = rnd.sample(syms, len(syms))
        shg = dep(rnd.sample([("e1", "/a", "/b", None), ("e2", "/a", "/c", None), ("e3", "/b", "/d", None), ("e4", "/c", "/d", None), ("e5", "/a", "/d", None)], 5))
        assert pipe(sh, shg)[0].model_dump_json() == ref


def test_CHN_23b_identical_across_hash_seeds(tmp_path):
    import subprocess
    script = tmp_path / "s.py"
    script.write_text("from tests.localization.test_chn import *\n"
                      "g = dep([('e1','/a','/b',None),('e2','/a','/c',None),('e3','/b','/d',None),('e4','/c','/d',None)])\n"
                      "syms=[at('a',1,'/a'),at('b',5,'/b'),at('c',6,'/c'),at('d',20,'/d')]\n"
                      "print(pipe(syms,g)[0].model_dump_json())\n")
    outs = set()
    for seed in ("0", "1", "424242"):
        p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env={**__import__("os").environ, "PYTHONHASHSEED": seed,
                           "PYTHONPATH": f"{Path(__file__).resolve().parents[2] / 'src'}:{Path(__file__).resolve().parents[2]}"}, cwd=str(Path(__file__).resolve().parents[2]))
        assert p.returncode == 0, p.stderr[-400:]
        outs.add(p.stdout)
    assert len(outs) == 1


def _diamonds(k):
    arcs = set()
    for i in range(k):
        arcs |= {(f"d{i:03d}", f"a{i:03d}"), (f"d{i:03d}", f"b{i:03d}"), (f"a{i:03d}", f"d{i + 1:03d}"), (f"b{i:03d}", f"d{i + 1:03d}")}
    return arcs


def test_CHN_24_lazy_enumeration_never_materialises_the_set():
    d = _arc_dag(LOG, _diamonds(20))                                   # 2**20 = 1,048,576 maximal chains
    pulled = []
    real = H.iter_chains

    def probe(dags, budget, state):
        for item in real(dags, budget, state):
            pulled.append(1)
            yield item
    H.iter_chains = probe
    try:
        b = WorkBudget(10**9)
        en = H.enumerate_chains([d], b, 1000, 10**9)
    finally:
        H.iter_chains = real
    assert en.total == 2**20 and len(en.kept) == 1000 and en.truncated_by == "report_limit"
    assert len(pulled) == 1001                                          # the 1,000 kept plus the one that proves truncation
    assert b.used <= 1001 * 41 and b.used < 50_000                      # ~ chain length (41) per chain; nowhere near 2**20 chains


def test_CHN_25_dp_total_equals_brute_force_on_500_random_dags():
    rnd = random.Random(25)
    n = 0
    for _ in range(500):
        arcs = _random_arcs(rnd, rnd.randint(2, 8), rnd.choice([0.2, 0.4, 0.7]))
        if not arcs:
            continue
        d = _arc_dag(LOG, arcs)
        want = O.oracle_maximal_paths(arcs, d.key)
        assert H.count_maximal_paths(d) == len(want)
        n += 1
    assert n > 400


def test_CHN_26_count_limit_below_size_gives_bounded_unknown_never_a_number():
    d = _arc_dag(LOG, _diamonds(5))
    size = len(d.key) + d.arc_count
    en = H.enumerate_chains([d], WorkBudget(10**9), 10, size - 1)
    assert en.total is None and len(en.kept) == 10
    full = H.enumerate_chains([d], WorkBudget(10**9), 10, size)
    assert full.total == 32
    g, syms = _fan(6)
    ana, bl, _ = pipe(syms, g, limits={"count_work_limit": 3, "chains_reported": 2})
    assert (ana.chains_total, ana.chains_total_status, ana.chains_lower_bound, ana.chains_reported) == (None, "bounded_unknown", 2, 2)
    with pytest.raises(ValidationError):
        H.ChainAnalysis(**{**ana.model_dump(), "chains_total": 6})


def test_CHN_27_report_limit_exact_count_downgrades_the_affected_component_only():
    g = dep([("e1", "/a", "/b", 5), ("e2", "/c", "/d", 5), ("e3", "/c", "/e", 5)])
    syms = [at("a", 1, "/a"), at("b", 3, "/b"), at("c", 100, "/c"), at("d", 102, "/d"), at("e", 103, "/e")]
    ana, bl, _ = pipe(syms, g, limits={"chains_reported": 2})
    assert paths(ana) == [("a", "b"), ("c", "d")] and ana.chains_total == 3 and ana.chains_truncated_by == "report_limit"
    tb = next(b for b in bl if b.code == "chains_truncated")
    assert tb.count == 1
    st = {tuple(c.symptom_ids): (c.defence, c.blocker_ids) for c in ana.components}
    assert st[("a", "b")] == ("defended_structure", []) and st[("c", "d", "e")] == ("not_defended", [tb.blocker_id])      # CHN-29 too
    chain = {tuple(c.symptom_ids): c for c in ana.chains}
    assert chain[("a", "b")].defence == "defended_structure" and chain[("c", "d")].defence == "not_defended" and tb.blocker_id in chain[("c", "d")].blocker_ids


def test_CHN_28_work_budget_exhausted_mid_enumeration():
    g, syms = _fan(8)
    full = pipe(syms, g)[0]
    cost = 0
    r, bl0 = C.analyse_links(syms, g, None, coverage={})
    b = WorkBudget(10**6)
    H.analyse_chains(C.apply_assignments(syms, r.assignments), r.links, r, g, blockers=bl0, budget=b)
    cost = b.used
    got = None
    for lim in range(1, cost):
        budget = WorkBudget(10**6)
        r, bl0 = C.analyse_links(syms, g, None, coverage={}, budget=budget)
        spent = budget.used
        budget.limit = spent + lim
        ana, nb = H.analyse_chains(C.apply_assignments(syms, r.assignments), r.links, r, g, blockers=bl0, budget=budget)
        if ana.chains_truncated_by == "work_limit":
            got = (ana, nb)
            break
    assert got is not None
    ana, nb = got
    assert [b.code for b in nb] == ["path_work_limit_reached"] and nb[0].source.field == "limits.path_work_limit"
    assert ana.chains_total == 8 and nb[0].count == 8 - len(ana.chains) and len(ana.chains) < 8
    assert paths(ana) == paths(full)[:len(ana.chains)]
    assert all(c.defence == "not_defended" for c in ana.components) and all(nb[0].blocker_id in c.blocker_ids for c in ana.components)


def test_CHN_30_reported_chains_are_a_canonical_prefix_of_the_full_enumeration():
    rnd = random.Random(30)
    n = 0
    for _ in range(300):
        arcs = _random_arcs(rnd, rnd.randint(3, 8), 0.45)
        if not arcs:
            continue
        d = _arc_dag(LOG, arcs)
        want = O.oracle_maximal_paths(arcs, d.key)
        k = rnd.randint(1, len(want))
        en = H.enumerate_chains([d], WorkBudget(10**9), k, 10**9)
        assert [p for _, p in en.kept] == want[:k] and (en.truncated_by == "report_limit") == (k < len(want))
        n += 1
    assert n > 200


# ------------------------------------------------------------------------------------------------------------- ADV

def test_ADV_14_receipt_order_inversion_is_inconsistent_with_a_caveat_and_no_negative_claim():
    g = dep([("e1", "/a", "/b", None)])
    cav = B.Caveat(code="receipt_order_not_occurrence_order", detail=B.CAVEAT_DETAIL["receipt_order_not_occurrence_order"])
    ana, bl, r = pipe([at("a", 50, "/a"), at("b", 10, "/b")], g, caveats=[cav])
    assert [l.label for l in r.links] == ["inconsistent_order"] and ana.chains == []
    assert [(h.status) for h in ana.link_hypotheses] == ["excluded_by_declared_edge_timing"]
    # the exclusion is worded as concerning the declared edge only, never as "A did not cause B"
    assert "This concerns this declared edge only" in H.HYPOTHESIS_SENTENCES[("propagation_along_declared_edge", "excluded_by_declared_edge_timing")]
    assert find_forbidden_values(ana.model_dump()) == []


def test_ADV_15_fully_connected_declared_graph_stays_bounded_and_exact():
    topics = [f"/t{i:02d}" for i in range(30)]
    edges = [(f"e{i}_{j}", topics[i], topics[j], None) for i in range(30) for j in range(30) if i != j]
    g = dep(edges)
    syms = [at(f"s{i:02d}", 10 * i, topics[i]) for i in range(30)]
    ana, bl, r = pipe(syms, g, limits={"links": 500, "path_work_limit": 50_000, "paths_per_pair": 20})
    assert len(r.links) <= 500 and r.links_total == r.links_suppressed + len(r.links) and r.links_status in ("capped", "bounded_paths")
    assert {b.code for b in bl} <= {"link_cap_reached", "path_work_limit_reached", "chains_truncated"} and len(ana.chains) <= 1000


def test_ADV_16_self_referential_feedback_has_no_cycle_chain():
    g = dep([("e1", "/a", "/b", None), ("e2", "/b", "/a", None)])
    ana, bl, r = pipe([at("a1", 1, "/a"), at("b", 2, "/b"), at("a3", 3, "/a")], g)
    got = {(l.from_symptom, l.to_symptom) for l in r.links if l.label == CAUSAL}
    assert ("a1", "b") in got and ("b", "a3") in got and ("a1", "a3") not in got
    for p in paths(ana):
        assert len(set(p)) == len(p)


def test_ADV_18_adding_the_edge_changes_only_links_chains_hypotheses_components():
    from physicore_collector.localization.precedence import build_lanes
    syms = [at("a", 1, "/a"), at("b", 5, "/b"), at("c", 9, "/c")]
    with_e = pipe(syms, dep([("e1", "/a", "/b", None), ("e2", "/b", "/c", None)]))[0]
    without = pipe(syms, dep([("e9", "/x", "/y", None)]))[0]
    assert without.chains == [] and with_e.chains and len(with_e.components) < len(without.components)
    l1, _ = build_lanes(syms, {})
    l2, _ = build_lanes(syms, {})
    assert [x.model_dump() for x in l1] == [x.model_dump() for x in l2]           # layers / earliest do not read the dependency file at all


def _layered_dag(layers, width):
    arcs = {(f"L{l:02d}n{i}", f"L{l + 1:02d}n{j}") for l in range(layers - 1) for i in range(width) for j in range(width)}
    return _arc_dag(LOG, arcs)


def test_ADV_26_dense_layered_dag_12x4_total_by_dp_first_1000_only():
    d = _layered_dag(12, 4)
    b = WorkBudget(10**9)
    en = H.enumerate_chains([d], b, 1000, 10**9)
    assert en.total == 4 ** 12 and len(en.kept) == 1000 and en.truncated_by == "report_limit" and b.used < 1001 * 12 + 100
    assert [p for _, p in en.kept] == [p for _, p in H.enumerate_chains([d], WorkBudget(10**9), 1000, 10**9).kept]
    small = _layered_dag(4, 3)
    assert H.count_maximal_paths(small) == len(O.oracle_maximal_paths(set(small.arc_links), small.key)) == 3 ** 4


def test_ADV_27_exponential_diamond_chain_60_total_exact_reported_bounded():
    d = _arc_dag(LOG, _diamonds(60))
    en = H.enumerate_chains([d], WorkBudget(10**9), 1000, 10**9)
    assert en.total == 2 ** 60 and len(en.kept) == 1000 and isinstance(en.total, int) and en.truncated_by == "report_limit"


def test_ADV_28_count_limit_below_size_gives_bounded_unknown_without_estimate():
    d = _arc_dag(LOG, _diamonds(60))
    en = H.enumerate_chains([d], WorkBudget(10**9), 1000, len(d.key) + d.arc_count - 1)
    assert en.total is None and len(en.kept) == 1000 and en.truncated_by == "report_limit"


def test_ADV_29_pair_products_above_the_global_budget_trip_the_shared_work_limit():
    topics = [f"/n{i:02d}" for i in range(12)]
    edges = [(f"e{i}_{j}", topics[i], topics[j], None) for i in range(12) for j in range(i + 1, 12)]       # complete DAG: many paths per pair
    g = dep(edges)
    syms = [at(f"s{i:02d}", 10 * i, topics[i]) for i in range(12)]
    ana, bl, r = pipe(syms, g, limits={"paths_per_pair": 10_000}, budget_limit=3_000)
    assert r.links_status == "bounded_paths" and any(b.code == "path_work_limit_reached" for b in bl)
    assert r.path_pairs_examined + r.path_pairs_not_examined == r.path_pairs_related


def test_ADV_30_raising_the_budget_never_flips_a_claim_except_through_blockers():
    g, syms = _fan(6)
    lo, bl_lo, _ = pipe(syms, g, limits={"chains_reported": 3})
    hi, bl_hi, _ = pipe(syms, g, limits={"chains_reported": 6})
    assert lo.chains_truncated_by == "report_limit" and hi.chains_truncated_by is None
    assert all(c.defence == "not_defended" for c in lo.components) and all(c.defence == "defended_structure" for c in hi.components)
    # every difference is explained by a blocker that disappeared; the reported chains are the same prefix
    assert paths(lo) == paths(hi)[:3] and [b.code for b in bl_lo] == ["chains_truncated"] and bl_hi == []
    for budget in (10, 40, 100, 10**6):
        a, bl, r = pipe(syms, g, budget_limit=budget)
        explained = a.chains_truncated_by is not None or r.links_status != "complete"
        assert explained == any(c.defence == "not_defended" for c in a.components)       # a claim is downgraded only because a blocker was raised
        assert explained == bool(bl)


# ------------------------------------------------------------------------------------------------------------- SCL

def test_SCL_03_chain_depth_10k_and_100k_no_recursion_error():
    for n in (10_000, 100_000):
        d, names = _chain_dag(n)
        en = H.enumerate_chains([d], WorkBudget(10**9), 10, 10**9)
        assert en.total == 1 and en.kept == [(LOG, tuple(names))]


def test_SCL_04_no_recursive_function_in_localization():
    root = Path(H.__file__).parent
    offenders = []
    for f in sorted(root.glob("*.py")):
        tree = ast.parse(f.read_text())
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                name = call.func.id if isinstance(call.func, ast.Name) else (call.func.attr if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "self" else None)
                if name == fn.name:
                    offenders.append(f"{f.name}:{fn.name}")
    assert offenders == []


def test_SCL_09_total_node_visits_never_exceed_the_work_limit():
    d = _layered_dag(12, 4)
    for lim in (1, 7, 100, 12_345):
        b = WorkBudget(lim)
        en = H.enumerate_chains([d], b, 1000, 10**9)
        assert b.used <= lim and (b.used == lim) == (en.truncated_by == "work_limit")
    g = dep([(f"e{i}_{j}", f"/n{i:02d}", f"/n{j:02d}", None) for i in range(10) for j in range(i + 1, 10)])
    syms = [at(f"s{i}", 10 * i, f"/n{i:02d}") for i in range(10)]
    for lim in (5, 500, 5_000):
        b = WorkBudget(lim)
        C.analyse_links(syms, g, None, coverage={}, budget=b)
        assert b.used <= lim


def test_SCL_10_dp_count_on_a_5000_node_5000_edge_dag():
    names = [f"v{i:05d}" for i in range(5000)]
    arcs = {(names[i], names[i + 1]) for i in range(4999)} | {(names[i], names[i + 2]) for i in range(1)}
    arcs |= {(names[i], names[i + 2]) for i in range(1, 1 + 5000 - 4999)}
    d = _arc_dag(LOG, arcs)
    n = H.count_maximal_paths(d)
    assert isinstance(n, int) and n >= 1
    big = _arc_dag(LOG, _diamonds(1250))                              # 3,751 vertices, 5,000 arcs
    assert len(big.key) + big.arc_count > 5000 and H.count_maximal_paths(big) == 2 ** 1250


# ------------------------------------------------------------------------------------------------------------- misc hardening

def test_chain_model_refuses_inconsistent_content():
    g = dep([("e1", "/a", "/b", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b")], g)
    d = ana.chains[0].model_dump()
    for m in (lambda x: x.update(head="b"), lambda x: x.update(symptom_ids=["a"]), lambda x: x.update(defence="defended_structure", blocker_ids=["b-1"]),
              lambda x: x.update(defence="tied"), lambda x: x.update(nodes=["/a"]), lambda x: x["steps"][0].update(to_symptom="zz"), lambda x: x.update(extra=1)):
        bad = json.loads(json.dumps(d))
        m(bad)
        with pytest.raises(ValidationError):
            H.Chain(**bad)
    H.Chain(**json.loads(json.dumps(d)))


def test_hypothesis_models_refuse_exclusion_of_templates_2_and_3_and_free_text():
    g = dep([("e1", "/a", "/b", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b")], g)
    h = ana.chains[0].hypotheses[0].model_dump()
    for bad in ({"status": "excluded_by_declared_edge_timing"}, {"status": "consistent_with"}, {"statement": "A caused B"}, {"common_ancestors": None},
                {"artifact_facts": {"caveat_codes": [], "recording_blocker_ids": []}}):
        with pytest.raises(ValidationError):
            H.Hypothesis(**{**h, **bad})
    lh = ana.link_hypotheses[0].model_dump()
    for bad in ({"status": "excluded_by_declared_edge_timing"}, {"template": "common_or_independent_origin_not_excluded"}, {"statement": "x"}):
        with pytest.raises(ValidationError):
            H.LinkHypothesis(**{**lh, **bad})


def test_analysis_report_is_clean_and_deterministic():
    edges, ss = FIXTURES["with_ancestor"]
    ana, *_ = pipe([at(*s) for s in ss], dep(edges))
    d = ana.model_dump()
    assert find_floats(d) == [] and find_forbidden_keys(d) == [] and find_forbidden_values(d) == []
    assert pipe([at(*s) for s in ss], dep(edges))[0].model_dump_json() == ana.model_dump_json()


def test_parallel_links_merge_into_one_arc_and_redundant_via_links_are_dropped():
    g = dep([("e1", "/a", "/b", 5), ("e2", "/a", "/m", 100), ("e3", "/m", "/b", 100)])
    ana, *_ = pipe([at("a", 0, "/a"), at("b", 50, "/b"), at("m", 20, "/m")], g)
    assert paths(ana) == [("a", "m", "b")]               # a -> b (via m, outside the direct edge's window) is the redundant long arc
    g2 = dep([("e1", "/a", "/b", 100), ("e2", "/a", "/m", 100), ("e3", "/m", "/b", 100)])
    ana2, *_ = pipe([at("a", 0, "/a"), at("b", 50, "/b")], g2)
    assert paths(ana2) == [("a", "b")] and sorted(len(s.link_ids) for s in ana2.chains[0].steps) == [2]       # two declared paths, one arc


def test_the_component_set_is_independent_of_chain_truncation_bookkeeping():
    g, syms = _fan(5)
    a, *_ = pipe(syms, g)
    b, *_ = pipe(syms, g, limits={"chains_reported": 2})
    assert [c.symptom_ids for c in a.components] == [c.symptom_ids for c in b.components]


# ------------------------------------------------------------------------------------------------------------- Step 7 mutation-driven additions

class _L:
    """Duck-typed link: build_dags must not trust upstream validation for the strict-forward invariant."""
    def __init__(self, a, b, alo, ahi, blo, bhi, lane=LOG, kind="direct", inter=()):
        from types import SimpleNamespace as N
        self.label, self.lane, self.link_id, self.path_kind, self.intermediates = CAUSAL, lane, f"l-{a}-{b}", kind, list(inter)
        self.from_symptom, self.to_symptom = a, b
        self.from_onset, self.to_onset = N(lower_ns=alo, upper_ns=ahi), N(lower_ns=blo, upper_ns=bhi)


def test_build_dags_refuses_a_non_forward_consistent_link():
    H.build_dags([_L("a", "b", 0, 9, 10, 20)])                       # strictly forward: fine
    for args in ((0, 10, 10, 20), (0, 11, 10, 20), (5, 5, 5, 5), (10, 20, 0, 5)):
        with pytest.raises(ValueError, match="strictly forward"):
            H.build_dags([_L("a", "b", *args)])


def test_dp_total_is_checked_against_a_complete_enumeration(monkeypatch):
    d = _arc_dag(LOG, {("a", "b"), ("a", "c")})
    monkeypatch.setattr(H, "count_maximal_paths", lambda dag: 99)
    with pytest.raises(AssertionError, match="DP total"):
        H.enumerate_chains([d], WorkBudget(10**6), 10, 10**6)


def test_every_vertex_visit_costs_exactly_one_unit():
    d, names = _chain_dag(5)
    b = WorkBudget(10**6)
    assert len(H.enumerate_chains([d], b, 10, 10**6).kept) == 1 and b.used == 5
    fan = _arc_dag(LOG, {("h", "t1"), ("h", "t2"), ("h", "t3")})
    b2 = WorkBudget(10**6)
    H.enumerate_chains([fan], b2, 10, 10**6)
    assert b2.used == 1 + 3                      # the head once, each successor once
    for lim in range(0, 5):
        bb = WorkBudget(lim)
        en = H.enumerate_chains([_chain_dag(5)[0]], bb, 10, 10**6)
        assert bb.used == lim and en.truncated_by == "work_limit" and en.kept == []
    bb = WorkBudget(5)
    assert H.enumerate_chains([_chain_dag(5)[0]], bb, 10, 10**6).truncated_by is None


def test_a_via_arc_is_kept_when_only_one_side_of_the_intermediate_path_exists():
    g = dep([("e1", "/a", "/m", 100), ("e2", "/m", "/b", 5), ("e3", "/a", "/x", 1)])
    ana, bl, r = pipe([at("a", 0, "/a"), at("m", 20, "/m"), at("b", 50, "/b")], g)
    got = {(l.from_symptom, l.to_symptom): l.label for l in r.links}
    assert got[("a", "m")] == CAUSAL and got[("m", "b")] == "outside_declared_window" and got[("a", "b")] == CAUSAL
    assert paths(ana) == [("a", "m"), ("a", "b")]          # successors in time order: m (20) before b (50)


def test_successors_and_heads_follow_time_not_ids():
    g = dep([("e1", "/h", "/p", None), ("e2", "/h", "/q", None)])
    ana, *_ = pipe([at("h", 0, "/h"), at("z_early", 10, "/p"), at("a_late", 20, "/q")], g)
    assert paths(ana) == [("h", "z_early"), ("h", "a_late")]
    g2 = dep([("e1", "/x", "/t", None), ("e2", "/y", "/t", None)])
    ana2, *_ = pipe([at("b_first", 1, "/x"), at("a_second", 2, "/y"), at("t", 50, "/t")], g2)
    assert paths(ana2) == [("b_first", "t"), ("a_second", "t")]


def test_heads_are_ordered_by_lower_bound_before_upper_bound():
    g = dep([("e1", "/x", "/t", None), ("e2", "/y", "/t", None)])
    ana, *_ = pipe([sym("wide", 0, 100, topic="/x"), sym("narrow", 10, 20, topic="/y"), at("t", 500, "/t")], g)
    assert paths(ana) == [("wide", "t"), ("narrow", "t")]


def test_ancestors_exclude_the_chains_own_nodes_even_in_a_feedback_loop():
    g = dep([("e1", "/a", "/b", None), ("e2", "/b", "/a", None)])
    ana, *_ = pipe([at("a1", 1, "/a"), at("b", 2, "/b"), at("a3", 3, "/a")], g)
    f = ana.chains[0].hypotheses[0].common_ancestors
    assert f.ancestors_total == 0 and f.ancestors == []


def test_ancestor_symptoms_before_head_must_strictly_precede_it():
    g = dep([("e0", "/z", "/a", 1), ("e1", "/a", "/b", None)])
    syms = [at("a", 10, "/a"), at("b", 20, "/b"), sym("z_touch", 5, 10, topic="/z"), sym("z_overlap", 8, 12, topic="/z"), sym("z_before", 1, 5, topic="/z")]
    ana, *_ = pipe(syms, g)
    chain = next(c for c in ana.chains if c.head == "a")
    anc = chain.hypotheses[0].common_ancestors.ancestors[0]
    assert (anc.node, anc.symptom_ids_before_head, anc.symptom_count_before_head) == ("/z", ["z_before"], 1)


def test_coverage_evidence_is_asked_only_for_uncovered_ancestors():
    g = dep([("e0", "/z", "/a", 1), ("e1", "/a", "/b", None)])
    syms = [at("a", 10, "/a"), at("b", 20, "/b")]
    unc = pipe(syms, g)[0].chains[0].hypotheses[0].discriminating_evidence_needed
    cov = pipe(syms, g, coverage={"/z": ("covered", ["tierA.ros_log"])})[0].chains[0].hypotheses[0].discriminating_evidence_needed
    assert "detector coverage of the ancestor's topics" in unc and "detector coverage of the ancestor's topics" not in cov
    assert cov == ["an intervention or a repeat recording", "a declared edge for the ancestor"]


def test_ancestor_and_symptom_lists_are_bounded_with_exact_totals():
    edges = [(f"e{i:02d}", f"/z{i:02d}", "/a", 1) for i in range(60)] + [("eab", "/a", "/b", None)]
    syms = [at("a", 1000, "/a"), at("b", 2000, "/b")] + [at(f"z{i:02d}_{k}", k, f"/z{i:02d}") for i in range(60) for k in range(1 if i else 25)]
    ana, *_ = pipe(syms, dep(edges))
    f = next(c for c in ana.chains if c.head == "a").hypotheses[0].common_ancestors
    assert f.ancestors_total == 60 and len(f.ancestors) == H.ANCESTORS_LISTED == 50
    z0 = next(a for a in f.ancestors if a.node == "/z00")
    assert z0.symptom_count_before_head == 25 and len(z0.symptom_ids_before_head) == H.SYMPTOMS_LISTED_PER_ANCESTOR == 20
    with pytest.raises(ValidationError):
        H.CommonAncestorFacts(ancestors_total=1, ancestors=[f.ancestors[0], f.ancestors[1]])
    with pytest.raises(ValidationError):
        H.CommonAncestorFacts(ancestors_total=100, ancestors=f.ancestors + f.ancestors[:1])


def test_link_hypothesis_status_must_follow_the_label_even_with_a_matching_sentence():
    g = dep([("e1", "/a", "/b", None)])
    ana, *_ = pipe([at("a", 1, "/a"), at("b", 5, "/b")], g)
    lh = ana.link_hypotheses[0].model_dump()
    bad = {**lh, "status": "excluded_by_declared_edge_timing", "statement": H.HYPOTHESIS_SENTENCES[("propagation_along_declared_edge", "excluded_by_declared_edge_timing")]}
    with pytest.raises(ValidationError, match="follows from the link label"):
        H.LinkHypothesis(**bad)


def test_analysis_model_rules_each_enforced():
    g, syms = _fan(3)
    ana, *_ = pipe(syms, g)
    d = ana.model_dump()
    H.ChainAnalysis(**d)
    with pytest.raises(ValidationError, match="lower bound"):
        H.ChainAnalysis(**{**d, "chains_lower_bound": 0})
    with pytest.raises(ValidationError, match="below the reported"):
        H.ChainAnalysis(**{**d, "chains_total": 2, "chains_total_status": "exact"})
    with pytest.raises(ValidationError):
        H.ChainAnalysis(**{**d, "chains_total": None})
    with pytest.raises(ValidationError):
        H.ChainAnalysis(**{**d, "chains_truncated_by": "mystery"})


def test_without_a_dependency_file_there_are_no_edges_unlocalized_or_locus():
    syms = [at("a", 1, "/a"), at("b", 5, "/b")]
    empty = C.ConsistencyResult(assignments=[], links=[], links_total=0, links_suppressed=0, links_status="complete", path_pairs_related=0, path_pairs_examined=0,
                                path_pairs_not_examined=0, no_declared_relation_count=1, cross_lane_pairs_not_compared=0, edge_coverage=[])
    ana, nb = H.analyse_chains(syms, [], empty, None)
    assert nb == [] and ana.chains == [] and [c.symptom_ids for c in ana.components] == [["a"], ["b"]]
    assert ana.localization.unlocalized == [] and ana.localization.components == [] and [(t.topic, t.layer) for t in ana.localization.topic_layers] == [("/a", 0), ("/b", 1)]
