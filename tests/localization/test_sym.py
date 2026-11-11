"""SYM-01..21 (TEST_PLAN 3). The real M3c detectors and the real M2 investigator produce the findings from synthetic recordings; the expected onset
intervals are computed from the declarative spec. This file is also the Step-2 verification record of the onset table (DESIGN 4.1)."""
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from physicore_collector.anomaly.findings import AnomalyReport
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator import investigate, load_contract
from physicore_collector.localization import symptoms as S
from physicore_collector.localization.report import find_forbidden_keys
from tests.localization import l2
from tests.localization.l2 import BASE, MS, NS, at

LOG = "mcap_log_time"
REPO = Path(__file__).resolve().parents[2]


def one(rep, cat=None):
    out = S.from_m3c(rep)
    assert out.problems == []
    syms = [s for s in out.symptoms if cat is None or s.category == cat]
    assert len(syms) == 1, [s.category for s in out.symptoms]
    return syms[0]


def iv(s):
    return (s.onset.lower_ns, s.onset.upper_ns)


def test_SYM_01_ros_log_run(tmp_path):
    msgs = l2.stream([l2.log(20)] * 3 + [l2.log(30)] * 3 + [l2.log(20)] * 2)
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    s = one(rep)
    assert iv(s) == (at(3), at(3)) and s.onset_basis == "received_message" and s.onset.domain == LOG
    assert (s.extent.lower_ns, s.extent.upper_ns) == (at(3), at(5))
    assert s.identities == {"logger_name": "nav"} and s.topic == s.node == "/rosout" and s.category == "log_severity"
    assert s.evidence[0].message_index == 3 and s.evidence[0].time_ns == at(3) and s.evidence[0].clock_domain == LOG
    assert s.secondary_onsets == [] and s.lanes == [LOG] and s.notes == [] and not s.truncated


def test_SYM_02_diagnostics_error(tmp_path):
    msgs = l2.stream([l2.diag(0), l2.diag(2, "motor", "hw1"), l2.diag(0)])
    rep, *_ = l2.m3c(tmp_path, {"/diagnostics": (l2.DIAG, msgs)}, {"tierA.diagnostics": {"enabled": True}})
    s = one(rep)
    assert iv(s) == (at(1), at(1)) and s.onset_basis == "received_message"
    assert s.identities == {"diagnostic_name": "motor", "hardware_id": "hw1"}


def test_SYM_03_goal_aborted(tmp_path):
    msgs = l2.stream([l2.goal(1), l2.goal(1), l2.goal(4, "g7")])
    rep, *_ = l2.m3c(tmp_path, {"/goal_status": (l2.GOAL, msgs)}, {"tierA.goal_status": {"enabled": True}})
    s = one(rep)
    assert iv(s) == (at(2), at(2)) and s.identities == {"goal_id": "g7"} and s.category == "goal_status"


def test_SYM_04_battery_health(tmp_path):
    msgs = l2.stream([l2.battery(1), l2.battery(1), l2.battery(2, "SN9", "bay")])
    rep, *_ = l2.m3c(tmp_path, {"/battery": (l2.BAT, msgs)}, {"tierA.battery": {"enabled": True, "topics": "auto", "health_flags": True}})
    s = one(rep, "power_status")
    assert iv(s) == (at(2), at(2)) and s.identities == {"battery_serial": "SN9", "battery_location": "bay"}


def _gap_times():
    return [at(i, 100 * MS) for i in range(30)] + [at(i, 100 * MS) for i in range(39, 50)]


def test_SYM_05_publishing_gap(tmp_path):
    times = _gap_times()
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.SCAN, [(t, {"i": i}) for i, t in enumerate(times)])}, l2.GAP)
    s = one(rep)
    assert iv(s) == (at(29, 100 * MS), at(39, 100 * MS)) and s.onset_basis == "silence_between_captured_messages"
    assert (s.extent.lower_ns, s.extent.upper_ns) == iv(s) and s.notes == ["onset_interval_width"]
    codes = {n.code for n in s.source_uncertainty}
    assert "capture_loss_indistinguishable_from_silence" in codes and "baseline_from_recording" in codes


def test_SYM_06_rate_change_first_flagged_window(tmp_path):
    counts = [10] * 6 + [2] * 2 + [10] * 6
    times = []
    for k, c in enumerate(counts):
        step = NS // (c + 1)
        times += [BASE + k * NS + (j + 1) * step for j in range(c)]
    times[0] = BASE
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.SCAN, [(t, {"i": i}) for i, t in enumerate(times)])}, l2.RATE)
    s = one(rep, "rate_change")
    assert iv(s) == (BASE + 6 * NS, BASE + 7 * NS) and s.onset_basis == "window_span"
    assert (s.extent.lower_ns, s.extent.upper_ns) == (BASE + 6 * NS, BASE + 8 * NS)        # the whole two-window run is the extent


def test_SYM_07_non_finite(tmp_path):
    payloads = [b'{"data": 1.0}', b'{"data": NaN}', b'{"data": NaN}', b'{"data": 1.0}']
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.F64, l2.stream(payloads))}, l2.NONFIN)
    s = one(rep, "non_finite_value")
    assert iv(s) == (at(1), at(1)) and s.onset_basis == "received_message" and (s.extent.lower_ns, s.extent.upper_ns) == (at(1), at(2))


def test_SYM_08_array_length_change(tmp_path):
    msgs = l2.stream([{"ranges": [1.0] * n} for n in (360, 360, 10, 10, 360)])
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.SCAN, msgs)}, l2.ARR)
    s = one(rep, "array_length_change")
    assert iv(s) == (at(2), at(2)) and (s.extent.lower_ns, s.extent.upper_ns) == (at(2), at(3))


def test_SYM_09_stamp_regression_has_a_log_lane_and_a_payload_lane(tmp_path):
    vals = [10 * NS, 11 * NS, 9 * NS + 5, 12 * NS]
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.HDR, l2.stream([l2.stamp_msg(v) for v in vals]))}, l2.STAMP)
    s = one(rep, "timestamp_regression")
    pdom = "payload_stamp:/t:header.stamp"
    assert s.onset.domain == LOG and iv(s) == (at(2), at(2)) and s.onset_basis == "received_message"       # receipt of the offending message
    assert len(s.secondary_onsets) == 1 and s.secondary_onsets[0].lane == pdom
    assert (s.secondary_onsets[0].onset.lower_ns, s.secondary_onsets[0].onset.upper_ns) == (9 * NS + 5, 9 * NS + 5)
    assert s.secondary_onsets[0].onset_basis == "payload_stamp_value" and s.lanes == [LOG, pdom]
    assert s.extent.domain == pdom


def test_SYM_09b_stamp_duplicate(tmp_path):
    vals = [5, 6, 7, 7, 7, 8]
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.HDR, l2.stream([l2.stamp_msg(v) for v in vals]))}, l2.STAMP)
    s = one(rep, "timestamp_duplicate")
    assert iv(s) == (at(3), at(3)) and s.secondary_onsets[0].onset.lower_ns == 7           # first repeat is the receipt that evidences it


def test_SYM_09c_stamp_symptom_without_two_retained_observations_has_only_the_payload_lane(tmp_path):
    vals = [10 * NS, 11 * NS, 9 * NS + 5, 12 * NS]
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.HDR, l2.stream([l2.stamp_msg(v) for v in vals]))}, l2.STAMP, limits={"max_retained_observations": 1})
    s = one(rep, "timestamp_regression")
    assert s.lanes == ["payload_stamp:/t:header.stamp"] and s.secondary_onsets == []          # the offending receipt was not retained: no log-lane claim


def test_SYM_10_log_lane_and_payload_lane_are_never_related(tmp_path):
    vals = [10 * NS, 11 * NS, 9 * NS + 5, 12 * NS]
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.HDR, l2.stream([l2.stamp_msg(v) for v in vals]))}, l2.STAMP)
    s = one(rep)
    a = s.onset_in(LOG)
    b = s.onset_in("payload_stamp:/t:header.stamp")
    assert a is not None and b is not None and s.onset_in("other") is None
    with pytest.raises(Exception):
        a.order(b)


M2_REC = REPO / "examples" / "investigator" / "example_recording.mcap"
M2_EXP = REPO / "examples" / "investigator" / "expectations.json"
M3B_REC = REPO / "examples" / "m3b" / "m3b_example.mcap"
M3B_EXP = REPO / "examples" / "m3b" / "expectations.json"


def test_SYM_11_m2_range_and_transition_deadline():
    res = investigate(inspect_evidence(M2_REC), load_contract(M2_EXP))
    out = S.from_m2(res)
    assert out.problems == []
    by = {s.source_id: s for s in out.symptoms}
    rng = next(s for s in out.symptoms if s.category == "m2_range")
    assert iv(rng) == (1700000000900000000, 1700000000900000000) and rng.onset_basis == "received_sample" and rng.onset.domain == LOG
    tr = next(s for s in out.symptoms if s.category == "m2_transition")
    # entry into CLOSING at 1700000000500000000 and a 500 ms deadline (from the contract)
    assert iv(tr) == (1700000000500000000, 1700000001000000000) and tr.onset_basis == "deadline_window"
    assert len(by) == 2 and {e.role for e in tr.evidence} == {"transition_entry"}


from physicore_collector.investigator.cdr import decoder_info

needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="optional extra 'ros2' is not installed (the M3b example recording is CDR)")


@needs_decoder
def test_SYM_12_m2_event_response_deadline_window():
    res = investigate(inspect_evidence(M3B_REC), load_contract(M3B_EXP))
    out = S.from_m2(res)
    assert out.problems == []
    er = next(s for s in out.symptoms if s.category == "m2_event_response")
    # trigger received at 1700000002000000000, deadline 500 ms; the late response (3.0 s) must NOT define the start
    assert iv(er) == (1700000002000000000, 1700000002500000000) and er.onset_basis == "deadline_window"
    assert {e.role for e in er.evidence} == {"trigger_event", "late_response"}


def test_SYM_13_no_expectations_no_m2_symptoms(tmp_path):
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, l2.stream([l2.log(30)]))}, {"tierA.ros_log": {"enabled": True}})
    assert {s.source for s in S.from_m3c(rep).symptoms} == {"m3c_finding"}


def test_SYM_14_source_fields_copied_verbatim(tmp_path):
    msgs = l2.stream([l2.log(30, "a"), l2.log(40, "b"), l2.log(50, "c"), l2.log(20, "d")])
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    by = {s.source_id: s for s in S.from_m3c(rep).symptoms}
    assert len(rep.findings) == 3 == len(by)
    for f in rep.findings:
        s = by[f.finding_id]
        assert (s.severity, s.severity_basis, s.detector_or_rule, s.topic) == (f.severity, f.severity_basis, f.detector_id, f.topic)
        assert [(n.code, n.params) for n in s.source_uncertainty] == [(n.code, n.params) for n in f.uncertainty]
        assert (s.source_coverage.topic_coverage, s.source_coverage.messages_examined) == (f.coverage.topic_coverage, f.coverage.messages_examined)
        assert [(e.artifact_ref, e.sha256, e.topic, e.message_index, e.time_ns) for e in s.evidence] == \
               [(o.artifact_ref, o.sha256, o.topic, o.message_index, o.log_time_ns) for o in f.observations]
    assert sorted(s.severity for s in by.values()) == ["critical", "error", "warning"]


def test_SYM_15_severity_does_not_affect_placement(tmp_path):
    msgs = l2.stream([l2.log(20), l2.log(30, "a"), l2.log(50, "b")])
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    base = {s.source_id: (s.onset, s.extent) for s in S.from_m3c(rep).symptoms}
    for f in rep.findings:
        f.severity = "critical" if f.severity != "critical" else "warning"
    flipped = {s.source_id: (s.onset, s.extent) for s in S.from_m3c(rep).symptoms}
    assert base == flipped


def test_SYM_16_truncated_source_is_flagged(tmp_path):
    msgs = l2.stream([l2.log(30, "a")] * 12)
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}}, limits={"max_retained_observations": 3})
    s = one(rep)
    assert rep.findings[0].observations_truncated and s.truncated and len(s.evidence) == 3


def test_SYM_17_one_symptom_per_finding_unique_ids(tmp_path):
    msgs = l2.stream([l2.log(lv, nm) for lv in (30, 40, 50) for nm in ("a", "b", "c")])
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    out = S.from_m3c(rep)
    assert len(out.symptoms) == len(rep.findings) == 9
    assert len({s.symptom_id for s in out.symptoms}) == 9 and {s.source_id for s in out.symptoms} == {f.finding_id for f in rep.findings}


def test_SYM_18_symptom_id_stable_and_sensitive(tmp_path):
    msgs = l2.stream([l2.log(30, "a"), l2.log(40, "b")])
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    a = sorted(s.symptom_id for s in S.from_m3c(rep).symptoms)
    rep.findings.reverse()
    assert sorted(s.symptom_id for s in S.from_m3c(rep).symptoms) == a
    f = rep.findings[0]
    f.first_observed_at = f.first_observed_at.model_copy(update={"value_ns": f.first_observed_at.value_ns + 1})
    assert sorted(s.symptom_id for s in S.from_m3c(rep).symptoms) != a


def test_SYM_18_hash_seed_independence(tmp_path):
    import os, subprocess, sys
    code = ("from tests.localization import l2\nfrom physicore_collector.localization import symptoms as S\nimport sys, pathlib\n"
            "tmp = pathlib.Path(sys.argv[1])\nmsgs = l2.stream([l2.log(30, 'a'), l2.log(40, 'b')])\n"
            "rep, *_ = l2.m3c(tmp, {'/rosout': (l2.LOG, msgs)}, {'tierA.ros_log': {'enabled': True}})\n"
            "print(sorted(s.symptom_id for s in S.from_m3c(rep).symptoms))")
    outs = []
    for seed in ("0", "1", "random"):
        d = tmp_path / f"s{seed}"
        d.mkdir()
        r = subprocess.run([__import__("sys").executable, "-c", code, str(d)], capture_output=True, text=True, cwd=REPO,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": f"{REPO}/src:{REPO}"})
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout)
    assert outs[0] == outs[1] == outs[2]


def test_SYM_19_symptom_cap_exact_suppressed_count(tmp_path):
    msgs = l2.stream([l2.log(30, f"n{i}") for i in range(12)])
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    syms = S.from_m3c(rep).symptoms
    kept, suppressed = S.apply_cap(syms, 5)
    assert len(syms) == 12 and len(kept) == 5 and suppressed == 7
    assert [s.symptom_id for s in kept] == [s.symptom_id for s in sorted(syms, key=S.canonical_key)[:5]]
    assert [iv(s)[0] for s in kept] == [at(i) for i in range(5)]
    kept2, sup2 = S.apply_cap(syms, 100)
    assert len(kept2) == 12 and sup2 == 0


def test_SYM_20_every_m3c_category_has_an_onset_rule():
    from physicore_collector.anomaly.findings import CATEGORIES
    assert set(CATEGORIES) == set(S.M3C_CATEGORIES_HANDLED)


def test_SYM_21_unknown_category_is_refused_not_defaulted(tmp_path):
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, l2.stream([l2.log(30)]))}, {"tierA.ros_log": {"enabled": True}})
    f = rep.findings[0].model_copy(update={"anomaly_category": "mystery"})
    bad = AnomalyReport.model_construct(**{**rep.__dict__, "findings": [f]})
    out = S.from_m3c(bad)
    assert out.symptoms == [] and [p.code for p in out.problems] == ["unplaceable_finding"]


def test_rate_finding_missing_window_fields_is_a_problem(tmp_path):
    counts = [10] * 6 + [2] * 2 + [10] * 6
    times = []
    for k, c in enumerate(counts):
        step = NS // (c + 1)
        times += [BASE + k * NS + (j + 1) * step for j in range(c)]
    times[0] = BASE
    rep, *_ = l2.m3c(tmp_path, {"/t": (l2.SCAN, [(t, {"i": i}) for i, t in enumerate(times)])}, l2.RATE)
    f = rep.findings[0].model_copy(update={"observed_value": {}})
    bad = AnomalyReport.model_construct(**{**rep.__dict__, "findings": [f]})
    out = S.from_m3c(bad)
    assert out.symptoms == [] and out.problems[0].code == "unplaceable_finding"


def test_identity_withheld_when_unknown_or_truncated(tmp_path):
    msgs = l2.stream([l2.log(30, "x" * 200), l2.log(40, ""), l2.log(50, "ok")])
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    by = {s.severity: s for s in S.from_m3c(rep).symptoms}
    assert by["warning"].identities == {}              # truncated text is never matched (fail closed)
    assert by["error"].identities == {}                # empty identity is unknown
    assert by["critical"].identities == {"logger_name": "ok"}


def test_symptom_model_is_strict_and_has_no_score_keys(tmp_path):
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, l2.stream([l2.log(30)]))}, {"tierA.ros_log": {"enabled": True}})
    s = one(rep)
    assert find_forbidden_keys(s.model_dump(mode="json")) == []
    with pytest.raises(ValidationError):
        S.Symptom(**{**s.model_dump(), "confidence": 1})
    with pytest.raises(ValidationError):
        S.Symptom(**{**s.model_dump(), "source": "guess"})
    with pytest.raises(ValidationError):
        S.Symptom(**{**s.model_dump(), "evidence": []})
    with pytest.raises(ValidationError):
        S.Placement(lane="a", onset={"domain": "b", "lower_ns": 1, "upper_ns": 1}, onset_basis="received_message")


# ---- strengthening after the first Step-2 mutation run (each test below kills a named survivor)
def _m2_transition_result():
    res = investigate(inspect_evidence(M2_REC), load_contract(M2_EXP))
    f = next(c for c in res.candidates if c.expectation_kind == "transition")
    return res, f


def _swap_candidate(res, f):
    cands = [f if c.finding_id == f.finding_id else c for c in res.candidates]
    return res.model_copy(update={"candidates": cands})


def test_m2_start_is_the_earliest_trigger_or_entry_and_ignores_other_roles():
    res, f = _m2_transition_result()
    entry_src, entry_obs = f.sources[0], f.observations[0]
    t_entry = entry_obs.timestamp_ns
    early = entry_obs.model_copy(update={"observation_id": "obs:x#900", "message_index": 900, "timestamp_ns": t_entry - 100})
    early_src = entry_src.model_copy(update={"observation_id": "obs:x#900", "role": "transition_entry"})
    late_other = entry_obs.model_copy(update={"observation_id": "obs:x#901", "message_index": 901, "timestamp_ns": t_entry - 400})
    other_src = entry_src.model_copy(update={"observation_id": "obs:x#901", "role": "late_response"})
    g = f.model_copy(update={"sources": [entry_src, early_src, other_src], "observations": [entry_obs, early, late_other]})
    out = S.from_m2(_swap_candidate(res, g))
    s = next(x for x in out.symptoms if x.category == "m2_transition")
    assert iv(s) == (t_entry - 100, f.time.ns)          # earliest qualifying role; the earlier late_response is ignored


def test_m2_entry_in_another_clock_domain_is_unplaceable():
    res, f = _m2_transition_result()
    other = f.observations[0].model_copy(update={"clock_domain": "payload_field:header.stamp"})
    g = f.model_copy(update={"observations": [other]})
    out = S.from_m2(_swap_candidate(res, g))
    assert [x for x in out.symptoms if x.category == "m2_transition"] == [] and [p.code for p in out.problems] == ["unplaceable_finding"]


def test_m2_duplicate_candidates_make_one_symptom():
    res, f = _m2_transition_result()
    out = S.from_m2(res.model_copy(update={"candidates": list(res.candidates) + [f, f]}))
    assert len([x for x in out.symptoms if x.category == "m2_transition"]) == 1 and len(out.symptoms) == 2


def test_m2_symptoms_carry_no_invented_severity_and_real_coverage():
    res, f = _m2_transition_result()
    s = next(x for x in S.from_m2(res).symptoms if x.category == "m2_transition")
    assert (s.severity, s.severity_basis) == ("not_applicable", "not_applicable") and s.source_coverage.topic_coverage == "complete"
    er = next(r for r in res.expectation_results if r.expectation_id == f.expectation_id)
    bad = er.model_copy(update={"evidence_complete": False, "missing_evidence": ["segment_missing"]})
    res2 = res.model_copy(update={"expectation_results": [bad if r.expectation_id == er.expectation_id else r for r in res.expectation_results]})
    s2 = next(x for x in S.from_m2(res2).symptoms if x.category == "m2_transition")
    assert s2.source_coverage.topic_coverage == "incomplete" and s2.source_coverage.incomplete_reasons == ["segment_missing"]


def test_point_category_outside_the_log_domain_is_refused(tmp_path):
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, l2.stream([l2.log(30)]))}, {"tierA.ros_log": {"enabled": True}})
    f0 = rep.findings[0]
    f = f0.model_copy(update={"clock_domain": "payload_stamp:/rosout:header.stamp"})
    out = S.from_m3c(AnomalyReport.model_construct(**{**rep.__dict__, "findings": [f]}))
    assert out.symptoms == [] and out.problems[0].code == "unplaceable_finding"


def test_identity_withheld_when_any_identity_part_is_unknown(tmp_path):
    el = {"level": 2, "name": "motor", "message": "m", "values": []}            # hardware_id absent: the element's identity is incomplete
    rep, *_ = l2.m3c(tmp_path, {"/diagnostics": (l2.DIAG, l2.stream([{"status": [el]}]))}, {"tierA.diagnostics": {"enabled": True}})
    f = rep.findings[0]
    assert "identity_unknown" in {n.code for n in f.uncertainty}
    assert f.observed_value["name"] == "motor"
    assert one(rep).identities == {}                                           # fail closed: a partly unknown identity is never matched


def test_empty_battery_serial_is_not_an_identity(tmp_path):
    msgs = l2.stream([l2.battery(1), l2.battery(2, "", "bay")])
    rep, *_ = l2.m3c(tmp_path, {"/battery": (l2.BAT, msgs)}, {"tierA.battery": {"enabled": True, "topics": "auto", "health_flags": True}})
    f = next(f for f in rep.findings if f.anomaly_category == "power_status")
    assert "identity_unknown" not in {n.code for n in f.uncertainty}
    assert one(rep, "power_status").identities == {"battery_location": "bay"}


def test_source_coverage_is_copied_including_incomplete(tmp_path):
    rep, *_ = l2.m3c(tmp_path, {"/rosout": (l2.LOG, l2.stream([l2.log(30)]))}, {"tierA.ros_log": {"enabled": True}})
    f = rep.findings[0]
    f2 = f.model_copy(update={"coverage": f.coverage.model_copy(update={"topic_coverage": "incomplete", "incomplete_reasons": ["why"], "messages_undecodable": 3})})
    s = S.from_m3c(AnomalyReport.model_construct(**{**rep.__dict__, "findings": [f2]})).symptoms[0]
    assert (s.source_coverage.topic_coverage, s.source_coverage.incomplete_reasons, s.source_coverage.messages_undecodable) == ("incomplete", ["why"], 3)


def test_symptom_ids_differ_for_findings_with_identical_placement(tmp_path):
    msgs = l2.stream([l2.log(30)])
    rep, *_ = l2.m3c(tmp_path, {"/a": (l2.LOG, msgs), "/b": (l2.LOG, msgs)}, {"tierA.ros_log": {"enabled": True}})
    syms = S.from_m3c(rep).symptoms
    assert len(syms) == 2 and iv(syms[0]) == iv(syms[1]) and syms[0].symptom_id != syms[1].symptom_id


def test_one_placement_per_lane():
    base = dict(symptom_id="s-1", source="m3c_finding", source_id="f", detector_or_rule="d", category="c", topic="/t", node="/t", severity="notice",
                severity_basis="detector_policy", extent={"domain": "d1", "lower_ns": 1, "upper_ns": 1}, onset={"domain": "d1", "lower_ns": 1, "upper_ns": 1},
                onset_basis="received_message", evidence=[{"artifact_ref": "a", "topic": "/t", "message_index": 0}],
                source_coverage={"topic_coverage": "complete"})
    S.Symptom(**base)
    dup = {**base, "secondary_onsets": [{"lane": "d1", "onset": {"domain": "d1", "lower_ns": 2, "upper_ns": 2}, "onset_basis": "received_message"}]}
    with pytest.raises(ValidationError):
        S.Symptom(**dup)
