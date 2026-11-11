"""HAR (M4-0): the evaluation harness scores results against the frozen truth, tells good from bad answers, and is deterministic."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.rootcause.corpus import generate as G
from tests.rootcause.corpus import harness as H

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def truths(tmp_path_factory):
    d = tmp_path_factory.mktemp("dev")
    G.generate_split("dev", d)
    return [json.loads((p / "truth.json").read_text()) for p in sorted(d.iterdir())]


def hyp(cls, tier="possible", status="assessed", src=None):
    return {"class": cls, "tier": tier, "status": status, "source_topic": src}


def oracle(t):
    """A result that is exactly right for this scenario, written from the truth record only."""
    sets, k = [], t["answer_kind"]
    if k == "mechanism":
        for f in t["faults"]:
            sets.append({"set_id": "set-" + f["fault_id"], "topics": [f["topic"]],
                         "hypotheses": [hyp(f["true_class"], "leading", src=f["topic"]), hyp("recording_or_observation_artifact", "possible"), hyp(H.UNRESOLVED, "possible", "unresolved")]})
    elif k == "unresolved":
        topics = [f["topic"] for f in t["faults"]]
        sets.append({"set_id": "set-1", "topics": topics, "hypotheses": [hyp(c, "not_assessed", "cannot_be_assessed") for c in t["candidates_expected_unassessable"]] + [hyp(H.UNRESOLVED, "possible", "unresolved")]})
    return H.score_input_for(t, sets=sets, named_missing_evidence=list(t["required_missing_evidence"]),
                             sentences=[{"text": "The evidence is consistent with this mechanism.", "claim_level": 2}])


def test_HAR_01_the_oracle_answer_scores_perfectly_on_every_development_scenario(truths):
    res = [H.score_scenario(t, oracle(t)) for t in truths]
    a = H.aggregate(res)
    assert a["scenarios"] == 30
    assert a["truth_in_set"] == {"yes": 21, "of": 21}
    assert a["confident_wrong_leader"] == {"yes": 0, "of": 30}
    assert a["correct_abstention"] == {"yes": 9, "of": 9}
    assert a["missing_evidence_named"] == {"yes": 27, "of": 27}
    assert a["forbidden_link_violations"] == 0 and a["wording_violations"] == 0


def test_HAR_02_a_wrong_leader_is_counted_everywhere_including_unresolved_and_healthy_scenarios(truths):
    for t in truths:
        wrong = H.score_input_for(t, sets=[{"set_id": "x", "topics": ["/any"], "hypotheses": [hyp("resource_exhaustion", "leading")]}])
        r = H.score_scenario(t, wrong)
        assert r["confident_wrong_leader"] is True, t["scenario_id"]


def test_HAR_03_answering_nothing_misses_the_truth_but_is_the_right_abstention_when_nothing_is_decidable(truths):
    res = {t["scenario_id"]: H.score_scenario(t, H.score_input_for(t)) for t in truths}
    for t in truths:
        r = res[t["scenario_id"]]
        assert not r["confident_wrong_leader"]
        if t["answer_kind"] == "mechanism":
            assert r["truth_in_set"] is False and r["faults_found"] == 0
        else:
            assert r["correct_abstention"] is True
        if t["required_missing_evidence"]:
            assert r["missing_evidence_named"] is False


def test_HAR_04_an_unobservable_mechanism_reported_as_supported_fails_the_abstention(truths):
    t = next(t for t in truths if t["family_id"] == "F10")
    bad = H.score_input_for(t, sets=[{"set_id": "s", "topics": [t["faults"][0]["topic"]], "hypotheses": [hyp("hardware_degradation", "supported")]}])
    assert H.score_scenario(t, bad)["correct_abstention"] is False
    ok = H.score_input_for(t, sets=[{"set_id": "s", "topics": [t["faults"][0]["topic"]], "hypotheses": [hyp("hardware_degradation", "not_assessed", "cannot_be_assessed")]}])
    assert H.score_scenario(t, ok)["correct_abstention"] is True


def test_HAR_05_a_healthy_recording_must_produce_no_sets_at_all(truths):
    t = next(t for t in truths if t["family_id"] == "F09")
    assert H.score_scenario(t, H.score_input_for(t))["correct_abstention"] is True
    some = H.score_input_for(t, sets=[{"set_id": "s", "topics": ["/lidar/scan"], "hypotheses": [hyp(H.UNRESOLVED, "possible", "unresolved")]}])
    assert H.score_scenario(t, some)["correct_abstention"] is False


def test_HAR_06_a_forbidden_link_is_counted_once_per_pair_and_set(truths):
    t = next(t for t in truths if t["family_id"] == "F06")
    a, b = t["forbidden_links"][0]
    both = H.score_input_for(t, sets=[{"set_id": "s", "topics": [a, b], "hypotheses": []}, {"set_id": "u", "topics": [a, b, "/z"], "hypotheses": []}])
    assert H.score_scenario(t, both)["forbidden_link_violations"] == 2
    apart = H.score_input_for(t, sets=[{"set_id": "s", "topics": [a], "hypotheses": []}, {"set_id": "u", "topics": [b], "hypotheses": []}])
    assert H.score_scenario(t, apart)["forbidden_link_violations"] == 0


def test_HAR_07_wording_forbidden_terms_are_released_only_by_the_claim_level_that_allows_them(truths):
    t = truths[0]
    s = lambda lvl: H.score_input_for(t, sentences=[{"text": "The origin is the lidar.", "claim_level": lvl}])  # noqa: E731
    assert H.score_scenario(t, s(0))["wording_violations"] == 1
    assert H.score_scenario(t, s(4), released={})["wording_violations"] == 1
    assert H.score_scenario(t, s(3), released={3: {"origin"}})["wording_violations"] == 0
    assert H.score_scenario(t, s(2), released={3: {"origin"}})["wording_violations"] == 1
    assert H.score_scenario(t, H.score_input_for(t, sentences=[{"text": "consistent_with_propagation", "claim_level": 0}]))["wording_violations"] == 0


def test_HAR_08_forbidden_leader_classes_and_acceptable_leaders_are_enforced():
    t = {"scenario_id": "X", "family_id": "FX", "split": "dev", "answer_kind": "mechanism", "faults": [{"fault_id": "a", "true_class": "upstream_source_silent", "topic": "/a"}],
         "acceptable_leaders": ["upstream_source_silent", "message_loss_in_transport"], "forbidden_leader_classes": ["message_loss_in_transport"], "forbidden_links": [],
         "candidates_expected_unassessable": [], "required_missing_evidence": []}
    mk = lambda c: H.score_input_for(t, sets=[{"set_id": "s", "topics": ["/a"], "hypotheses": [hyp("upstream_source_silent"), hyp(c, "leading")]}])  # noqa: E731
    assert H.score_scenario(t, mk("upstream_source_silent"))["confident_wrong_leader"] is False
    assert H.score_scenario(t, mk("message_loss_in_transport"))["confident_wrong_leader"] is True      # acceptable, but forbidden wins
    assert H.score_scenario(t, mk("configuration_mismatch"))["confident_wrong_leader"] is True         # not acceptable


def test_HAR_09_truth_must_sit_in_a_set_that_covers_the_fault_topic_and_every_fault_counts():
    t = {"scenario_id": "X", "family_id": "FX", "split": "dev", "answer_kind": "mechanism",
         "faults": [{"fault_id": "a", "true_class": "upstream_source_silent", "topic": "/a"}, {"fault_id": "b", "true_class": "invalid_sensor_data_propagated", "topic": "/b"}],
         "acceptable_leaders": ["upstream_source_silent", "invalid_sensor_data_propagated"], "forbidden_leader_classes": [], "forbidden_links": [],
         "candidates_expected_unassessable": [], "required_missing_evidence": []}
    one = H.score_input_for(t, sets=[{"set_id": "s", "topics": ["/a"], "hypotheses": [hyp("upstream_source_silent")]}])
    r = H.score_scenario(t, one)
    assert r["truth_in_set"] is False and r["faults_found"] == 1 and r["faults_total"] == 2
    elsewhere = H.score_input_for(t, sets=[{"set_id": "s", "topics": ["/zzz"], "hypotheses": [hyp("upstream_source_silent"), hyp("invalid_sensor_data_propagated")]}])
    assert H.score_scenario(t, elsewhere)["faults_found"] == 0
    both = H.score_input_for(t, sets=[{"set_id": "s", "topics": ["/a"], "hypotheses": [hyp("upstream_source_silent")]}, {"set_id": "u", "topics": ["/b"], "hypotheses": [hyp("invalid_sensor_data_propagated")]}])
    assert H.score_scenario(t, both)["truth_in_set"] is True


def test_HAR_10_missing_evidence_must_all_be_named(truths):
    t = next(t for t in truths if t["family_id"] == "F10")
    part = H.score_input_for(t, named_missing_evidence=t["required_missing_evidence"][:-1])
    assert H.score_scenario(t, part)["missing_evidence_named"] is False
    extra = H.score_input_for(t, named_missing_evidence=t["required_missing_evidence"] + ["whatever"])
    assert H.score_scenario(t, extra)["missing_evidence_named"] is True
    h = next(t for t in truths if t["family_id"] == "F09")
    assert H.score_scenario(h, H.score_input_for(h))["missing_evidence_named"] is None


def test_HAR_11_hostile_score_inputs_are_rejected_with_a_clear_error(truths):
    t = truths[0]
    ok = oracle(t)
    muts = []
    for mut in (lambda d: d.pop("sets"), lambda d: d.update(extra=1), lambda d: d.update(score_input_version=2), lambda d: d.update(scenario_id=""),
                lambda d: d.update(sets={}), lambda d: d["sets"][0].update(topics="x"), lambda d: d["sets"][0]["hypotheses"][0].update(tier="top"),
                lambda d: d["sets"][0]["hypotheses"][0].update(status="maybe"), lambda d: d["sets"][0]["hypotheses"][0].update({"class": "magic"}),
                lambda d: d["sets"][0]["hypotheses"][0].update(source_topic=5), lambda d: d["sets"][0]["hypotheses"][0].pop("tier"),
                lambda d: d["sets"][0]["hypotheses"][0].update(status="cannot_be_assessed"),   # a leading hypothesis cannot be unassessed
                lambda d: d["sets"][0]["hypotheses"].append({**d["sets"][0]["hypotheses"][0], "class": H.UNRESOLVED}),  # unresolved cannot lead
                lambda d: d["sentences"][0].update(claim_level=6), lambda d: d["sentences"][0].update(claim_level=True),
                lambda d: d["sentences"][0].update(text=3), lambda d: d.update(named_missing_evidence=[1]),
                lambda d: d["sets"].append(copy.deepcopy(d["sets"][0]))):
        d = copy.deepcopy(ok)
        mut(d)
        muts.append(d)
    for d in muts:
        with pytest.raises(H.ScoreInputError):
            H.score_scenario(t, d)
    with pytest.raises(H.ScoreInputError):
        H.score_scenario(t, "not a dict")


def test_HAR_12_a_score_input_for_another_scenario_is_refused(truths):
    with pytest.raises(H.ScoreInputError):
        H.score_scenario(truths[0], oracle(truths[1]))


def test_HAR_13_the_report_is_deterministic_and_independent_of_hash_seed(truths, tmp_path):
    res = [H.score_scenario(t, oracle(t)) for t in truths]
    assert H.report_json(res) == H.report_json(list(reversed(res)))
    code = ("import json,sys,tempfile;from pathlib import Path;from tests.rootcause.corpus import generate as G, harness as H;"
            "import tests.rootcause.test_harness as T;d=Path(tempfile.mkdtemp());G.generate_split('dev',d);"
            "ts=[json.loads((p/'truth.json').read_text()) for p in sorted(d.iterdir())];"
            "sys.stdout.write(H.report_json([H.score_scenario(t,T.oracle(t)) for t in ts]))")
    outs = []
    for seed in ("0", "123"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}", "LC_ALL": "C"}
        outs.append(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, check=True).stdout)
    assert outs[0] == outs[1] == H.report_json(res)


def test_HAR_14_the_report_states_that_the_corpus_is_synthetic(truths):
    out = H.report_json([H.score_scenario(truths[0], oracle(truths[0]))])
    assert "not diagnostic accuracy on real robots" in out


def test_HAR_15_aggregate_counts_exactly_what_the_results_say():
    base = {"family_id": "F01", "split": "dev", "answer_kind": "mechanism", "forbidden_link_violations": 2, "wording_violations": 3}
    rs = [{**base, "scenario_id": "a", "confident_wrong_leader": True, "truth_in_set": True, "correct_abstention": None, "missing_evidence_named": False},
          {**base, "scenario_id": "b", "confident_wrong_leader": False, "truth_in_set": False, "correct_abstention": None, "missing_evidence_named": None}]
    a = H.aggregate(rs)
    assert a["truth_in_set"] == {"yes": 1, "of": 2} and a["confident_wrong_leader"] == {"yes": 1, "of": 2}
    assert a["correct_abstention"] == {"yes": 0, "of": 0} and a["missing_evidence_named"] == {"yes": 0, "of": 1}
    assert a["forbidden_link_violations"] == 4 and a["wording_violations"] == 6
