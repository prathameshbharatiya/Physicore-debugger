"""SPEC (M4-0): the scenario specification is frozen: its hash is pinned and its structure is checked."""
import hashlib
import json
from pathlib import Path

C = Path(__file__).with_name("corpus")
SPEC_PATH = C / "spec.json"
SPEC_SHA = "d0e95f9703ab52dcb6f46a9dcd0076c0f2ad8d704e1099f0f689655e6dda1d6d"
SPEC = json.loads(SPEC_PATH.read_text())
KEYS = {"family_id", "name", "injected_fault", "faults", "answer_kind", "acceptable_leaders", "forbidden_leader_classes", "forbidden_links", "candidates_expected_unassessable",
        "recording_contains", "recording_lacks", "required_missing_evidence", "expected_detectors", "needs_contract", "declared_edges", "seed_varies"}


def test_SPEC_01_the_specification_is_the_frozen_one():
    assert hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest() == SPEC_SHA


def test_SPEC_02_ten_families_with_unique_ids_and_the_required_keys():
    fams = SPEC["families"]
    assert [f["family_id"] for f in fams] == [f"F{i:02d}" for i in range(1, 11)]
    assert len({f["name"] for f in fams}) == 10
    for f in fams:
        assert set(f) == KEYS, f["family_id"]


def test_SPEC_03_classes_are_from_the_closed_list_and_answer_kinds_are_consistent():
    classes = set(SPEC["mechanism_classes"])
    assert len(classes) == 12
    for f in SPEC["families"]:
        assert f["answer_kind"] in SPEC["answer_kinds"]
        for x in f["faults"]:
            assert x["true_class"] in classes and x["topic"].startswith("/")
        for c in f["acceptable_leaders"] + f["forbidden_leader_classes"] + f["candidates_expected_unassessable"]:
            assert c in classes
        if f["answer_kind"] == "mechanism":
            assert f["faults"] and f["acceptable_leaders"] and {x["true_class"] for x in f["faults"]} <= set(f["acceptable_leaders"]) | {"command_not_followed"}
        if f["answer_kind"] == "no_failure":
            assert not f["faults"] and not f["expected_detectors"] and not f["acceptable_leaders"]
        if f["answer_kind"] == "unresolved":
            assert not f["acceptable_leaders"]


def test_SPEC_04_splits_are_disjoint_and_the_holdout_is_namespaced():
    d, h = SPEC["splits"]["dev"], SPEC["splits"]["holdout"]
    assert set(d["seeds"]).isdisjoint(h["seeds"]) and len(d["seeds"]) == 3 and len(h["seeds"]) == 2
    assert d["namespace"] == "" and set(h["namespace_by_seed"]) == {str(s) for s in h["seeds"]}
    assert all(v.startswith("/") for v in h["namespace_by_seed"].values())


def test_SPEC_05_every_family_that_needs_a_missing_measurement_names_it_and_hardware_families_abstain():
    byid = {f["family_id"]: f for f in SPEC["families"]}
    assert byid["F10"]["required_missing_evidence"] and byid["F10"]["candidates_expected_unassessable"] == ["hardware_degradation", "resource_exhaustion"]
    assert byid["F03"]["needs_contract"] is True and all(not f["needs_contract"] for k, f in byid.items() if k != "F03")
    assert byid["F06"]["forbidden_links"] == [["/lidar/scan", "/imu/data"]] and byid["F07"]["forbidden_links"] == [["/rosout", "/lidar/scan"]]

