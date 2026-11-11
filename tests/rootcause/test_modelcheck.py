"""CHK (M4a): the comparison of the declared model with recording facts: each finding kind, ordering, wording, report shape."""
import copy
import json
import random
from types import SimpleNamespace as NS

import pytest

from physicore_collector.localization.vocabulary import find_forbidden
from physicore_collector.rootcause import model as M
from physicore_collector.rootcause import modelcheck as C
from tests.rootcause import mfx

W = {"min_log_time_ns": 1_700_000_000_000_000_000, "max_log_time_ns": 1_700_000_010_000_000_000}


def mdl(fn=None):
    d = mfx.valid() if fn is None else mfx.mutated(fn)
    r = M.parse_system_model(mfx.jbytes(d), name="m.json")
    assert r.model is not None, r.problems
    return r.model


def facts(topics=None, window=W):
    t = topics if topics is not None else {"/lidar/scan": ["sensor_msgs/msg/LaserScan"], "/cmd_mode": ["std_msgs/msg/String"], "/robot_state": ["std_msgs/msg/String"],
                                           "/diagnostics": ["diagnostic_msgs/msg/DiagnosticArray"]}
    return {"source": "from_recording", "inspection_state": "complete", "window_ns": window,
            "topics": [{"topic": k, "types": v, "message_count": 10} for k, v in sorted(t.items())]}


def kinds(fs):
    return [(f["kind"], f["subject"]) for f in fs]


def run(model, f=None, **kw):
    return C.check_model(model, f or facts(), dependency_sha=kw.pop("dep", mfx.H64), dependency_supplied=kw.pop("supplied", True), **kw)


def test_CHK_01_a_model_that_matches_the_recording_has_no_finding():
    assert run(mdl()) == []


def test_CHK_02_a_topic_the_model_uses_but_the_recording_lacks_is_reported_once_per_topic():
    f = facts({k: v for k, v in facts()["topics"] and {t["topic"]: t["types"] for t in facts()["topics"]}.items() if k != "/lidar/scan"})
    got = [x for x in run(mdl(), f) if x["kind"] == "declared_topic_not_recorded"]
    assert kinds(got) == [("declared_topic_not_recorded", "/lidar/scan")]
    assert got[0]["severity"] == "warning"


def test_CHK_03_edge_endpoints_that_are_topics_count_as_model_topics():
    m = mdl(lambda x: x["edges"].append({"id": "e3", "from": "/only_in_edge", "to": "lidar", "relation": "data_flow"}))
    assert ("declared_topic_not_recorded", "/only_in_edge") in kinds(run(m))


def test_CHK_04_a_recorded_topic_the_model_never_uses_is_a_note():
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    t["/rosout"] = ["rcl_interfaces/msg/Log"]
    got = run(mdl(), facts(t))
    assert kinds(got) == [("recorded_topic_not_declared", "/rosout")] and got[0]["severity"] == "note"


def test_CHK_05_type_mismatch_needs_both_a_declared_and_a_recorded_type():
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    t["/lidar/scan"] = ["sensor_msgs/msg/PointCloud2"]
    got = run(mdl(), facts(t))
    assert kinds(got) == [("type_mismatch", "/lidar/scan")]
    assert got[0]["detail"] == {"component": "lidar", "role": "scan", "declared": "sensor_msgs/msg/LaserScan", "recorded": ["sensor_msgs/msg/PointCloud2"]}
    t["/lidar/scan"] = []                                              # no recorded type: nothing to compare
    assert run(mdl(), facts(t)) == []
    t["/lidar/scan"] = ["a/msg/B", "sensor_msgs/msg/LaserScan"]        # one of several recorded types matches
    assert run(mdl(), facts(t)) == []
    t["/lidar/scan"] = ["sensor_msgs/msg/PointCloud2"]
    assert run(mdl(lambda x: x["bindings"][0].pop("type")), facts(t)) == []   # no declared type


def test_CHK_06_a_value_range_needs_the_topic_and_a_declared_field():
    m = mdl(lambda x: x["bindings"][0].pop("field"))
    got = [f for f in run(m) if f["kind"] == "constraint_cannot_be_evaluated"]
    assert [(f["subject"], f["detail"]) for f in got] == [("c_range", {"constraint_kind": "value_range", "reason": "field_not_declared"})]


def test_CHK_07_a_rate_needs_only_the_topic():
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    del t["/lidar/scan"]
    got = [f for f in run(mdl(lambda x: x["bindings"][0].pop("field")), facts(t)) if f["kind"] == "constraint_cannot_be_evaluated"]
    assert sorted((f["subject"], f["detail"]["reason"]) for f in got) == [("c_range", "field_not_declared"), ("c_range", "topic_not_recorded"), ("c_rate", "topic_not_recorded")]


def test_CHK_08_a_deadline_names_each_reason_once_across_trigger_and_response():
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    del t["/robot_state"]
    m = mdl(lambda x: x["bindings"][1].pop("field"))
    got = [f for f in run(m, facts(t)) if f["subject"] == "c_deadline"]
    assert sorted(f["detail"]["reason"] for f in got) == ["field_not_declared", "topic_not_recorded"]


def test_CHK_09_a_redundancy_group_needs_a_binding_for_every_member_and_one_recorded_topic():
    m = mdl(lambda x: (x["bindings"].pop(1), x["constraints"].pop(2)))   # nav loses its binding (and the deadline that used it)
    got = [f for f in run(m) if f["subject"] == "c_redundancy"]
    assert [f["detail"]["reason"] for f in got] == ["member_without_binding"]
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    del t["/lidar/scan"]
    assert [f for f in run(mdl(), facts(t)) if f["subject"] == "c_redundancy"] == []        # nav is still recorded
    del t["/cmd_mode"]
    got = [f for f in run(mdl(), facts(t)) if f["subject"] == "c_redundancy"]
    assert [f["detail"]["reason"] for f in got] == ["topic_not_recorded"]


def test_CHK_10_interventions_must_lie_inside_the_recording_window_inclusive():
    lo, hi = W["min_log_time_ns"], W["max_log_time_ns"]
    for at, outside in ((lo, False), (hi, False), (lo - 1, True), (hi + 1, True)):
        m = mdl(lambda x, at=at: x["interventions"][1].update(at_ns=at))
        got = [f for f in run(m) if f["kind"] == "intervention_outside_recording"]
        assert bool(got) is outside, at
    m = mdl(lambda x: x["interventions"][1].update(at_ns=lo - 1))
    assert [f for f in run(m, facts(window=None)) if f["kind"] == "intervention_outside_recording"] == []
    f = [f for f in run(m) if f["kind"] == "intervention_outside_recording"][0]
    assert f["subject"] == "i_restart" and f["detail"] == {"at_ns": lo - 1, "min_log_time_ns": lo, "max_log_time_ns": hi}


def test_CHK_11_the_dependency_file_digest_is_checked_only_when_the_model_names_one():
    assert C.check_dependency(mdl(), mfx.H64, True) == []
    assert kinds(C.check_dependency(mdl(), "0" * 64, True)) == [("dependency_file_digest_mismatch", mfx.H64)]
    assert C.check_dependency(mdl(), "0" * 64, True)[0]["detail"] == {"supplied": "0" * 64}
    assert kinds(C.check_dependency(mdl(), None, False)) == [("dependency_file_not_supplied", mfx.H64)]
    assert C.check_dependency(mdl(), None, False)[0]["severity"] == "note"
    assert C.check_dependency(mdl(lambda x: x.pop("dependency_file_sha256")), "0" * 64, True) == []
    assert kinds(run(mdl(), supplied=False, dep=None)) == [("dependency_file_not_supplied", mfx.H64)]


def test_CHK_12_findings_are_ordered_by_kind_then_subject_whatever_the_declaration_order():
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    del t["/lidar/scan"], t["/robot_state"]
    t["/b_extra"], t["/a_extra"] = [], []
    d = mfx.valid()
    base = run(M.parse_system_model(mfx.jbytes(d), name="m.json").model, facts(t), dep="0" * 64)
    rng = random.Random(3)
    for _ in range(6):
        e = copy.deepcopy(d)
        for k in ("components", "bindings", "constraints", "edges", "interventions", "versions"):
            rng.shuffle(e[k])
        assert run(M.parse_system_model(mfx.jbytes(e), name="m.json").model, facts(t), dep="0" * 64) == base
    order = [C.KIND_ORDER.index(f["kind"]) for f in base]
    assert order == sorted(order) and len(base) >= 6
    assert [f["subject"] for f in base if f["kind"] == "recorded_topic_not_declared"] == ["/a_extra", "/b_extra"]


def test_CHK_13_every_message_is_fixed_text_free_of_forbidden_words_and_states_no_cause():
    assert set(C.SENTENCES) == set(C.KIND_ORDER)
    for s in list(C.SENTENCES.values()) + list(C.LIMITATIONS):
        assert not find_forbidden(s), s
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    del t["/lidar/scan"]
    for f in run(mdl(), facts(t), dep="0" * 64, supplied=True):
        assert f["message"] == C.SENTENCES[f["kind"]]


def test_CHK_14_recording_facts_merge_channels_by_topic_and_mark_their_source():
    inv = NS(topics=[NS(topic="/a", schema_name="x/msg/A", message_count=3), NS(topic="/a", schema_name="x/msg/B", message_count=4),
                     NS(topic="/b", schema_name=None, message_count=1)],
             timebase=NS(recording_window=NS(min_log_time_ns=5, max_log_time_ns=9)), inspection=NS(state="complete"))
    f = C.recording_facts(inv)
    assert f == {"source": "from_recording", "inspection_state": "complete", "window_ns": {"min_log_time_ns": 5, "max_log_time_ns": 9},
                 "topics": [{"topic": "/a", "types": ["x/msg/A", "x/msg/B"], "message_count": 7}, {"topic": "/b", "types": [], "message_count": 1}]}
    inv.timebase.recording_window = None
    assert C.recording_facts(inv)["window_ns"] is None


def test_CHK_15_the_report_keeps_declared_and_recording_sections_apart():
    m = mdl()
    rep = C.build_report(m, facts(), run(m))
    assert rep["model"]["source"] == "declared" and rep["recording"]["source"] == "from_recording"
    assert rep["model"]["digest"] == m.digest and rep["model"]["file_sha256"] == m.file_sha256
    assert rep["model"]["counts"] == {"components": 4, "bindings": 4, "versions": 2, "constraints": 4, "interventions": 2, "edges": 2}
    assert rep["findings"] == [] and rep["findings_total"] == 0 and rep["findings_truncated"] is False
    none = C.build_report(m, None, None)
    assert none["recording"] == {"source": "not_supplied"} and none["findings"] is None and none["findings_total"] is None


def test_CHK_16_findings_are_capped_at_two_hundred_and_the_total_is_kept():
    t = {f"/extra{i}": [] for i in range(250)}
    m = mdl()
    fs = run(m, facts(t))
    rep = C.build_report(m, facts(t), fs)
    assert len(rep["findings"]) == 200 and rep["findings_total"] == len(fs) > 250 and rep["findings_truncated"] is True


def test_CHK_17_the_text_report_shows_every_section_and_no_forbidden_word():
    m = mdl()
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    del t["/lidar/scan"]
    rep = C.build_report(m, facts(t), run(m, facts(t)))
    text = C.render_text(rep)
    for needle in ("System model: test robot", "model digest", "Recording facts (from the recording", "Findings:", "declared_topic_not_recorded: /lidar/scan", "Limits:",
                   "indoor warehouse flat floor"):
        assert needle in text, needle
    assert not find_forbidden(text.replace("test robot", ""))
    assert "Recording: not supplied" in C.render_text(C.build_report(m, None, None))
    assert C.render_text(rep) == text


def test_CHK_18_the_json_report_is_sorted_stable_and_round_trips():
    m = mdl()
    rep = C.build_report(m, facts(), run(m))
    a, b = C.to_json(rep), C.to_json(copy.deepcopy(rep))
    assert a == b and json.loads(a) == rep and a.endswith("\n")


def test_CHK_19_a_real_recording_end_to_end(tmp_path):
    from tests.rootcause.corpus import generate as G
    bag = G.generate("F01", 1, tmp_path / "c") / "bag"
    p = mfx.write(tmp_path, mfx.mutated(lambda x: (x.pop("dependency_file_sha256"), x["bindings"][0].update(topic="/lidar/scan", type="std_msgs/msg/Float64"),
                                                    x["bindings"][3].update(topic="/diagnostics"), x["bindings"][1].update(topic="/not_in_bag"))))
    r = C.run_system_model(p, bag)
    assert r["exit_code"] == 0 and r["problems"] == []
    rep = r["report"]
    assert [t["topic"] for t in rep["recording"]["topics"]] == ["/diagnostics", "/lidar/scan"]
    got = {(f["kind"], f["subject"]) for f in rep["findings"]}
    assert ("declared_topic_not_recorded", "/not_in_bag") in got and ("declared_topic_not_recorded", "/robot_state") in got
    assert not any(k == "type_mismatch" for k, _ in got) and ("recorded_topic_not_declared", "/lidar/scan") not in got
    assert C.run_system_model(p, bag)["report"] == rep                                                    # deterministic


def test_CHK_20_without_a_recording_only_the_dependency_check_runs(tmp_path):
    p = mfx.write(tmp_path, mfx.valid())
    r = C.run_system_model(p)
    assert r["exit_code"] == 0 and r["report"]["recording"] == {"source": "not_supplied"}
    assert kinds(r["report"]["findings"]) == [("dependency_file_not_supplied", mfx.H64)]
    dep = tmp_path / "dep.yaml"
    dep.write_bytes(b"x")
    assert kinds(C.run_system_model(p, None, dep)["report"]["findings"]) == [("dependency_file_digest_mismatch", mfx.H64)]
    assert C.run_system_model(p, None, tmp_path / "nope.yaml")["exit_code"] == 2


def test_CHK_21_an_invalid_model_compares_nothing_and_exits_three(tmp_path):
    p = mfx.write(tmp_path, mfx.mutated(lambda x: x["components"][0].update(role="wizard")))
    r = C.run_system_model(p, tmp_path)
    assert r["exit_code"] == 3 and r["report"] is None and any("role" in l for l in r["problems"])


def test_CHK_22_an_unreadable_recording_exits_two(tmp_path):
    p = mfx.write(tmp_path, mfx.valid())
    (tmp_path / "junk.mcap").write_bytes(b"not an mcap")
    r = C.run_system_model(p, tmp_path / "junk.mcap")
    assert r["exit_code"] == 1 and r["report"]["recording"]["topics"] == [] and r["report"]["exit_code"] == 1
    assert C.run_system_model(p, tmp_path / "absent")["exit_code"] == 2


def test_CHK_23_a_deadline_with_the_same_reason_on_both_sides_names_it_once():
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    del t["/cmd_mode"], t["/robot_state"]
    got = [f for f in run(mdl(), facts(t)) if f["subject"] == "c_deadline"]
    assert [f["detail"]["reason"] for f in got] == ["topic_not_recorded"]


def test_CHK_24_within_a_kind_findings_are_ordered_by_subject_not_by_declaration_or_time():
    lo = W["min_log_time_ns"]
    m = mdl(lambda x: (x["interventions"][0].update(at_ns=lo - 5), x["interventions"][1].update(at_ns=lo - 9)))   # i_param is later in time but first by id
    got = [f["subject"] for f in run(m) if f["kind"] == "intervention_outside_recording"]
    assert got == ["i_param", "i_restart"]
    t = {x["topic"]: x["types"] for x in facts()["topics"]}
    t["/robot_state"] = ["x/msg/Y"]                                    # binding of component base
    t["/cmd_mode"] = ["x/msg/Z"]                                       # binding of component nav
    m2 = mdl(lambda x: (x["bindings"][1].update(type="std_msgs/msg/String"), x["bindings"][2].update(type="std_msgs/msg/String")))
    got2 = [f["subject"] for f in run(m2, facts(t)) if f["kind"] == "type_mismatch"]
    assert got2 == ["/cmd_mode", "/robot_state"]                       # by topic; by component it would be base (/robot_state) first


def test_CHK_25_the_json_report_has_sorted_keys_at_every_level():
    m = mdl()
    text = C.to_json(C.build_report(m, facts(), run(m)))
    def ordered(o):
        if isinstance(o, dict):
            assert list(o) == sorted(o), list(o)
            for v in o.values():
                ordered(v)
        elif isinstance(o, list):
            for v in o:
                ordered(v)
    ordered(json.loads(text))


def test_CHK_26_the_text_report_lists_every_recorded_topic_with_types_and_counts():
    m = mdl()
    text = C.render_text(C.build_report(m, facts(), run(m)))
    assert "  /diagnostics  diagnostic_msgs/msg/DiagnosticArray  10 messages" in text
    t = {"/typeless": []}
    assert "  /typeless  type not recorded  10 messages" in C.render_text(C.build_report(m, facts(t), []))


def test_CHK_27_an_invalid_model_with_many_problems_says_how_many_were_not_listed(tmp_path):
    p = mfx.write(tmp_path, mfx.mutated(lambda x: x["components"].extend({"id": f"c{i}", "role": "wizard"} for i in range(150))))
    r = C.run_system_model(p)
    assert r["exit_code"] == 3 and len(r["problems"]) == 101 and r["problems"][-1].startswith("... and ") and r["problems"][-1].endswith(" more problems")


def test_CHK_28_evidence_that_cannot_be_inspected_gives_no_report_and_says_so(tmp_path):
    p = mfx.write(tmp_path, mfx.valid())
    (tmp_path / "empty").mkdir()
    r = C.run_system_model(p, tmp_path / "empty")
    assert r == {"exit_code": 2, "problems": ["the evidence could not be inspected"], "report": None}
