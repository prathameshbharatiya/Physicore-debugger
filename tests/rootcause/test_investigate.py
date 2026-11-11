"""INV (M4e): the investigation run, its exit codes, its strict schema, and its refusals."""
import copy
import json
import shutil
from types import SimpleNamespace

import pytest
import yaml

from physicore_collector.rootcause import investigate as V
from tests.rootcause.hyp_fx import scenario
from tests.rootcause.test_catalogue import user_entry, user_file


@pytest.fixture(scope="module")
def sc(tmp_path_factory):
    t = tmp_path_factory.mktemp("m4e")
    return {k: scenario(t, k) for k in ("F03", "F02", "F09", "F08", "F01")} | {"tmp": t}


def run(s, fmt="json", **kw):
    return V.run_hypotheses(s["report"], fmt, **kw)


def test_INV_01_end_to_end_on_the_command_scenario(sc):
    s = sc["F03"]
    rep = V.investigate(s["report"], evidence_path=s["bag"], verify=True)
    assert V.validate_report(rep) == [] and rep["exit_code"] == 0
    u = rep["units"][0]
    assert u["set_status"] == "leading" and next(i for i in u["instances"] if i["instance_id"] == u["leader"])["entry_id"] == "command_not_followed"
    assert rep["verification"]["summary"]["mismatch"] == 0 and rep["verification"]["summary"]["verified"] >= 1
    assert V.to_json(rep) == V.to_json(V.investigate(s["report"], evidence_path=s["bag"], verify=True))
    assert rep["inputs"]["catalogue"]["entries_used_by_review_status"]["reviewed"] == 0


def test_INV_02_the_strict_schema_refuses_each_kind_of_damage(sc):
    rep = V.investigate(sc["F03"]["report"])
    assert V.validate_report(rep) == []
    def damaged(f):
        r = copy.deepcopy(rep)
        f(r)
        return V.validate_report(r)
    u = lambda r: r["units"][0]
    i0 = lambda r: u(r)["instances"][0]
    assert damaged(lambda r: r.update(extra=1))
    assert damaged(lambda r: r.pop("rule"))
    assert damaged(lambda r: r.update(investigation_report_version="2.0"))
    assert damaged(lambda r: r.update(exit_code=7))
    assert damaged(lambda r: r.update(exit_code=True))
    assert damaged(lambda r: u(r).update(set_status="certain"))
    assert damaged(lambda r: u(r).update(leader="h-nope"))
    assert damaged(lambda r: u(r).update(topics="not a list"))
    assert damaged(lambda r: i0(r).update(tier="probable"))
    assert damaged(lambda r: i0(r).update(claim_level=5))
    assert damaged(lambda r: i0(r).update(claim_level=True))
    assert damaged(lambda r: i0(r).update(claim_sentence="The cause is known."))
    assert damaged(lambda r: i0(r).update(claim_level=3, uncapped_level=2))
    assert damaged(lambda r: i0(r).update(extra=1))
    assert damaged(lambda r: i0(r)["ledger"].append({"kind": "guess", "stance": "supports"}))
    assert damaged(lambda r: r.update(score=1))
    assert damaged(lambda r: i0(r).update(would_raise="This is the cause."))
    assert damaged(lambda r: r.update(limitations="x"))
    assert damaged(lambda r: r.update(units="x"))
    assert V.validate_report([]) and V.validate_report("x")


def test_INV_03_a_contradicted_hypothesis_above_level_1_is_refused(sc):
    r = copy.deepcopy(V.investigate(sc["F03"]["report"]))
    i = r["units"][0]["instances"][0]
    i.update(tier="contradicted", uncapped_level=2, claim_level=2, claim_sentence=V.K.LEVEL_SENTENCE[2])
    r["units"][0]["leader"] = None
    assert any("level 0 or 1" in e for e in V.validate_report(r))
    r2 = copy.deepcopy(V.investigate(sc["F03"]["report"]))
    r2["units"][0]["instances"][1].update(tier="possible")
    r2["units"][0]["leader"] = r2["units"][0]["instances"][1]["instance_id"]
    assert any("leader must be supported" in e for e in V.validate_report(r2))


def test_INV_04_exit_codes_for_unreadable_and_invalid_inputs(sc, tmp_path):
    s = sc["F03"]
    assert V.run_hypotheses(tmp_path / "missing.json", "json")["exit_code"] == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert V.run_hypotheses(bad, "json")["exit_code"] == 3
    assert V.run_hypotheses(s["report"], "pdf")["exit_code"] == 2
    assert V.run_hypotheses(s["report"], "json", evidence_path=tmp_path / "nope")["exit_code"] == 2
    assert V.run_hypotheses(s["report"], "json", verify=True)["exit_code"] == 2
    badm = tmp_path / "m.yaml"
    badm.write_text("nonsense: 1\n")
    r = V.run_hypotheses(s["report"], "json", model_path=badm)
    assert r["exit_code"] == 3 and r["problems"] and r["text"] == ""
    badc = tmp_path / "c.yaml"
    badc.write_text("catalogue_schema_version: 9\n")
    assert V.run_hypotheses(s["report"], "json", catalogue_path=badc)["exit_code"] == 3
    rival = tmp_path / "c2.yaml"
    rival.write_text(yaml.safe_dump(user_file(user_entry(rivals=["user.missing"]))))
    assert V.run_hypotheses(s["report"], "json", catalogue_path=rival)["exit_code"] == 3
    assert V.run_hypotheses(s["report"], "json", baseline_path=bad)["exit_code"] == 3


def test_INV_05_the_dependency_file_must_belong_to_the_report(sc, tmp_path):
    f2, f3 = sc["F02"], sc["F03"]
    assert f2["deps"] is not None
    ok = V.run_hypotheses(f2["report"], "json", dependencies_path=f2["deps"])
    assert ok["exit_code"] == 0 and json.loads(ok["text"])["problems"] == []
    note = json.loads(V.run_hypotheses(f2["report"], "json")["text"])
    assert note["problems"] and "not supplied" in note["problems"][0]
    other = tmp_path / "other.yaml"
    other.write_text(f2["deps"].read_text() + "\n# changed\n")
    assert V.run_hypotheses(f2["report"], "json", dependencies_path=other)["exit_code"] == 3
    assert V.run_hypotheses(f3["report"], "json", dependencies_path=f2["deps"])["exit_code"] == 3        # the report was made without one
    broken = tmp_path / "broken.yaml"
    broken.write_text("x: [")
    assert V.run_hypotheses(f2["report"], "json", dependencies_path=broken)["exit_code"] == 3


def test_INV_06_a_recording_that_is_not_the_reports_gives_exit_1_and_a_note(sc):
    r = V.run_hypotheses(sc["F03"]["report"], "json", evidence_path=sc["F02"]["bag"], verify=True)
    assert r["exit_code"] == 1
    rep = json.loads(r["text"])
    assert rep["verification"]["summary"]["mismatch"] + rep["verification"]["summary"]["not_checked"] >= 1 and rep["exit_code"] == 1


def test_INV_07_a_baseline_is_compared_and_named(sc):
    rep = V.investigate(sc["F03"]["report"], baseline_path=sc["F09"]["report"])
    b = rep["inputs"]["baseline"]
    assert set(b) == {"comparable", "differs"} and isinstance(b["comparable"], bool)
    assert V.investigate(sc["F03"]["report"])["inputs"]["baseline"] is None


def test_INV_08_user_catalogue_entries_are_marked_and_counted(sc, tmp_path):
    c = tmp_path / "u.yaml"
    c.write_text(yaml.safe_dump(user_file(user_entry(applies_to={"symptom_categories": ["m2_event_response"]}))))
    rep = V.investigate(sc["F03"]["report"], catalogue_path=c)
    ids = {i["entry_id"]: i for u in rep["units"] for i in u["instances"]}
    assert ids["user.sag"]["review_status"] == "user_supplied" and ids["user.sag"]["supplied_by"] == "user_supplied"
    assert rep["inputs"]["catalogue"]["entries_used_by_review_status"]["user_supplied"] == 1
    assert [s["supplied_by"] for s in rep["inputs"]["catalogue"]["sources"]] == ["builtin", "user_supplied"]


def test_INV_09_a_system_model_is_reported_by_digest(sc, tmp_path):
    m = tmp_path / "m.yaml"
    m.write_text("system_model_schema_version: 1\ndeclared_by: t\nsystem_name: t\ncomponents:\n  - {id: c1, role: compute}\nbindings:\n  - {component: c1, role: o, topic: /cmd_mode}\n")
    rep = V.investigate(sc["F03"]["report"], model_path=m)
    assert rep["inputs"]["model"]["digest"] and len(rep["inputs"]["model"]["file_sha256"]) == 64


def test_INV_10_dependency_edges_expand_nodes_to_topics():
    E = lambda i, a, b: SimpleNamespace(id=i, src=a, dst=b)
    g = SimpleNamespace(bindings=[SimpleNamespace(node="n1", topic="/a"), SimpleNamespace(node="n1", topic="/b")], edges=[E("e1", "n1", "/c"), E("e2", "/d", "n2"), E("e3", "x", "y")])
    assert V.dependency_edges(g) == [{"from": "/a", "to": "/c"}, {"from": "/b", "to": "/c"}]


def test_INV_11_scenarios_without_symptoms_give_a_valid_empty_report(sc):
    rep = V.investigate(sc["F09"]["report"])
    assert rep["units"] == [] and rep["summary"]["units"] == 0 and V.validate_report(rep) == []


def test_INV_12_nothing_is_written_next_to_the_inputs(sc):
    s = sc["F03"]
    before = sorted(p.name for p in s["dir"].rglob("*"))
    V.investigate(s["report"], evidence_path=s["bag"], verify=True)
    assert before == sorted(p.name for p in s["dir"].rglob("*"))


def test_INV_13_every_scenario_of_the_dev_families_validates(sc):
    for k in ("F01", "F02", "F08"):
        rep = V.investigate(sc[k]["report"], dependencies_path=sc[k]["deps"], evidence_path=sc[k]["bag"])
        assert V.validate_report(rep) == [], k
