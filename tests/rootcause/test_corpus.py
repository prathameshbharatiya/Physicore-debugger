"""CORP (M4-0): the corpus generator is deterministic, honours the frozen spec, keeps the hold-out sealed, and every scenario really shows what the spec says."""
import json
import tempfile
from pathlib import Path

import pytest

from physicore_collector.localization.service import run_localization
from tests.rootcause.corpus import generate as G

DEV_DIGEST = "f552f7ecd28b56e8f7124b1f18b1339e15429491f112c992feff04a1be2aca2e"
HOLDOUT_DIGEST = "82fff084652edd6d66b28d5ec21cfb60bbcd1da6e7649a83f4aac88e41b5f61c"


@pytest.fixture(scope="module")
def dev(tmp_path_factory):
    d = tmp_path_factory.mktemp("dev")
    G.generate_split("dev", d)
    return d


def test_CORP_01_the_development_corpus_reproduces_byte_for_byte(dev, tmp_path):
    assert len(list(dev.iterdir())) == 30
    G.generate_split("dev", tmp_path / "again")
    assert G.corpus_digest(tmp_path / "again") == G.corpus_digest(dev) == DEV_DIGEST


def test_CORP_02_the_holdout_split_is_refused_unless_asked_for_explicitly(tmp_path):
    with pytest.raises(PermissionError):
        G.scenario_ids("holdout")
    with pytest.raises(PermissionError):
        G.generate("F01", 101, tmp_path)
    with pytest.raises(PermissionError):
        G.generate_split("holdout", tmp_path)
    assert not list(tmp_path.iterdir())


def test_CORP_03_the_holdout_reproduces_and_is_pinned_by_hash_only(tmp_path):
    G.generate_split("holdout", tmp_path / "a", allow_holdout=True)
    G.generate_split("holdout", tmp_path / "b", allow_holdout=True)
    assert len(list((tmp_path / "a").iterdir())) == 20
    assert G.corpus_digest(tmp_path / "a") == G.corpus_digest(tmp_path / "b") == HOLDOUT_DIGEST


def test_CORP_04_truth_records_match_the_spec_and_the_recording_topics(dev):
    for d in sorted(dev.iterdir()):
        t = json.loads((d / "truth.json").read_text())
        fam = G.family(t["family_id"])
        assert t["spec_sha256"] == G.SPEC_SHA and t["split"] == "dev" and t["namespace"] == ""
        assert t["answer_kind"] == fam["answer_kind"] and t["expected_detectors"] == fam["expected_detectors"]
        assert [f["true_class"] for f in t["faults"]] == [f["true_class"] for f in fam["faults"]]
        assert [f["topic"] for f in t["faults"]] == [f["topic"] for f in fam["faults"]]
        assert (d / "dependencies.yaml").exists() == bool(fam["declared_edges"])
        assert (d / "expectations.json").exists() == fam["needs_contract"]
        assert [e for e in t["declared_edges"]] == [list(e) for e in fam["declared_edges"]]


def test_CORP_05_the_holdout_topics_carry_their_namespace(tmp_path):
    G.generate_split("holdout", tmp_path, allow_holdout=True)
    for d in sorted(tmp_path.iterdir()):
        t = json.loads((d / "truth.json").read_text())
        assert t["namespace"] in ("/ns1", "/ns2") and t["split"] == "holdout"
        assert all(f["topic"].startswith(t["namespace"] + "/") for f in t["faults"])
        assert all(a.startswith(t["namespace"]) and b.startswith(t["namespace"]) for a, b in t["declared_edges"] + t["forbidden_links"])


def _symptom_detectors(d):
    kw = {}
    if (d / "dependencies.yaml").exists():
        kw["dependencies"] = d / "dependencies.yaml"
    if (d / "expectations.json").exists():
        kw["expectations"] = d / "expectations.json"
    rep = json.loads(run_localization(d / "bag", d / "anomaly.json", **kw).model_dump_json())
    return rep, {s["detector_or_rule"] for s in rep["symptoms"]}


def test_CORP_06_every_development_scenario_shows_exactly_the_symptoms_the_spec_says(dev):
    for d in sorted(dev.iterdir()):
        t = json.loads((d / "truth.json").read_text())
        rep, dets = _symptom_detectors(d)
        assert dets == set(t["expected_detectors"]), (t["scenario_id"], dets)
        if t["answer_kind"] == "no_failure":
            assert not rep["symptoms"] and not rep["chains"]


def test_CORP_07_the_symptom_topics_include_every_fault_topic(dev):
    for d in sorted(dev.iterdir()):
        t = json.loads((d / "truth.json").read_text())
        rep, _ = _symptom_detectors(d)
        topics = {s["topic"] for s in rep["symptoms"]}
        for f in t["faults"]:
            assert f["topic"] in topics or t["family_id"] == "F03", (t["scenario_id"], f["topic"])


def test_CORP_08_different_seeds_differ_and_the_same_seed_repeats(tmp_path):
    a = G.generate("F01", 1, tmp_path / "a")
    b = G.generate("F01", 2, tmp_path / "b")
    c = G.generate("F01", 1, tmp_path / "c")
    assert G.file_hashes(a)["bag/bag_0.mcap"] != G.file_hashes(b)["bag/bag_0.mcap"]
    assert G.file_hashes(a) == G.file_hashes(c)


def test_CORP_09_unknown_families_and_seeds_are_errors(tmp_path):
    with pytest.raises(G.ScenarioError):
        G.generate("F99", 1, tmp_path)
    with pytest.raises(G.ScenarioError):
        G.generate("F01", 7, tmp_path)
    with pytest.raises(G.ScenarioError):
        G.scenario_ids("test")


def test_CORP_10_the_files_list_matches_the_bytes(dev):
    for d in sorted(dev.iterdir()):
        listed = dict(reversed(l.split("  ", 1)) for l in (d / "FILES.sha256").read_text().splitlines())
        assert listed == G.file_hashes(d, skip={"FILES.sha256"})


def test_CORP_11_every_scenario_stays_inside_its_size_budget(dev):
    for d in sorted(dev.iterdir()):
        assert sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) < 400_000, d.name
