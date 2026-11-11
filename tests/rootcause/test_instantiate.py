"""INS (M4b): units, matching, bindings and competing sets are deterministic, honest about missing bindings, and cover the truth of every dev scenario."""
import copy
import json
import random
import re

import pytest

from physicore_collector.localization.service import run_localization
from physicore_collector.localization.report import find_forbidden_keys
from physicore_collector.localization.vocabulary import find_forbidden
from physicore_collector.rootcause import catalogue as C, instantiate as I
from physicore_collector.rootcause.model import parse_system_model
from tests.rootcause.corpus import generate as G, harness as H


@pytest.fixture(scope="module")
def dev(tmp_path_factory):
    d = tmp_path_factory.mktemp("insdev")
    G.generate_split("dev", d)
    out = {}
    for s in sorted(d.iterdir()):
        t = json.loads((s / "truth.json").read_text())
        kw = {}
        if (s / "dependencies.yaml").exists():
            kw["dependencies"] = s / "dependencies.yaml"
        if (s / "expectations.json").exists():
            kw["expectations"] = s / "expectations.json"
        rep = json.loads(run_localization(s / "bag", s / "anomaly.json", **kw).model_dump_json())
        out[t["scenario_id"]] = (t, rep)
    return out


CAT = C.builtin()


def edges_of(t):
    return [{"from": a, "to": b} for a, b in t["declared_edges"]]


def sets_of(b):
    return [{"set_id": u["unit_id"], "topics": u["covered_topics"],
             "hypotheses": [{"class": i["entry_id"], "tier": "possible", "status": "assessed", "source_topic": None} for i in u["instances"]]} for u in b["units"]]


def test_INS_01_the_true_mechanism_is_in_a_set_that_covers_its_topic_for_every_dev_scenario(dev):
    n = 0
    for sid, (t, rep) in dev.items():
        b = I.build(CAT, rep, dependency_edges=edges_of(t))
        sc = H.score_scenario(t, H.score_input_for(t, sets=sets_of(b)))
        if t["answer_kind"] == "mechanism":
            assert sc["truth_in_set"] is True, sid
            n += 1
        assert sc["forbidden_link_violations"] == 0, sid
    assert n == 21


def test_INS_02_the_true_entry_names_the_evidence_the_family_lacks(dev):
    by = CAT.by_id()
    for sid, (t, rep) in dev.items():
        req = set(t["required_missing_evidence"])
        if t["answer_kind"] == "mechanism":
            have = set().union(*({r["token"] for r in by[f["true_class"]]["evidence_requirements"]} for f in t["faults"]))
            assert req <= have, (sid, req - have)
        elif req:
            have = {r["token"] for e in CAT.entries for r in e["evidence_requirements"]}
            assert req <= have, (sid, req - have)


def test_INS_03_a_recording_without_symptoms_has_no_units(dev):
    for sid, (t, rep) in dev.items():
        if t["answer_kind"] == "no_failure":
            b = I.build(CAT, rep)
            assert b["units"] == [] and b["instance_total"] == 0


def test_INS_04_every_unit_always_holds_the_fixed_members_and_unresolved(dev):
    for _sid, (t, rep) in dev.items():
        for u in I.build(CAT, rep, dependency_edges=edges_of(t))["units"]:
            names = [m["member"] for m in u["always_present"]]
            assert names[:3] == [m["member"] for m in I.FIXED_MEMBERS] + ["unresolved"]
            assert ("no_applicable_mechanism" in names) == (not u["instances"])
            for m in u["always_present"]:
                assert find_forbidden(m["sentence"]) == [], m


def test_INS_05_units_are_components_plus_symptoms_outside_components(dev):
    t, rep = dev["F01-s1"] if "F01-s1" in dev else next(v for k, v in dev.items() if v[0]["family_id"] == "F01" and v[0]["seed"] == 1)
    us = I.units_of(rep)
    ids = [u["unit_id"] for u in us]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert sum(len(u["symptom_ids"]) for u in us) == len(rep["symptoms"])
    assert {u["unit_kind"] for u in us} <= {"component", "symptom"}


def test_INS_06_the_result_does_not_depend_on_input_order(dev):
    t, rep = next(v for v in dev.values() if v[0]["family_id"] == "F07" and v[0]["seed"] == 1)
    a = I.build(CAT, rep, dependency_edges=edges_of(t))
    rng = random.Random(5)
    for _ in range(4):
        r2 = copy.deepcopy(rep)
        for k in ("symptoms", "components"):
            rng.shuffle(r2[k])
        for c in r2["components"]:
            rng.shuffle(c["symptom_ids"])
        cat2 = C.Catalogue(tuple(rng.sample(list(CAT.entries), len(CAT.entries))), CAT.digest, CAT.sources)
        assert json.dumps(I.build(cat2, r2, dependency_edges=list(reversed(edges_of(t)))), sort_keys=True) == json.dumps(a, sort_keys=True)


def test_INS_07_a_check_without_its_binding_is_not_runnable(dev):
    t, rep = next(v for v in dev.values() if v[0]["family_id"] == "F03" and v[0]["seed"] == 1)
    for u in I.build(CAT, rep)["units"]:                            # no declared edge: the responder slot cannot be bound
        for i in u["instances"]:
            if i["entry_id"] == "command_not_followed":
                assert i["binding_sources"]["responders"] == "not_bound"
    b = I.build(CAT, rep, dependency_edges=edges_of(t))
    inst = [i for u in b["units"] for i in u["instances"] if i["entry_id"] == "command_not_followed"][0]
    assert inst["binding_sources"]["responders"] == "dependency_file" and inst["bindings"]["responders"] == ["/robot_state"]
    nodata = json.loads(json.dumps(CAT.by_id()["invalid_sensor_data_propagated"]))
    nodata["slots"] = [s for s in nodata["slots"] if s["name"] != nodata["checks"][0]["slot"]] + [{"name": nodata["checks"][0]["slot"], "selects": "binding_role:none"}]
    got = I.instantiate(nodata, {"unit_id": "u", "topics": ["/a"], "categories": ["non_finite_value"], "symptom_ids": ["s"]})
    assert got["checks"][0]["runnable"] is False and got["checks"][0]["state"] == I.BINDING_MISSING


MODEL = b"""
system_model_schema_version: 1
declared_by: "tester"
system_name: t
components:
  - {id: lidar, role: sensor}
  - {id: mon, role: middleware}
  - {id: drv, role: driver}
bindings:
  - {component: lidar, role: scan, topic: /scan, field: ranges}
  - {component: lidar, role: extra, topic: /scan2}
  - {component: mon, role: status, topic: /diag}
  - {component: drv, role: out, topic: /out}
edges:
  - {id: e1, from: lidar, to: mon, relation: data_flow, window_ns: 1000000000}
  - {id: e2, from: drv, to: lidar, relation: data_flow, window_ns: 1000000000}
"""


def test_INS_08_system_model_binds_siblings_roles_and_declared_edges():
    m = parse_system_model(MODEL, name="m.yaml").model
    assert m is not None
    unit = {"unit_id": "u", "unit_kind": "symptom", "symptom_ids": ["s"], "topics": ["/scan"], "categories": ["publishing_gap"]}
    e = CAT.by_id()["upstream_source_silent"]
    got = I.instantiate(e, unit, m)
    assert got["bindings"]["siblings"] == ["/scan2"] and got["bindings"]["consumers"] == ["/diag"]
    assert got["binding_sources"] == {"consumers": "system_model", "silent_topics": "m3d_report", "siblings": "system_model"}


def test_INS_09_component_roles_restrict_matching():
    m = parse_system_model(MODEL, name="m.yaml").model
    base = {"unit_kind": "symptom", "symptom_ids": ["s"]}
    e = copy.deepcopy(CAT.by_id()["upstream_source_silent"])
    e["applies_to"]["component_roles"] = ["power"]
    assert not I.matches(e, {**base, "unit_id": "u", "topics": ["/scan"], "categories": ["publishing_gap"]}, m)
    e["applies_to"]["component_roles"] = ["sensor"]
    assert I.matches(e, {**base, "unit_id": "u", "topics": ["/scan"], "categories": ["publishing_gap"]}, m)
    assert not I.matches(e, {**base, "unit_id": "u", "topics": ["/scan"], "categories": ["publishing_gap"]}, None)
    e["applies_to"]["symptom_categories"] = ["rate_change"]
    assert not I.matches(e, {**base, "unit_id": "u", "topics": ["/scan"], "categories": ["publishing_gap"]}, m)


def test_INS_10_instances_carry_no_rank_score_or_cause_wording(dev):
    t, rep = next(v for v in dev.values() if v[0]["family_id"] == "F02" and v[0]["seed"] == 1)
    b = I.build(CAT, rep)
    assert find_forbidden_keys(b) == []
    for u in b["units"]:
        for i in u["instances"]:
            assert i["review_status"] == "proposed" and i["supplied_by"] == "builtin"
            for t_ in [i["mechanism"]] + [q["text"] for q in i["predictions"]]:
                assert find_forbidden(t_) == []
    assert b["catalogue_digest"] == CAT.digest and b["model_digest"] is None


def test_INS_11_instance_ids_change_with_bindings_and_version():
    unit = {"unit_id": "u", "unit_kind": "symptom", "symptom_ids": ["s"], "topics": ["/a"], "categories": ["publishing_gap"]}
    e = CAT.by_id()["upstream_source_silent"]
    a = I.instantiate(e, unit)["instance_id"]
    assert a == I.instantiate(e, unit)["instance_id"] and re.fullmatch(r"h-[0-9a-f]{16}", a)
    assert a != I.instantiate(e, {**unit, "topics": ["/b"]})["instance_id"]
    assert a != I.instantiate({**e, "version": 2}, unit)["instance_id"]
    assert a != I.instantiate(e, {**unit, "unit_id": "v"})["instance_id"]


def test_INS_12_user_entries_are_instantiated_and_marked():
    import yaml
    from tests.rootcause.test_catalogue import user_entry, user_file
    u = C.parse_catalogue(yaml.safe_dump(user_file(user_entry())).encode())
    m = C.merged(u)
    unit = {"unit_id": "u", "unit_kind": "symptom", "symptom_ids": ["s"], "topics": ["/a"], "categories": ["rate_change"]}
    got = [I.instantiate(e, unit) for e in m.entries if I.matches(e, unit, None) and e["id"].startswith("user.")]
    assert got and got[0]["supplied_by"] == "user_supplied" and got[0]["review_status"] == "user_supplied"
