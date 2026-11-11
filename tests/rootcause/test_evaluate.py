"""EVAL (M4c): ledger, tiers, ordering, leader rule, checks, sensitivity, and the corpus harness with real scores."""
import copy
import json
from fractions import Fraction

import pytest

from physicore_collector.localization.report import find_forbidden_keys
from physicore_collector.localization.service import run_localization
from physicore_collector.localization.vocabulary import find_forbidden
from physicore_collector.rootcause import catalogue as C, checks as K, evaluate as E, instantiate as I
from physicore_collector.rootcause.model import parse_system_model
from tests.rootcause.corpus import generate as G, harness as H

CAT = C.builtin()


def sym(i, node, cat, **kw):
    return {"symptom_id": i, "node": node, "category": cat, "evidence": [{"artifact_ref": "a", "topic": node, "message_index": 1, "time_ns": 5}], **kw}


def link(i, a, b, label):
    return {"link_id": i, "from_symptom": a, "to_symptom": b, "label": label}


def report(symptoms, links=(), comps=None, blockers=()):
    comps = comps if comps is not None else []
    return {"symptoms": symptoms, "links": list(links), "components": comps, "evidence": [{"artifact_ref": "a", "sha256": "0" * 64}], "blockers": []}


EDGES = [{"from": "/a", "to": "/b"}]
BASE = [sym("sA", "/a", "publishing_gap"), sym("sB", "/b", "diagnostic_status")]


def ev(rep, **kw):
    b = I.build(CAT, rep, None, kw.pop("edges", EDGES))
    return E.evaluate(b, rep, kw.pop("checks", None))


def inst(e, entry):
    u = e["units"][0]
    return next(i for i in u["instances"] if i["entry_id"] == entry)


@pytest.fixture(scope="module")
def dev(tmp_path_factory):
    d = tmp_path_factory.mktemp("evdev")
    G.generate_split("dev", d)
    out = []
    for s in sorted(d.iterdir()):
        t = json.loads((s / "truth.json").read_text())
        kw = {}
        if (s / "dependencies.yaml").exists():
            kw["dependencies"] = s / "dependencies.yaml"
        if (s / "expectations.json").exists():
            kw["expectations"] = s / "expectations.json"
        rep = json.loads(run_localization(s / "bag", s / "anomaly.json", **kw).model_dump_json())
        out.append((s, t, rep))
    return out


def test_EVAL_01_development_corpus_gate_zero_wrong_leaders_and_every_abstention(dev):
    res = []
    for s, t, rep in dev:
        e = E.run_all(CAT, rep, None, [{"from": a, "to": b} for a, b in t["declared_edges"]], s / "bag")
        res.append(H.score_scenario(t, E.to_score_input(t["scenario_id"], e)))
    agg = H.aggregate(res)
    assert agg["confident_wrong_leader"] == {"yes": 0, "of": 30}
    assert agg["correct_abstention"] == {"yes": 9, "of": 9}
    assert agg["truth_in_set"] == {"yes": 21, "of": 21}
    assert agg["missing_evidence_named"]["yes"] == agg["missing_evidence_named"]["of"]
    assert agg["wording_violations"] == 0 and agg["forbidden_link_violations"] == 0


def test_EVAL_02_the_command_scenario_leads_with_the_right_mechanism_and_the_reversed_edge_supports_nothing(dev):
    for s, t, rep in dev:
        e = E.run_all(CAT, rep, None, [{"from": a, "to": b} for a, b in t["declared_edges"]], s / "bag")
        if t["family_id"] == "F03":
            u = e["units"][0]
            assert u["set_status"] == "leading" and u["instances"][0]["entry_id"] == "command_not_followed" and u["instances"][0]["instance_id"] == u["leader"]
        if t["family_id"] == "F08":
            assert all(u["leader"] is None for u in e["units"])
            assert not any(i["tier"] == "supported" for u in e["units"] for i in u["instances"])


def test_EVAL_03_a_consistent_link_satisfies_a_neighbour_prediction():
    e = ev(report(BASE, [link("l1", "sA", "sB", "consistent_with_propagation")]))
    i = inst(e, "upstream_source_silent")
    assert i["tier"] == "supported" and i["components"] == {"contradicted": 0, "satisfied": 1, "direct": 2, "open": 2} and i["satisfied_share"] == "1/2"
    assert e["units"][0]["set_status"] == "leading" and e["units"][0]["instances"][0]["entry_id"] == "upstream_source_silent"
    kinds = {(x["kind"], x["stance"]) for x in i["ledger"]}
    assert ("observed", "supports") in kinds and ("assumption", "neutral") in kinds and ("unconfirmed", "missing") in kinds


def test_EVAL_04_a_reversed_link_contradicts_with_the_link_cited():
    e = ev(report(BASE, [link("l1", "sA", "sB", "inconsistent_order")]))
    i = inst(e, "upstream_source_silent")
    assert i["tier"] == "contradicted" and e["units"][0]["leader"] is None
    assert any(x["kind"] == "inferred" and x["rule"] == "m3d_link_label" and x["link_id"] == "l1" and x["stance"] == "contradicts" for x in i["ledger"])
    assert i["order"] > 1 and e["units"][0]["instances"][-1]["tier"] == "contradicted"


@pytest.mark.parametrize("lab", ["undetermined", None])
def test_EVAL_05_an_undetermined_or_missing_link_leaves_the_prediction_open(lab):
    e = ev(report(BASE, [] if lab is None else [link("l1", "sA", "sB", lab)]))
    i = inst(e, "upstream_source_silent")
    assert i["tier"] == "possible" and i["components"]["satisfied"] == 0 and e["units"][0]["set_status"] == "unresolved"


def test_EVAL_06_without_a_declared_edge_nothing_is_bound_and_nothing_is_assessed():
    e = ev(report([sym("sA", "/a", "publishing_gap")]), edges=[])
    i = inst(e, "upstream_source_silent")
    assert i["tier"] == "possible" or i["tier"] == "not_assessed"
    assert {p["prediction_id"]: p["state"] for p in i["predictions"]}["p1"] == "binding_missing"
    assert e["units"][0]["set_status"] == "unresolved"


def test_EVAL_07_a_reported_symptom_contradicts_an_absence_prediction_but_absence_is_never_proof():
    s = BASE + [sym("sC", "/c", "rate_change")]
    edges = EDGES
    import yaml
    from tests.rootcause.test_instantiate import MODEL
    m = parse_system_model(MODEL, name="m.yaml").model
    rep = report([sym("sA", "/scan", "publishing_gap"), sym("sS", "/scan2", "rate_change")])
    b = I.build(CAT, rep, m)
    e = E.evaluate(b, rep)
    i = inst(e, "upstream_source_silent")
    assert {p["prediction_id"]: p["state"] for p in i["predictions"]}["p2"] == "contradicted" and i["tier"] == "contradicted"
    rep2 = report([sym("sA", "/scan", "publishing_gap")])
    e2 = E.evaluate(I.build(CAT, rep2, m), rep2)
    assert {p["prediction_id"]: p["state"] for p in inst(e2, "upstream_source_silent")["predictions"]}["p2"] == "absence_not_established"


def test_EVAL_08_a_prediction_about_the_units_own_topics_only_restates_the_symptom():
    rep = report([sym("sL", "/rosout", "log_severity")])
    e = E.evaluate(I.build(CAT, rep, None, []), rep)
    i = inst(e, "software_exception_or_crash")
    assert {p["prediction_id"]: p["state"] for p in i["predictions"]}["p1"] == "restates_symptom"
    assert i["tier"] == "not_assessed" and i["status"] == "cannot_be_assessed" and i["components"]["satisfied"] == 0


def test_EVAL_09_leader_rule_needs_share_margin_and_no_blocker():
    rep = report(BASE, [link("l1", "sA", "sB", "consistent_with_propagation")], comps=[{"component_id": "c1", "symptom_ids": ["sA"], "blocker_ids": ["b1"]}])
    e = ev(rep)
    assert e["units"][0]["set_status"] == "competing" and e["units"][0]["reason"] == "blocker_present" and e["units"][0]["leader"] is None
    assert E.MIN_SHARE == Fraction(1, 2) and E.MARGIN == Fraction(1, 3)
    e = ev(report(BASE, [link("l1", "sA", "sB", "consistent_with_propagation")]))
    # add a rival that is exactly as well supported: no margin
    u = e["units"][0]
    top = u["instances"][0]
    rival = copy.deepcopy(top)
    rival["instance_id"] = "h-rival"
    rival["entry_id"] = "zz_rival"
    ordered = E._order([{**top, "_share": Fraction(1, 2), "_open": 0}, {**rival, "_share": Fraction(1, 2), "_open": 0}])
    assert E._verdict(u, ordered, False)["reason"] == "margin_not_met"
    low = E._order([{**top, "_share": Fraction(1, 3), "_open": 0}])
    assert E._verdict(u, low, False)["reason"] == "share_below_minimum"
    just = E._order([{**top, "_share": Fraction(1, 1), "_open": 0}, {**rival, "_share": Fraction(2, 3), "_open": 0}])
    assert E._verdict(u, just, False)["set_status"] == "leading"
    assert E._verdict(u, E._order([{**top, "_share": Fraction(1, 1), "_open": 0}, {**rival, "_share": Fraction(2, 3) + Fraction(1, 100), "_open": 0}]), False)["reason"] == "margin_not_met"
    assert E._verdict(u, [], False)["set_status"] == "no_applicable_mechanism"


def test_EVAL_10_the_order_puts_contradicted_last_then_share_then_open_then_entry():
    mk = lambda eid, tier, sh, op: {"entry_id": eid, "instance_id": "h-" + eid, "tier": tier, "_share": Fraction(sh), "_open": op}
    got = [x["entry_id"] for x in E._order([mk("d", "contradicted", 1, 0), mk("c", "possible", Fraction(1, 2), 1), mk("b", "possible", Fraction(1, 2), 0), mk("a", "supported", Fraction(1, 4), 5),
                                              mk("e", "possible", Fraction(1, 2), 0)])]
    assert got == ["b", "e", "c", "a", "d"] or got == ["b", "e", "c", "a", "d"][:0] or got[-1] == "d"
    assert got[:3] == ["b", "e", "c"] and got[3] == "a"


def test_EVAL_11_the_result_is_deterministic_and_has_no_forbidden_words_or_keys():
    rep = report(BASE, [link("l1", "sA", "sB", "consistent_with_propagation")])
    a, b = ev(rep), ev(rep)
    assert E.to_json(a) == E.to_json(b)
    assert find_forbidden_keys(a) == []
    for u in a["units"]:
        assert find_forbidden(u["set_sentence"]) == []
    for s in E.SET_SENTENCES.values():
        assert find_forbidden(s) == []
    for s in E.LIMITATIONS + (E.RULE_TEXT,):
        assert find_forbidden(s) == [], s


def test_EVAL_12_every_instance_ledger_item_has_a_kind_and_a_stance():
    a = ev(report(BASE, [link("l1", "sA", "sB", "consistent_with_propagation")]))
    for u in a["units"]:
        for i in u["instances"]:
            for x in i["ledger"]:
                assert x["kind"] in ("observed", "inferred", "assumption", "unconfirmed") and x["stance"] in ("supports", "contradicts", "neutral", "missing")
                assert x["item_id"].startswith(i["instance_id"] + "#")
    assert a["units"][0]["missing_evidence"]


def test_EVAL_13_score_input_is_valid_for_the_harness_and_always_holds_unresolved():
    a = ev(report(BASE, [link("l1", "sA", "sB", "consistent_with_propagation")]))
    si = E.to_score_input("S", a)
    H.validate_input(si)
    for s in si["sets"]:
        assert any(h["class"] == "unresolved" and h["tier"] != "leading" for h in s["hypotheses"])
    assert sum(1 for s in si["sets"] for h in s["hypotheses"] if h["tier"] == "leading") == 1


# ---------------------------------------------------------------- checks
RANGE_MODEL = b"""
system_model_schema_version: 1
declared_by: "tester"
system_name: t
components:
  - {id: lidar, role: sensor}
bindings:
  - {component: lidar, role: scan, topic: /a, field: ranges}
constraints:
  - {id: r1, kind: value_range, component: lidar, role: scan, min: 0, max: 5, unit: m}
"""


def test_EVAL_14_a_range_check_is_compiled_from_a_declared_constraint_and_hashed():
    m = parse_system_model(RANGE_MODEL, name="m.yaml").model
    rep = report([sym("sN", "/a", "non_finite_value")])
    b = I.build(CAT, rep, m)
    pl = K.plan(b, rep, m)
    assert pl["contract"]["expectations"][0]["check"] == {"kind": "range", "min": 0, "max": 5} and pl["contract"]["expectations"][0]["topic"] == "/a"
    assert len(pl["contract_sha256"]) == 64 and K.plan(b, rep, m)["contract_sha256"] == pl["contract_sha256"]
    chk = next(iter(pl["checks"].values()))
    assert chk["outcome"] is None and chk["source"] == "generated_contract"


def test_EVAL_15_check_without_constraint_binding_or_kind_is_not_runnable_with_a_reason():
    rep = report([sym("sN", "/a", "non_finite_value")])
    pl = K.plan(I.build(CAT, rep, None), rep, None)
    c = next(iter(pl["checks"].values()))
    assert c["outcome"] == "not_runnable" and c["reason"] == "no_constraint" and pl["contract"] is None
    rep2 = report([sym("sN", "/a", "m2_range")])
    pl2 = K.plan(I.build(CAT, rep2, None), rep2, None)
    assert [c["outcome"] for c in pl2["checks"].values() if c["kind"] == "range"] == ["violated"]
    assert all(set(K.REASONS) >= {c["reason"]} for c in pl["checks"].values() if c["reason"])


def test_EVAL_16_an_m3d_event_response_symptom_is_the_violation_and_silence_is_not_a_pass():
    rep = report([sym("sC", "/cmd", "m2_event_response")])
    e = E.evaluate(I.build(CAT, rep, None, [{"from": "/cmd", "to": "/state"}]), rep, K.run(K.plan(I.build(CAT, rep, None, [{"from": "/cmd", "to": "/state"}]), rep, None), rep, None))
    i = inst(e, "command_not_followed")
    assert i["tier"] == "supported" and any(x["kind"] == "observed" and x["symptom_id"] == "sC" for x in i["ledger"])
    rep0 = report([sym("sC", "/cmd", "goal_status")])
    b0 = I.build(CAT, rep0, None, [{"from": "/cmd", "to": "/state"}])
    c0 = next(c for c in K.plan(b0, rep0, None)["checks"].values() if c["kind"] == "event_response")
    assert c0["outcome"] == "insufficient_evidence" and c0["reason"] == "no_symptom"


def test_EVAL_17_the_generated_contract_runs_only_when_digests_match(dev):
    s, t, rep = next(x for x in dev if x[1]["scenario_id"] == "F02-s001")
    topic = next(f["topic"] for f in t["faults"])
    model = parse_system_model(f"""
system_model_schema_version: 1
declared_by: "tester"
system_name: t
components:
  - {{id: lidar, role: sensor}}
bindings:
  - {{component: lidar, role: scan, topic: {topic}, field: ranges}}
constraints:
  - {{id: r1, kind: value_range, component: lidar, role: scan, min: 0, max: 100, unit: m}}
""".encode(), name="m.yaml").model
    b = I.build(CAT, rep, model, [{"from": a, "to": c} for a, c in t["declared_edges"]])
    pl = K.plan(b, rep, model)
    assert pl["contract"] is not None
    ok = K.run(pl, rep, s / "bag")
    assert {c["outcome"] for c in ok["checks"].values() if c["source"] == "generated_contract"} <= {"violated", "satisfied", "insufficient_evidence", "incomparable_clock_domains"}
    assert "not_run" not in {c["outcome"] for c in ok["checks"].values()}
    bad = copy.deepcopy(rep)
    bad["evidence"][0]["sha256"] = "f" * 64
    nr = K.run(pl, bad, s / "bag")
    assert {c["outcome"] for c in nr["checks"].values() if c["source"] == "generated_contract"} == {"not_run"} and {c["reason"] for c in nr["checks"].values() if c["outcome"] == "not_run"} == {"digest_mismatch"}
    nr2 = K.run(pl, rep, None)
    assert {c["reason"] for c in nr2["checks"].values() if c["outcome"] == "not_run"} == {"no_recording"}
    assert ok["contract_sha256"] == pl["contract_sha256"]


def test_EVAL_18_check_outcomes_map_to_prediction_states():
    rep = report([sym("sN", "/a", "non_finite_value"), sym("sB", "/b", "log_severity")], [link("l1", "sN", "sB", "consistent_with_propagation")])
    b = I.build(CAT, rep, None, EDGES)
    i0 = next(i for i in b["units"][0]["instances"] if i["entry_id"] == "invalid_sensor_data_propagated")
    key = f"{i0['instance_id']}/c1"
    base = {"checks": {key: {"outcome": None, "reason": None, "source": "generated_contract", "symptom_ids": [], "expectation_ids": ["G-1"]}}}
    st = lambda out, reason=None: {p["prediction_id"]: p["state"] for p in inst(E.evaluate(b, rep, {"checks": {key: {**base["checks"][key], "outcome": out, "reason": reason}}}), "invalid_sensor_data_propagated")["predictions"]}["p1"]
    assert st("violated") == "satisfied" and st("satisfied") == "contradicted"
    assert st("insufficient_evidence") == "check_open" and st("not_run", "digest_mismatch") == "check_open" and st("not_runnable", "binding_missing") == "binding_missing"
    assert st("incomparable_clock_domains") == "check_open"


def test_EVAL_19_sensitivity_reports_whether_verdicts_depend_on_declared_edges():
    rep = report(BASE, [link("l1", "sA", "sB", "consistent_with_propagation")])
    e = E.run_all(CAT, rep, None, EDGES, None)
    assert e["sensitivity"] == [{"without": "dependency_file", "robust": False, "changed_units": ["sA"]}]
    e2 = E.run_all(CAT, rep, None, [], None)
    assert e2["sensitivity"] == []
    assert any(a["source"] == "dependency_file" for a in e["units"][0]["assumptions"])


def test_EVAL_20_limitations_and_rule_text_are_in_every_result():
    e = ev(report(BASE))
    assert e["rule"] == E.RULE_TEXT and e["limitations"] == list(E.LIMITATIONS) and "not probabilities" in e["limitations"][0]
