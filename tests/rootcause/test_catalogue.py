"""CAT (M4b): the catalogue loader is strict, the built-in catalogue is valid and neutral, and user entries cannot claim a review."""
import copy
import json
import re

import pytest
import yaml

from physicore_collector.localization.vocabulary import find_forbidden
from physicore_collector.rootcause import catalogue as C
from physicore_collector.rootcause.catalogue_v1 import CATALOGUE_V1_YAML
from tests.rootcause.corpus import generate as G


def raw():
    return yaml.safe_load(CATALOGUE_V1_YAML)


def user_entry(**kw):
    e = {"id": "user.sag", "version": 1, "family": "resource", "mechanism": "A shared bus was busy while a topic was late.",
         "applies_to": {"symptom_categories": ["rate_change"]}, "slots": [{"name": "late", "selects": "symptom_topics"}],
         "predictions": [{"id": "p1", "text": "The topic is late.", "rule": {"op": "symptom_present", "slot": "late", "categories": ["rate_change"]}}],
         "evidence_requirements": [{"token": "bus_load", "description": "Bus load figures.", "usual_availability": "usually_absent"}], "rivals": []}
    e.update(kw)
    return e


def load(d, **kw):
    return C.parse_catalogue(yaml.safe_dump(d).encode(), **kw)


def user_file(*entries):
    return {"catalogue_schema_version": 1, "declared_by": "tester", "entries": list(entries)}


def test_CAT_01_builtin_has_the_twelve_mechanism_entries_all_proposed():
    b = C.builtin()
    assert [e["id"] for e in b.entries] == sorted(C.MECHANISM_IDS_V1)
    assert set(C.MECHANISM_IDS_V1) == set(G.SPEC["mechanism_classes"])
    assert {e["review"]["status"] for e in b.entries} == {"proposed"}
    assert all(e["supplied_by"] == "builtin" and e["version"] == 1 for e in b.entries)


def test_CAT_02_builtin_is_deterministic():
    assert C.builtin().digest == C.builtin().digest == C.digest_of(C.builtin().entries)


def test_CAT_03_every_sentence_passes_the_forbidden_wording_screen():
    for e in C.builtin().entries:
        texts = [e["mechanism"]] + [q["text"] for q in e["predictions"]] + [k["description"] for k in e["checks"]] + [r["description"] for r in e["evidence_requirements"]]
        for t in texts:
            assert find_forbidden(t) == [], (e["id"], t)
            assert t.endswith(".")


def test_CAT_04_entry_rivals_exist_and_are_symmetric_in_the_catalogue():
    ids = {e["id"] for e in C.builtin().entries}
    for e in C.builtin().entries:
        assert set(e["rivals"]) <= ids and e["id"] not in e["rivals"]


def test_CAT_05_a_reviewed_builtin_entry_needs_a_reviewer_and_a_date():
    d = raw()
    d["entries"][0]["review"] = {"status": "reviewed", "note": "Checked."}
    assert C.parse_catalogue(yaml.safe_dump(d).encode(), supplied_by="builtin").entries is None
    d["entries"][0]["review"] = {"status": "reviewed", "note": "Checked.", "reviewed_by": "A. Person", "reviewed_on": "2026-11-10"}
    assert C.parse_catalogue(yaml.safe_dump(d).encode(), supplied_by="builtin").entries is not None
    d["entries"][0]["review"] = {"status": "proposed", "note": "Pending.", "reviewed_by": "A. Person"}
    assert C.parse_catalogue(yaml.safe_dump(d).encode(), supplied_by="builtin").entries is None


def test_CAT_06_a_user_entry_loads_marked_user_supplied_without_a_review():
    r = load(user_file(user_entry()))
    assert r.problems == [] and r.entries[0]["review"]["status"] == "user_supplied" and r.entries[0]["supplied_by"] == "user_supplied"


def test_CAT_07_a_user_entry_cannot_claim_a_review_or_use_a_builtin_id():
    assert load(user_file(user_entry(review={"status": "reviewed", "note": "x."}))).entries is None
    assert load(user_file(user_entry(id="configuration_mismatch"))).entries is None
    assert load(user_file(user_entry(id="user.Bad"))).entries is None


@pytest.mark.parametrize("mut", [
    lambda e: e.update(family="astrology"), lambda e: e.update(mechanism="The sensor crashed."), lambda e: e.update(mechanism="No full stop"),
    lambda e: e.update(version=0), lambda e: e.update(version=True), lambda e: e.update(extra=1),
    lambda e: e["applies_to"].update(symptom_categories=["nonsense"]), lambda e: e["applies_to"].update(symptom_categories=[]),
    lambda e: e["applies_to"].update(component_roles=["wizard"]),
    lambda e: e["slots"].append({"name": "late", "selects": "symptom_topics"}), lambda e: e["slots"][0].update(selects="anywhere"),
    lambda e: e["predictions"][0]["rule"].update(op="guess"), lambda e: e["predictions"][0]["rule"].update(slot="nope"),
    lambda e: e["predictions"][0].update(text="This is the root cause."), lambda e: e["predictions"][0]["rule"].update(extra=1),
    lambda e: e.update(checks=[{"id": "c1", "kind": "range", "slot": "nope", "constraint_kind": "value_range", "description": "A check."}]),
    lambda e: e.update(checks=[{"id": "c1", "kind": "sum", "slot": "late", "constraint_kind": "value_range", "description": "A check."}]),
    lambda e: e["evidence_requirements"][0].update(usual_availability="sometimes"), lambda e: e["evidence_requirements"].append(dict(e["evidence_requirements"][0])),
    lambda e: e.update(rivals=["user.sag"]), lambda e: e.update(rivals=["Not An Id"]), lambda e: e.pop("slots"), lambda e: e.update(mechanism="x" * 300 + "."),
])
def test_CAT_08_bad_user_entries_are_refused(mut):
    e = user_entry()
    mut(e)
    r = load(user_file(e))
    assert r.entries is None and r.problems


def test_CAT_09_file_level_refusals():
    ok = user_file(user_entry())
    assert load({**ok, "catalogue_schema_version": 2}).entries is None
    assert load({**ok, "entries": []}).entries is None
    assert load({**ok, "extra": 1}).entries is None
    assert load(user_file(user_entry(), user_entry())).entries is None                     # duplicate id
    d = copy.deepcopy(ok)
    del d["declared_by"]
    assert load(d).entries is None
    for bad in (b"", b"\xff\xfe", b"a\x00b", b"[1, 2]", b"entries: *x", b"x: !!python/object/apply:os.system ['ls']"):
        assert C.parse_catalogue(bad).entries is None
    assert C.parse_catalogue(b"x" * (C.LIMITS["file_bytes"] + 1)).entries is None


def test_CAT_10_json_files_load_and_duplicate_keys_are_refused():
    assert C.parse_catalogue(json.dumps(user_file(user_entry())).encode(), name="c.json").entries is not None
    assert C.parse_catalogue(b'{"a":1,"a":2}', name="c.json").entries is None
    assert C.parse_catalogue(b'{"a":NaN}', name="c.json").entries is None


def test_CAT_11_merged_catalogue_adds_user_entries_and_checks_rivals(tmp_path):
    u = load(user_file(user_entry(rivals=["configuration_mismatch"])))
    m = C.merged(u)
    assert len(m.entries) == 13 and m.digest != C.builtin().digest and [s["supplied_by"] for s in m.sources] == ["builtin", "user_supplied"]
    with pytest.raises(ValueError):
        C.merged(load(user_file(user_entry(rivals=["user.missing"]))))
    with pytest.raises(ValueError):
        C.merged(C.LoadedCatalogue(None, [], None))
    p = tmp_path / "u.yaml"
    p.write_text(yaml.safe_dump(user_file(user_entry())))
    assert C.load_catalogue_file(p).entries is not None
    assert C.load_catalogue_file(tmp_path / "missing.yaml").entries is None


def test_CAT_12_builtin_loaded_with_a_missing_entry_is_refused():
    d = raw()
    d["entries"].pop()
    assert C.parse_catalogue(yaml.safe_dump(d).encode(), supplied_by="builtin").entries is None


def test_CAT_13_checks_use_only_m2_kinds_and_every_check_slot_exists():
    for e in C.builtin().entries:
        names = {s["name"] for s in e["slots"]}
        for k in e["checks"]:
            assert k["kind"] in C.CHECK_KINDS and k["slot"] in names
        for q in e["predictions"]:
            if q["rule"]["op"] == "check_violated":
                assert q["rule"]["check"] in {k["id"] for k in e["checks"]}


def test_CAT_14_entry_ids_never_appear_inside_sentences():
    for e in C.builtin().entries:
        for t in [e["mechanism"]] + [q["text"] for q in e["predictions"]]:
            assert not re.search(r"[a-z]+_[a-z_]+", t)
