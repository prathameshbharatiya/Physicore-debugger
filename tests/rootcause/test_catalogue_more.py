"""CAT+ / INS+ (M4b): second layer of tests that pin every refusal and every binding rule individually."""
import copy
import json

import pytest
import yaml

from physicore_collector.rootcause import catalogue as C, instantiate as I, review_pack as R
from physicore_collector.rootcause.catalogue_v1 import CATALOGUE_V1_YAML
from physicore_collector.rootcause.model import parse_system_model
from tests.rootcause.test_catalogue import load, raw, user_entry, user_file
from tests.rootcause.test_instantiate import MODEL

CAT = C.builtin()


def bl(d):
    return C.parse_catalogue(yaml.safe_dump(d).encode(), supplied_by="builtin")


def paths(r):
    return [p.path for p in r.problems]


def u(**kw):
    return load(user_file(user_entry(**kw)))


def test_CATX_01_text_rules():
    assert load({**user_file(user_entry()), "declared_by": ""}).entries is None
    assert u(mechanism="A​B.").entries is None
    assert u(mechanism="A\x07B.").entries is None
    assert any(p.endswith("mechanism") for p in paths(u(mechanism=" .")) ) is False       # a bare full stop with text is a sentence of sorts; only emptiness is refused
    assert u(mechanism="  ").entries is None


def test_CATX_02_list_and_file_limits():
    slots = [{"name": f"s{i}", "selects": "symptom_topics"} for i in range(31)]
    r = u(slots=slots, predictions=[{"id": "p1", "text": "A thing.", "rule": {"op": "not_evaluable"}}])
    assert r.entries is None and any("more than" in p.message for p in r.problems)
    many = [user_entry(id=f"user.e{i}") for i in range(C.LIMITS["entries"] + 1)]
    r = load(user_file(*many))
    assert r.entries is None and any("more than" in p.message for p in r.problems)
    pad = yaml.safe_dump(user_file(user_entry())) + "# " + "x" * C.LIMITS["file_bytes"] + "\n"
    r = C.parse_catalogue(pad.encode())
    assert r.entries is None and "larger" in r.problems[0].message


def test_CATX_03_duplicates_and_empties_are_refused_with_a_path():
    assert u(applies_to={"symptom_categories": ["rate_change", "rate_change"]}).entries is None
    assert u(slots=[]).entries is None
    assert u(slots=[], predictions=[{"id": "p1", "text": "A thing.", "rule": {"op": "not_evaluable"}}]).entries is None
    assert u(predictions=[]).entries is None
    p = user_entry()["predictions"][0]
    assert u(predictions=[p, copy.deepcopy(p)]).entries is None
    assert u(rivals=["configuration_mismatch", "configuration_mismatch"]).entries is None
    ck = {"id": "c1", "kind": "range", "slot": "late", "constraint_kind": "value_range", "description": "A check."}
    assert u(checks=[ck]).entries is not None
    assert u(checks=[ck, dict(ck)]).entries is None
    assert u(checks=[{**ck, "constraint_kind": "bogus"}]).entries is None


def test_CATX_04_rules_are_checked_individually():
    def rule(r):
        return u(predictions=[{"id": "p1", "text": "A thing.", "rule": r}])
    assert paths(rule({"op": "guess"})) == ["$.entries[0].predictions[0].rule"]
    assert rule({"op": "check_violated", "check": "zz"}).entries is None
    assert rule({"op": "symptom_present", "slot": "late", "categories": []}).entries is None
    assert rule({"op": "symptom_present", "slot": "late"}).entries is None
    assert rule({"op": "not_evaluable", "slot": "late"}).entries is None
    assert rule({"op": "symptom_absent", "slot": "late", "categories": ["rate_change"]}).entries is not None


def test_CATX_05_builtin_review_rules_are_checked_individually():
    d = raw()
    e = d["entries"][0]
    e["review"] = {"status": "reviewed", "note": "Checked.", "reviewed_on": "2026-11-10"}
    assert any("reviewed_by" in p for p in paths(bl(d)))
    e["review"] = {"status": "reviewed", "note": "Checked.", "reviewed_by": "A"}
    assert any("reviewed_on" in p for p in paths(bl(d)))
    e["review"] = {"status": "reviewed", "note": "Checked.", "reviewed_by": "A", "reviewed_on": "10-11-2026"}
    assert any("reviewed_on" in p for p in paths(bl(d)))
    e["review"] = {"status": "proposed", "note": "Pending.", "reviewed_on": "2026-11-10"}
    assert bl(d).entries is None
    del e["review"]
    assert any(p.endswith(".review") for p in paths(bl(d)))
    e2 = raw()["entries"][0]
    e2["id"] = "not_a_mechanism"
    d2 = raw()
    d2["entries"][0] = e2
    assert any(p.endswith(".id") for p in paths(bl(d2)))
    d3 = raw()
    d3["entries"].pop()
    assert any(p == "$.entries" for p in paths(bl(d3)))


def test_CATX_06_json_duplicates_and_digest_order():
    good = json.dumps(user_file(user_entry()))
    dup = good.replace('"version": 1', '"version": 1, "version": 2', 1)
    assert dup != good and C.parse_catalogue(dup.encode(), name="c.json").entries is None
    assert C.digest_of([{"b": 1, "a": 2}]) == C.digest_of([{"a": 2, "b": 1}])
    two = load(user_file(user_entry(id="user.b"), user_entry(id="user.a")))
    assert [e["id"] for e in two.entries] == ["user.a", "user.b"]


def test_CATX_07_empty_and_nul_inputs_are_refused():
    assert C.parse_catalogue(b"").problems and C.parse_catalogue(b"a: 1\x00").problems and C.parse_catalogue(b"~").problems


def test_INSX_01_components_and_duplicate_nodes_in_units():
    rep = {"components": [{"component_id": "c1", "symptom_ids": ["s2", "s1"]}],
           "symptoms": [{"symptom_id": "s1", "node": "/a", "category": "rate_change"}, {"symptom_id": "s2", "node": "/a", "category": "publishing_gap"},
                        {"symptom_id": "s3", "node": "/b", "category": "log_severity"}]}
    us = I.units_of(rep)
    assert [(x["unit_id"], x["unit_kind"], x["topics"], x["symptom_ids"]) for x in us] == [("c1", "component", ["/a"], ["s1", "s2"]), ("s3", "symptom", ["/b"], ["s3"])]
    assert us[0]["categories"] == ["publishing_gap", "rate_change"]


def _entry(selects):
    e = copy.deepcopy(CAT.by_id()["upstream_source_silent"])
    e["slots"] = [{"name": "x", "selects": selects}]
    return e


UNIT = {"unit_id": "u", "unit_kind": "symptom", "symptom_ids": ["s"], "topics": ["/scan"], "categories": ["publishing_gap"]}


def test_INSX_02_upstream_and_downstream_directions_and_exclusion():
    m = parse_system_model(MODEL, name="m.yaml").model
    assert I.instantiate(_entry("declared_upstream_topics"), UNIT, m)["bindings"]["x"] == ["/out"]
    assert I.instantiate(_entry("declared_downstream_topics"), UNIT, m)["bindings"]["x"] == ["/diag"]
    both = {**UNIT, "topics": ["/a", "/b"]}
    edges = [{"from": "/a", "to": "/b"}]
    assert I.instantiate(_entry("declared_upstream_topics"), both, None, edges)["bindings"]["x"] == []
    assert I.instantiate(_entry("declared_downstream_topics"), both, None, edges)["bindings"]["x"] == []
    assert I.instantiate(_entry("declared_downstream_topics"), {**UNIT, "topics": ["/a"]}, None, edges)["bindings"]["x"] == ["/b"]
    assert I.instantiate(_entry("declared_upstream_topics"), {**UNIT, "topics": ["/b"]}, None, edges)["bindings"]["x"] == ["/a"]


def test_INSX_03_role_slots_follow_the_unit_components():
    m = parse_system_model(MODEL, name="m.yaml").model
    assert I.instantiate(_entry("binding_role:status"), UNIT, m)["bindings"]["x"] == []              # status is on another component
    assert I.instantiate(_entry("binding_role:extra"), UNIT, m)["bindings"]["x"] == ["/scan2"]
    assert I.instantiate(_entry("binding_role:extra"), UNIT, None)["binding_sources"]["x"] == "not_bound"


def test_INSX_04_fixed_texts_totals_and_digests():
    assert I.BINDING_MISSING == "not runnable: binding missing"
    solo = C.Catalogue((C.parse_catalogue(yaml.safe_dump(user_file(user_entry())).encode()).entries[0],), "d", ())
    rep = {"components": [], "symptoms": [{"symptom_id": "s1", "node": "/a", "category": "log_severity"}]}
    b = I.build(solo, rep)
    assert [m["member"] for m in b["units"][0]["always_present"]][-1] == "no_applicable_mechanism" and b["instance_total"] == 0
    rep2 = {"components": [], "symptoms": [{"symptom_id": "s1", "node": "/a", "category": "rate_change"}]}
    b2 = I.build(solo, rep2)
    assert b2["instance_total"] == 1 and "no_applicable_mechanism" not in [m["member"] for m in b2["units"][0]["always_present"]]
    m = parse_system_model(MODEL, name="m.yaml").model
    assert I.build(solo, rep2, m)["model_digest"] == m.digest


def test_PACKX_01_proposed_count_excludes_user_entries():
    u_ = C.parse_catalogue(yaml.safe_dump(user_file(user_entry())).encode())
    assert "13 entries; 12 proposed, 0 reviewed" in R.render(C.merged(u_))
