"""EVAL+ (M4c): second layer pinning slot kinds, link directions, check statuses and digest rules one by one."""
import copy
from fractions import Fraction
from types import SimpleNamespace

import yaml

from physicore_collector.rootcause import catalogue as C, checks as K, evaluate as E, instantiate as I
from physicore_collector.rootcause.model import parse_system_model
from tests.rootcause.test_catalogue import user_entry, user_file
from tests.rootcause.test_evaluate import BASE, CAT, EDGES, ev, inst, link, report, sym
from tests.rootcause.test_instantiate import MODEL


def user_cat(slot_selects, op="symptom_present", cats=("rate_change",)):
    e = user_entry(slots=[{"name": "x", "selects": slot_selects}, {"name": "late", "selects": "symptom_topics"}],
                   predictions=[{"id": "p1", "text": "A thing.", "rule": {"op": op, "slot": "x", "categories": list(cats)}}],
                   applies_to={"symptom_categories": ["publishing_gap"]})
    u = C.parse_catalogue(yaml.safe_dump(user_file(e)).encode())
    assert u.entries is not None, u.problems
    return C.merged(u)


def state(e, entry="user.sag", unit=0):
    i = next(i for i in e["units"][unit]["instances"] if i["entry_id"] == entry)
    return i["predictions"][0]["state"], i


def test_EVALX_01_sibling_slot_present_prediction_is_satisfied_only_by_a_reported_symptom():
    m = parse_system_model(MODEL, name="m.yaml").model
    cat = user_cat("sibling_topics")
    hit = report([sym("sA", "/scan", "publishing_gap"), sym("sS", "/scan2", "rate_change")])
    assert state(E.evaluate(I.build(cat, hit, m), hit))[0] == "satisfied"
    miss = report([sym("sA", "/scan", "publishing_gap"), sym("sS", "/scan2", "log_severity")])
    assert state(E.evaluate(I.build(cat, miss, m), miss))[0] == "not_observed"


def test_EVALX_02_own_topic_prediction_of_a_user_entry_restates():
    rep = report([sym("sA", "/scan", "publishing_gap"), sym("sS", "/scan", "rate_change")])
    cat = user_cat("symptom_topics")
    s, i = state(E.evaluate(I.build(cat, rep, None), rep))
    assert s == "restates_symptom" and i["satisfied_share"] == "0/0" and i["tier"] == "not_assessed"


def test_EVALX_03_upstream_slot_uses_the_link_in_the_other_direction():
    cat = user_cat("declared_upstream_topics", cats=("publishing_gap",))
    edges = [{"from": "/a", "to": "/b"}]
    rep = report([sym("sA", "/a", "publishing_gap"), sym("sB", "/b", "publishing_gap")], [link("l1", "sA", "sB", "consistent_with_propagation")])
    e = E.evaluate(I.build(cat, rep, None, edges), rep)
    ub = next(u for u in e["units"] if u["topics"] == ["/b"])
    ua = next(u for u in e["units"] if u["topics"] == ["/a"])
    assert next(i for i in ub["instances"] if i["entry_id"] == "user.sag")["predictions"][0]["state"] == "satisfied"
    assert "user.sag" not in [i["entry_id"] for i in ua["instances"]] or next(i for i in ua["instances"] if i["entry_id"] == "user.sag")["predictions"][0]["state"] in ("binding_missing",)
    rev = report([sym("sA", "/a", "publishing_gap"), sym("sB", "/b", "publishing_gap")], [link("l1", "sB", "sA", "consistent_with_propagation")])
    e2 = E.evaluate(I.build(cat, rev, None, edges), rev)
    ub2 = next(u for u in e2["units"] if u["topics"] == ["/b"])
    assert next(i for i in ub2["instances"] if i["entry_id"] == "user.sag")["predictions"][0]["state"] == "not_observed"


def test_EVALX_04_every_bad_label_contradicts_and_a_consistent_link_wins():
    for lab in ("inconsistent_order", "outside_declared_window"):
        e = ev(report(BASE, [link("l1", "sA", "sB", lab)]))
        assert inst(e, "upstream_source_silent")["tier"] == "contradicted", lab
    e = ev(report(BASE, [link("l1", "sA", "sB", "inconsistent_order"), link("l2", "sA", "sB", "consistent_with_propagation")]))
    assert inst(e, "upstream_source_silent")["tier"] == "supported"


def test_EVALX_05_a_contradicted_instance_with_a_high_share_does_not_block_the_leader():
    mk = lambda eid, tier, sh: {"entry_id": eid, "instance_id": "h-" + eid, "tier": tier, "_share": Fraction(sh), "_open": 0}
    ordered = E._order([mk("a", "supported", 1), mk("b", "contradicted", Fraction(1, 1))])
    assert E._verdict({}, ordered, False)["set_status"] == "leading"
    assert E._verdict({}, E._order([mk("a", "contradicted", 1)]), False)["set_status"] == "unresolved"
    assert E._verdict({}, E._order([mk("a", "possible", Fraction(2, 3))]), False)["set_status"] == "unresolved"


def test_EVALX_06_missing_evidence_lists_only_usually_absent_tokens_of_live_instances():
    rep = report(BASE, [link("l1", "sA", "sB", "inconsistent_order")])
    b = I.build(CAT, rep, None, EDGES)
    e = E.evaluate(b, rep)
    dead = {i["instance_id"] for u in e["units"] for i in u["instances"] if i["tier"] == "contradicted"}
    want = set()
    for u in b["units"][:1]:
        for i in u["instances"]:
            if i["instance_id"] not in dead:
                want |= {r["token"] for r in i["evidence_requirements"] if r["usual_availability"] == "usually_absent"}
    only_dead = {r["token"] for u in b["units"][:1] for i in u["instances"] if i["instance_id"] in dead for r in i["evidence_requirements"]} - want
    assert set(e["units"][0]["missing_evidence"]) == want
    present = {r["token"] for en in CAT.entries for r in en["evidence_requirements"] if r["usual_availability"] == "usually_present"}
    assert not (set(e["units"][0]["missing_evidence"]) & present)
    assert only_dead == set() or not (set(e["units"][0]["missing_evidence"]) & only_dead)


def test_EVALX_07_sensitivity_covers_both_declared_sources():
    m = parse_system_model(MODEL, name="m.yaml").model
    rep = report([sym("sS", "/scan", "publishing_gap"), sym("sD", "/diag", "diagnostic_status")], [link("l1", "sS", "sD", "consistent_with_propagation")])
    e = E.run_all(CAT, rep, m, [], None)
    assert [(s["without"], s["robust"]) for s in e["sensitivity"]] == [("system_model", False)]
    e2 = E.run_all(CAT, rep, m, [{"from": "/scan", "to": "/diag"}], None)       # the same edge is declared twice, so dropping either source changes nothing
    assert [(s["without"], s["robust"]) for s in e2["sensitivity"]] == [("dependency_file", True), ("system_model", True)]


def test_EVALX_08_signature_includes_the_leading_entry():
    def e(entry):
        return {"units": [{"unit_id": "u", "set_status": "leading", "leader": "h1", "instances": [{"instance_id": "h1", "entry_id": entry}]}]}
    assert E._signature(e("a")) != E._signature(e("b"))


def test_EVALX_09_check_planning_edge_cases():
    rep = report([sym("sC", "/cmd", "goal_status")])
    ent = user_entry(slots=[{"name": "x", "selects": "sibling_topics"}], applies_to={"symptom_categories": ["goal_status"]},
                     checks=[{"id": "c1", "kind": "range", "slot": "x", "constraint_kind": "value_range", "description": "A check."}],
                     predictions=[{"id": "p1", "text": "A thing.", "rule": {"op": "check_violated", "check": "c1"}}])
    ucat = C.merged(C.parse_catalogue(yaml.safe_dump(user_file(ent)).encode()))
    bu = I.build(ucat, rep, None, [])
    uid = next(i["instance_id"] for u in bu["units"] for i in u["instances"] if i["entry_id"] == "user.sag")
    c = K.plan(bu, rep, None)["checks"][f"{uid}/c1"]
    assert c["outcome"] == "not_runnable" and c["reason"] == "binding_missing" and c["source"] == "none"
    m = parse_system_model(b"""
system_model_schema_version: 1
declared_by: "t"
system_name: t
components:
  - {id: s1, role: sensor}
  - {id: s2, role: sensor}
bindings:
  - {component: s1, role: out, topic: /a, field: v}
  - {component: s1, role: nofield, topic: /a}
  - {component: s2, role: out, topic: /z, field: w}
constraints:
  - {id: c_b, kind: value_range, component: s2, role: out, min: 1, max: 2, unit: m}
  - {id: c_a, kind: value_range, component: s1, role: out, min: 0, max: 5, unit: m}
  - {id: c_nf, kind: value_range, component: s1, role: nofield, min: 0, max: 9, unit: m}
""", name="m.yaml").model
    rep2 = report([sym("sN", "/a", "non_finite_value")])
    pl = K.plan(I.build(CAT, rep2, m), rep2, m)
    ex = pl["contract"]["expectations"]
    assert [x["topic"] for x in ex] == ["/a"] and ex[0]["signal"] == "v"            # not /z (other topic) and not the binding without a field
    m2 = parse_system_model(b"""
system_model_schema_version: 1
declared_by: "t"
system_name: t
components:
  - {id: s1, role: sensor}
bindings:
  - {component: s1, role: out, topic: /a, field: v}
  - {component: s1, role: o2, topic: /a, field: w}
constraints:
  - {id: z_last, kind: value_range, component: s1, role: out, min: 0, max: 5, unit: m}
  - {id: a_first, kind: value_range, component: s1, role: o2, min: 0, max: 5, unit: m}
""", name="m.yaml").model
    pl2 = K.plan(I.build(CAT, rep2, m2), rep2, m2)
    ids = [x["expectation_id"] for x in pl2["contract"]["expectations"]]
    assert len(ids) == 2 and ids == sorted(ids)


def test_EVALX_10_digest_comparison_rules():
    art = lambda r, h: SimpleNamespace(ref_id=r, sha256=h, kind="mcap_recording")
    rep = {"evidence": [{"artifact_ref": "a", "sha256": "1"}, {"artifact_ref": "b", "sha256": "2"}]}
    assert K.digests_match(rep, SimpleNamespace(artifacts=[art("b", "2"), art("a", "1")]))
    assert not K.digests_match(rep, SimpleNamespace(artifacts=[art("a", "1")]))
    assert not K.digests_match({"evidence": []}, SimpleNamespace(artifacts=[]))
    assert not K.digests_match({"evidence": [{"artifact_ref": "a", "sha256": "1"}]}, SimpleNamespace(artifacts=[art("a", "9")]))
    assert K.digests_match({"evidence": [{"artifact_ref": "a", "sha256": "1"}]}, SimpleNamespace(artifacts=[art("a", "1"), SimpleNamespace(ref_id="x", sha256="5", kind="other")]))


def test_EVALX_11_combining_outcomes():
    c = K._combine
    assert c(["satisfied", "violated"]) == "violated" and c(["satisfied", "satisfied"]) == "satisfied"
    assert c(["satisfied", "insufficient_evidence"]) == "insufficient_evidence" and c(["satisfied", "incomparable_clock_domains"]) == "incomparable_clock_domains"
    assert c(["satisfied", "not_runnable"]) == "not_runnable" and c([]) == "insufficient_evidence"
    assert c(["insufficient_evidence", "incomparable_clock_domains"]) == "incomparable_clock_domains"


def test_EVALX_12_investigator_statuses_map_to_check_outcomes(monkeypatch):
    import physicore_collector.inventory as inv
    import physicore_collector.investigator as invest
    import physicore_collector.investigator.contract as cmod
    rep = report([sym("sN", "/a", "non_finite_value")])
    rep["evidence"] = [{"artifact_ref": "r", "sha256": "1"}]
    m = parse_system_model(b"""
system_model_schema_version: 1
declared_by: "t"
system_name: t
components:
  - {id: s1, role: sensor}
bindings:
  - {component: s1, role: out, topic: /a, field: v}
constraints:
  - {id: c1, kind: value_range, component: s1, role: out, min: 0, max: 5, unit: m}
""", name="m.yaml").model
    pl = K.plan(I.build(CAT, rep, m), rep, m)
    eid = pl["contract"]["expectations"][0]["expectation_id"]
    monkeypatch.setattr(inv, "inspect_evidence", lambda p: SimpleNamespace(artifacts=[SimpleNamespace(ref_id="r", sha256="1", kind="mcap_recording")]))
    monkeypatch.setattr(cmod, "parse_contract", lambda d: d)
    for status, want in (("divergence_found", "violated"), ("no_divergence_observed_in_covered_window", "satisfied"), ("insufficient_evidence", "insufficient_evidence"),
                         ("incomparable_timebases", "incomparable_clock_domains"), ("invalid_expectation", "not_runnable")):
        monkeypatch.setattr(invest, "investigate", lambda i, l, status=status: SimpleNamespace(expectation_results=[SimpleNamespace(expectation_id=eid, status=SimpleNamespace(value=status))]))
        out = K.run(pl, rep, "x")
        assert [c["outcome"] for c in out["checks"].values()] == [want], status
    monkeypatch.setattr(invest, "investigate", lambda i, l: SimpleNamespace(expectation_results=[]))
    assert [c["outcome"] for c in K.run(pl, rep, "x")["checks"].values()] == ["not_runnable"]


def test_EVALX_13_a_usually_present_token_is_not_listed_as_missing():
    e = user_entry(evidence_requirements=[{"token": "bus_load", "description": "Bus load figures.", "usual_availability": "usually_absent"},
                                          {"token": "topic_list", "description": "The topic list.", "usual_availability": "usually_present"}],
                   applies_to={"symptom_categories": ["publishing_gap"]})
    cat = C.merged(C.parse_catalogue(yaml.safe_dump(user_file(e)).encode()))
    rep = report([sym("sA", "/a", "publishing_gap")])
    out = E.evaluate(I.build(cat, rep, None, []), rep)
    assert "bus_load" in out["units"][0]["missing_evidence"] and "topic_list" not in out["units"][0]["missing_evidence"]
