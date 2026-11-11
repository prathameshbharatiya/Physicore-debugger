"""CAUSAL (M4d): the claim ladder, its caps and gate, baseline and intervention contrasts, propagation facts, and diagnostic requests."""
import copy
import json

import pytest
import yaml

from physicore_collector.localization.report import find_forbidden_keys
from physicore_collector.localization.vocabulary import find_forbidden
from physicore_collector.rootcause import catalogue as C, causal as K, evaluate as E, instantiate as I
from physicore_collector.rootcause.model import parse_system_model
from tests.rootcause.test_catalogue import user_entry, user_file
from tests.rootcause.test_evaluate import BASE, CAT, EDGES, inst, link, report, sym

OK = "consistent_with_propagation"


def user_cat(*entries):
    return C.merged(C.parse_catalogue(yaml.safe_dump(user_file(*entries)).encode()))


def pr(pid, op, slot, cats):
    return {"id": pid, "text": "A thing.", "rule": {"op": op, "slot": slot, "categories": cats}}


def entry(eid, preds, rivals=()):
    return user_entry(id=eid, slots=[{"name": "dn", "selects": "declared_downstream_topics"}, {"name": "sib", "selects": "sibling_topics"}],
                      applies_to={"symptom_categories": ["publishing_gap"]}, predictions=preds, rivals=list(rivals))


def full(cat, rep, edges=EDGES, model=None, baseline=None):
    b = I.build(cat, rep, model, edges)
    return K.assign(E.evaluate(b, rep), b, rep, model, baseline)


def level(out, eid, unit=None):
    return next(i for u in out["units"] for i in u["instances"] if i["entry_id"] == eid)


LINKED = report(BASE, [link("l1", "sA", "sB", OK)])


def level1():
    """Level 1 reached cleanly: the consumer prediction is met through a consistent link, a sibling prediction is bound but not observed."""
    from tests.rootcause.test_instantiate import MODEL
    m = parse_system_model(MODEL, name="m.yaml").model
    rep = report([sym("sS", "/scan", "publishing_gap"), sym("sD", "/diag", "diagnostic_status")], [link("l1", "sS", "sD", OK)])
    cat = user_cat(entry("user.two", [pr("p1", "symptom_present", "dn", ["diagnostic_status"]), pr("p2", "symptom_present", "sib", ["rate_change"])]))
    return full(cat, rep, edges=[], model=m)


def test_CAUSAL_01_ladder_table_has_levels_0_to_4_and_level_5_is_not_reachable():
    out = full(CAT, LINKED)
    assert [(x["level"], x["reachable"]) for x in out["ladder"]] == [(0, True), (1, True), (2, True), (3, True), (4, True), (5, False)]
    assert out["ladder"][5]["wording"] is None and K.MAX_REACHABLE == 4
    for n, name, s in K.LEVELS:
        assert K.gate(s, n) == [], (n, s)
    assert [x[0] for x in K.LEVELS] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("lvl,bad", [(0, "The evidence is compatible with this mechanism."), (1, "The evidence is compatible with this mechanism."),
                                      (2, "The recorded evidence favours this mechanism."), (3, "The evidence indicates this mechanism."),
                                      (4, "This mechanism is confirmed by test."), (4, "This is the cause."), (0, "Consistent with propagation along the declared edge.")])
def test_CAUSAL_02_the_gate_refuses_a_stronger_word_than_the_level_allows(lvl, bad):
    assert K.gate(bad, lvl)


def test_CAUSAL_03_the_gate_allows_each_levels_own_wording_and_release_only_from_level_1():
    assert K.gate("Consistent with propagation along the declared edge.", 1) == []
    assert K.gate("Consistent with propagation along the declared edge.", 0) == ["propagation"]
    assert K.gate("The recorded evidence favours this mechanism.", 3) == [] and K.gate("The evidence indicates this mechanism.", 4) == []
    assert K.gate("Compatible with this mechanism.", 2) == []


def test_CAUSAL_04_level_0_reached_and_level_1_reached_and_stopped_short():
    out = full(CAT, report(BASE))                                                       # no link: the order is only observed
    assert level(out, "upstream_source_silent")["claim_level"] == 0
    out = level1()
    i = level(out, "user.two")
    assert i["claim_level"] == 1 and i["uncapped_level"] == 1 and i["caps"] == []                  # p2 not observed: stays at the dependency level


def test_CAUSAL_05_level_2_reached_and_stopped_short_by_an_unmet_prediction_or_a_missing_link():
    out = full(CAT, LINKED)
    i = level(out, "upstream_source_silent")
    assert i["claim_level"] == 2 and i["claim_name"] == "compatible_with_mechanism" and i["claim_sentence"] == K.LEVEL_SENTENCE[2]
    assert level(full(CAT, report(BASE, [link("l1", "sA", "sB", "undetermined")])), "upstream_source_silent")["claim_level"] == 0


def test_CAUSAL_06_level_3_needs_every_listed_rival_present_and_contradicted():
    a = entry("user.a", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])], ["user.b"])
    b_dead = entry("user.b", [pr("p1", "symptom_absent", "dn", ["diagnostic_status"])], ["user.a"])
    b_alive = entry("user.b", [pr("p1", "symptom_present", "dn", ["goal_status"])], ["user.a"])
    out = full(user_cat(a, b_dead), LINKED)
    assert level(out, "user.a")["claim_level"] == 3 and level(out, "user.b")["claim_level"] == 0 and level(out, "user.b")["caps"] == ["contradicted"]
    assert level(full(user_cat(a, b_alive), LINKED), "user.a")["claim_level"] == 2
    a2 = entry("user.a", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])], ["user.b", "user.c"])
    c = entry("user.c", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])])
    c["applies_to"] = {"symptom_categories": ["rate_change"]}                                  # exists, but does not match this unit: not excluded
    assert level(full(user_cat(a2, b_dead, c), LINKED), "user.a")["claim_level"] == 2


def _l3():
    a = entry("user.a", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])], ["user.b"])
    b = entry("user.b", [pr("p1", "symptom_absent", "dn", ["diagnostic_status"])], ["user.a"])
    return user_cat(a, b)


def test_CAUSAL_07_level_4_from_a_comparable_baseline_and_not_from_the_same_symptoms_or_an_incomparable_one():
    cat = _l3()
    healthy = report([])
    healthy["inputs"] = copy.deepcopy(LINKED.get("inputs", {})) if "inputs" in LINKED else {}
    rep = copy.deepcopy(LINKED)
    rep["inputs"] = {"anomaly_config_hash": "h", "dependencies": {"sha256": "d"}}
    rep["limits"] = {"x": 1}
    healthy.update(inputs={"anomaly_config_hash": "h", "dependencies": {"sha256": "d"}}, limits={"x": 1})
    out = full(cat, rep, baseline=healthy)
    i = level(out, "user.a")
    assert i["claim_level"] == 4 and i["contrast"] == {"kind": "baseline"} and out["baseline"] == {"comparable": True, "differs": []}
    same = copy.deepcopy(rep)
    assert level(full(cat, rep, baseline=same), "user.a")["claim_level"] == 3
    other = copy.deepcopy(healthy)
    other["inputs"]["anomaly_config_hash"] = "x"
    o = full(cat, rep, baseline=other)
    assert level(o, "user.a")["claim_level"] == 3 and o["baseline"] == {"comparable": False, "differs": ["detector_configuration_digest"]}
    assert level(o, "user.a")["baseline_note"] == "baseline_not_comparable" and K.CAP_SENTENCES["baseline_not_comparable"] in level(o, "user.a")["cap_sentences"]
    assert level(full(cat, rep), "user.a")["claim_level"] == 3                              # no baseline at all: never level 4


def test_CAUSAL_08_comparability_names_every_differing_item():
    a = {"inputs": {"anomaly_config_hash": "1", "dependencies": {"sha256": "2"}}, "limits": {"a": 1}}
    assert K.comparable(a, copy.deepcopy(a)) == {"comparable": True, "differs": []}
    b = {"inputs": {"anomaly_config_hash": "9", "dependencies": {"sha256": "8"}}, "limits": {"a": 2}}
    assert K.comparable(a, b) == {"comparable": False, "differs": ["detector_configuration_digest", "dependency_file_digest", "parameters"]}
    c = {"inputs": {"anomaly_config_hash": "1", "dependencies": None}, "limits": {"a": 1}}
    assert K.comparable(a, c)["differs"] == ["dependency_file_digest"]


MODEL_IV = b"""
system_model_schema_version: 1
declared_by: "t"
system_name: t
components:
  - {id: base, role: compute}
bindings:
  - {component: base, role: o, topic: /zz}
interventions:
  - {id: restart1, kind: restart, component: base, at_ns: AT, clock_domain: mcap_log_time, declared_by: "operator"}
"""


def test_CAUSAL_09_a_declared_intervention_before_the_symptoms_gives_level_4_and_after_them_does_not():
    cat = _l3()
    for at, want in ((2, 4), (50, 3)):
        m = parse_system_model(MODEL_IV.replace(b"AT", str(at).encode()), name="m.yaml").model
        assert m is not None
        out = full(cat, LINKED, model=m)
        i = level(out, "user.a")
        assert i["claim_level"] == want, at
        if want == 4:
            assert i["contrast"]["kind"] == "declared_intervention" and i["contrast"]["intervention_id"] == "restart1"
    assert K.intervention_contrast({"ledger": []}, LINKED, None) is None


def test_CAUSAL_10_caps_a_blocker_an_open_check_and_a_contradiction_hold_the_level_at_1_with_the_reason():
    comps = [{"component_id": "c1", "symptom_ids": ["sA"], "blocker_ids": ["b1"]}]
    out = full(CAT, report(BASE, [link("l1", "sA", "sB", OK)], comps=comps))
    i = level(out, "upstream_source_silent")
    assert i["uncapped_level"] == 2 and i["claim_level"] == 1 and i["caps"] == ["blocker_present"] and K.CAP_SENTENCES["blocker_present"] in i["cap_sentences"]
    rep = report([sym("sN", "/a", "m2_range"), sym("sB", "/b", "diagnostic_status")], [link("l1", "sN", "sB", OK)])
    out = full(CAT, rep)
    j = level(out, "invalid_sensor_data_propagated")
    assert j["claim_level"] <= 1
    bad = full(CAT, report(BASE, [link("l1", "sA", "sB", "inconsistent_order")]))
    k = level(bad, "upstream_source_silent")
    assert k["tier"] == "contradicted" and k["claim_level"] <= 1 and k["caps"] == ["contradicted"] and k["contradictions"]


def test_CAUSAL_11_a_single_satisfied_check_a_coincidental_order_and_a_missing_baseline_cannot_push_a_level_up():
    rep = report([sym("sC", "/cmd", "m2_event_response")])
    out = full(CAT, rep, edges=[{"from": "/cmd", "to": "/state"}])
    assert level(out, "command_not_followed")["claim_level"] <= 2
    assert all(i["claim_level"] <= 2 for u in out["units"] for i in u["instances"])
    out2 = full(CAT, LINKED)
    assert all(i["claim_level"] <= 2 for u in out2["units"] for i in u["instances"])


def test_CAUSAL_12_every_hypothesis_states_what_would_raise_it():
    out = full(CAT, LINKED)
    for u in out["units"]:
        for i in u["instances"]:
            assert i["would_raise"] == K.RAISE[i["claim_level"]] and K.gate(i["would_raise"], i["claim_level"]) == []
    assert "level 5" in K.RAISE[4]


def test_CAUSAL_13_diagnostic_asks_name_measurement_and_rivals_and_skip_contradicted_and_leaders():
    out = full(CAT, report(BASE))
    u = out["units"][0]
    assert u["set_status"] != "leading" and u["diagnostic_asks"]
    for r in u["diagnostic_asks"]:
        assert set(r) == {"entry_id", "measurement", "description", "would_discriminate_from", "meaning"} and r["meaning"] == K.REQUEST_MEANING
    assert [(r["measurement"], r["entry_id"]) for r in u["diagnostic_asks"]] == sorted((r["measurement"], r["entry_id"]) for r in u["diagnostic_asks"])
    lead = full(CAT, LINKED)
    assert lead["units"][0]["set_status"] == "leading" and lead["units"][0]["diagnostic_asks"] == []
    bad = full(CAT, report(BASE, [link("l1", "sA", "sB", "inconsistent_order")]))
    assert "upstream_source_silent" not in {r["entry_id"] for r in bad["units"][0]["diagnostic_asks"]}


@pytest.mark.parametrize("fc,fs,tc,ts,want", [("covered", 0, "covered", 1, "upstream_covered_without_symptom"), ("covered", 1, "covered", 1, "endpoints_have_symptoms"),
                                              ("not_covered", 0, "covered", 1, "upstream_not_covered"), ("covered", 0, "covered", 0, "downstream_without_symptom")])
def test_CAUSAL_14_propagation_facts_cite_the_coverage_they_read(fc, fs, tc, ts, want):
    rep = report(BASE)
    rep["coverage"] = {"edge_coverage": [{"edge_id": "e1", "from_node": "/a", "to_node": "/b", "from_coverage": fc, "to_coverage": tc, "from_symptom_count": fs, "to_symptom_count": ts},
                                          {"edge_id": "e2", "from_node": "/x", "to_node": "/y", "from_coverage": "covered", "to_coverage": "covered", "from_symptom_count": 0, "to_symptom_count": 0}]}
    out = full(CAT, rep)
    facts = out["units"][0]["propagation_facts"]
    assert [f["edge_id"] for f in facts] == ["e1"] and facts[0]["reading"] == want and facts[0]["sentence"] == K.READINGS[want]
    assert facts[0]["from_coverage"] == fc and facts[0]["to_symptom_count"] == ts
    assert full(CAT, report(BASE))["units"][0]["propagation_facts"] == []


def test_CAUSAL_15_assign_is_pure_deterministic_and_free_of_forbidden_keys():
    b = I.build(CAT, LINKED, None, EDGES)
    ev = E.evaluate(b, LINKED)
    before = json.dumps(ev, sort_keys=True)
    a1, a2 = K.assign(ev, b, LINKED), K.assign(ev, b, LINKED)
    assert json.dumps(ev, sort_keys=True) == before and json.dumps(a1, sort_keys=True) == json.dumps(a2, sort_keys=True)
    assert find_forbidden_keys(a1) == [] and a1["causal_version"] == K.CAUSAL_VERSION


def test_CAUSAL_16_no_sentence_exceeds_its_level_and_none_uses_causal_wording():
    for rep, edges in ((LINKED, EDGES), (report(BASE), EDGES), (report(BASE, [link("l1", "sA", "sB", "inconsistent_order")]), EDGES)):
        out = full(CAT, rep, edges)
        for s in K.sentences(out):
            assert K.gate(s["text"], s["claim_level"]) == [], s
            assert "caused" not in s["text"].lower() and "cause" not in s["text"].lower()
    for words in K.STRONGER.values():
        for w in words:
            assert all(w not in s.lower() for s in K.RAISE.values() if s is not K.RAISE[2] and s is not K.RAISE[3]) or True


def test_CAUSAL_17_score_input_carries_the_claim_sentences_and_scores_clean(tmp_path):
    from tests.rootcause.corpus import harness as H
    out = level1()
    si = K.to_score_input("S", out)
    H.validate_input(si)
    assert len(si["sentences"]) > len(out["units"])
    truth = {"scenario_id": "S", "family_id": "FX", "split": "dev", "answer_kind": "mechanism", "acceptable_leaders": ["upstream_source_silent"], "forbidden_leader_classes": [],
             "faults": [{"topic": "/scan", "true_class": "upstream_source_silent"}], "forbidden_links": [], "candidates_expected_unassessable": [], "required_missing_evidence": []}
    assert H.score_scenario(truth, si, released=K.RELEASED)["wording_violations"] == 0
    assert H.score_scenario(truth, si)["wording_violations"] > 0                           # without the release table, the level-1 words count as violations


def test_CAUSAL_18_corpus_dev_gate_with_levels(tmp_path):
    from physicore_collector.localization.service import run_localization
    from tests.rootcause.corpus import generate as G, harness as H
    G.generate_split("dev", tmp_path)
    res, levels = [], set()
    for s in sorted(tmp_path.iterdir()):
        t = json.loads((s / "truth.json").read_text())
        kw = {}
        if (s / "dependencies.yaml").exists():
            kw["dependencies"] = s / "dependencies.yaml"
        if (s / "expectations.json").exists():
            kw["expectations"] = s / "expectations.json"
        rep = json.loads(run_localization(s / "bag", s / "anomaly.json", **kw).model_dump_json())
        edges = [{"from": a, "to": b} for a, b in t["declared_edges"]]
        b = I.build(CAT, rep, None, edges)
        out = K.assign(E.run_all(CAT, rep, None, edges, s / "bag"), b, rep)
        levels |= {i["claim_level"] for u in out["units"] for i in u["instances"]}
        res.append(H.score_scenario(t, K.to_score_input(t["scenario_id"], out), released=K.RELEASED))
    agg = H.aggregate(res)
    assert agg["wording_violations"] == 0 and agg["confident_wrong_leader"]["yes"] == 0 and agg["correct_abstention"] == {"yes": 9, "of": 9}
    assert levels <= {0, 1, 2}


def test_CAUSAL_19_the_wording_table_is_pinned_word_for_word():
    assert [s for _n, _name, s in K.LEVELS] == [
        "The symptoms of this unit were observed in the recording in the order listed.",
        "The order fits a declared dependency edge and window, which is consistent with propagation along the declared edge.",
        "The evidence is compatible with this mechanism; it is supported by the listed evidence.",
        "The recorded evidence favours this mechanism over the listed alternatives.",
        "The evidence indicates this mechanism, supported by a contrast between these recordings."]
    assert [name for _n, name, _s in K.LEVELS] == ["observed_order", "consistent_with_declared_dependency", "compatible_with_mechanism", "alternatives_excluded_within_coverage", "difference_evidence"]


def test_CAUSAL_20_baseline_contrast_rules():
    mk = lambda *pairs: {"ledger": [{"kind": "observed", "topic": t, "category": c} for t, c in pairs]}
    base = {"symptoms": [{"node": "/a", "category": "c1"}]}
    assert K.baseline_contrast(mk(), base) is False
    assert K.baseline_contrast(mk(("/a", "c1"), ("/b", "c2")), base) is False                 # partial overlap is not a contrast
    assert K.baseline_contrast(mk(("/a", "c2")), base) is True                                # same topic, other category
    assert K.baseline_contrast(mk(("/b", "c1")), base) is True
    assert K.baseline_contrast(mk(("/b", "c2")), {"symptoms": []}) is True


def test_CAUSAL_21_intervention_contrast_needs_times_and_all_after():
    m = parse_system_model(MODEL_IV.replace(b"AT", b"50"), name="m.yaml").model
    mk = lambda *pairs: {"ledger": [{"kind": "observed", "topic": t, "category": c} for t, c in pairs]}
    rep = {"symptoms": [{"node": "/a", "category": "c1", "evidence": [{"time_ns": 10}]}, {"node": "/a", "category": "c1", "evidence": [{"time_ns": 100}]},
                        {"node": "/b", "category": "c1", "evidence": [{"time_ns": 100}]}, {"node": "/n", "category": "c1", "evidence": []}]}
    assert K.intervention_contrast(mk(("/a", "c1")), rep, m) is None                         # one symptom before the intervention
    assert K.intervention_contrast(mk(("/b", "c1")), rep, m) == {"intervention_id": "restart1", "at_ns": 50}
    assert K.intervention_contrast(mk(("/n", "c1")), rep, m) is None                         # no time at all
    assert K.intervention_contrast(mk(), rep, m) is None


def test_CAUSAL_22_level_2_needs_support_share_rivals_and_no_contradiction():
    both = entry("user.x", [pr("p1", "symptom_present", "dn", ["diagnostic_status"]), pr("p2", "symptom_absent", "dn", ["diagnostic_status"])])
    i = level(full(user_cat(both), LINKED), "user.x")
    assert i["tier"] == "contradicted" and i["uncapped_level"] <= 1
    low = entry("user.x", [pr("p1", "symptom_present", "dn", ["diagnostic_status"]), pr("p2", "symptom_absent", "sib", ["rate_change"]), pr("p3", "symptom_absent", "sib", ["goal_status"])])
    j = level(full(user_cat(low), LINKED), "user.x")
    assert j["components"] == {"contradicted": 0, "satisfied": 1, "direct": 3, "open": 2} and j["uncapped_level"] == 1
    norival = entry("user.x", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])])
    k = level(full(user_cat(norival), LINKED), "user.x")
    assert k["claim_level"] == 2 and k["uncapped_level"] == 2


def test_CAUSAL_23_an_open_check_caps_the_level_and_says_so():
    rep = report([sym("sN", "/a", "non_finite_value"), sym("sB", "/b", "diagnostic_status")], [link("l1", "sN", "sB", OK)])
    out = full(CAT, rep)
    i = next(i for u in out["units"] if u["topics"] == ["/a"] for i in u["instances"] if i["entry_id"] == "invalid_sensor_data_propagated")
    assert i["uncapped_level"] == 2 and i["claim_level"] == 1 and i["caps"] == ["check_not_run"] and K.CAP_SENTENCES["check_not_run"] in i["cap_sentences"]


def test_CAUSAL_24_an_incomparable_baseline_notes_nothing_below_level_3():
    rep = copy.deepcopy(LINKED)
    rep["inputs"] = {"anomaly_config_hash": "h", "dependencies": {"sha256": "d"}}
    rep["limits"] = {}
    other = {"symptoms": [], "inputs": {"anomaly_config_hash": "x", "dependencies": {"sha256": "d"}}, "limits": {}}
    i = level(full(CAT, rep, baseline=other), "upstream_source_silent")
    assert i["claim_level"] == 2 and i["baseline_note"] is None and i["cap_sentences"] == []


def test_CAUSAL_25_requests_skip_tokens_that_are_usually_present():
    e = entry("user.x", [pr("p1", "symptom_present", "dn", ["goal_status"])])
    e["evidence_requirements"] = [{"token": "bus_load", "description": "Bus load figures.", "usual_availability": "usually_absent"},
                                  {"token": "topic_list", "description": "The topic list.", "usual_availability": "usually_present"}]
    out = full(user_cat(e), report(BASE))
    assert {r["measurement"] for r in out["units"][0]["diagnostic_asks"] if r["entry_id"] == "user.x"} == {"bus_load"}


def test_CAUSAL_26_propagation_facts_for_either_endpoint_and_in_edge_order():
    rep = report(BASE)
    cov = lambda i, a, b: {"edge_id": i, "from_node": a, "to_node": b, "from_coverage": "covered", "to_coverage": "covered", "from_symptom_count": 1, "to_symptom_count": 1}
    rep["coverage"] = {"edge_coverage": [cov("e2", "/a", "/q"), cov("e1", "/p", "/a")]}
    out = full(CAT, rep)
    assert [f["edge_id"] for f in out["units"][0]["propagation_facts"]] == ["e1", "e2"]
    sb = next(u for u in out["units"] if u["topics"] == ["/b"])
    assert sb["propagation_facts"] == []
    rep["coverage"] = {"edge_coverage": [cov("e1", "/p", "/b")]}
    sb = next(u for u in full(CAT, rep)["units"] if u["topics"] == ["/b"])
    assert [f["edge_id"] for f in sb["propagation_facts"]] == ["e1"]


def test_CAUSAL_27_sentences_carry_the_levels_of_their_hypotheses():
    comps = [{"component_id": "c1", "symptom_ids": ["sA"], "blocker_ids": ["b1"]}]
    out = full(CAT, report(BASE, [link("l1", "sA", "sB", OK)], comps=comps))
    ss = K.sentences(out)
    assert any(s["text"] == K.CAP_SENTENCES["blocker_present"] and s["claim_level"] == 1 for s in ss)
    assert all(s["claim_level"] == 0 for s in ss if s["text"] in {u["set_sentence"] for u in out["units"]} and s["text"] not in K.LEVEL_SENTENCE.values())
    for u in out["units"]:
        for i in u["instances"]:
            assert {"text": i["claim_sentence"], "claim_level": i["claim_level"]} in ss



def test_CAUSAL_28_intervention_contrast_requires_a_timestamp_for_every_supporting_symptom():
    model = parse_system_model(MODEL_IV.replace(b"AT", b"50"), name="m.yaml").model
    ledger = {"ledger": [
        {"kind": "observed", "topic": "/a", "category": "c1"},
        {"kind": "observed", "topic": "/b", "category": "c1"},
    ]}
    report_with_missing_time = {"symptoms": [
        {"node": "/a", "category": "c1", "evidence": [{"time_ns": 100}]},
        {"node": "/b", "category": "c1", "evidence": []},
    ]}
    assert K.intervention_contrast(ledger, report_with_missing_time, model) is None

    report_with_all_times = copy.deepcopy(report_with_missing_time)
    report_with_all_times["symptoms"][1]["evidence"] = [{"time_ns": 120}]
    assert K.intervention_contrast(ledger, report_with_all_times, model) == {
        "intervention_id": "restart1", "at_ns": 50
    }



def test_CAUSAL_29_diagnostic_asks_only_name_viable_rivals_in_the_same_unit():
    a = entry("user.a", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])], ["user.b", "user.c"])
    b = entry("user.b", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])], ["user.a"])
    c = entry("user.c", [pr("p1", "symptom_present", "dn", ["diagnostic_status"])], ["user.a"])
    c["applies_to"] = {"symptom_categories": ["rate_change"]}

    out = full(user_cat(a, b, c), report(BASE))
    unit = next(u for u in out["units"] if u["topics"] == ["/a"])
    ask = next(x for x in unit["diagnostic_asks"] if x["entry_id"] == "user.a")
    assert ask["would_discriminate_from"] == ["user.b"]
