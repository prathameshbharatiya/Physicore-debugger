"""TA-GOAL-* (TEST_PLAN 4)."""
import pytest

from physicore_collector.anomaly.detectors.tier_a_goal_status import GoalStatusDetector
from physicore_collector.anomaly.findings import find_forbidden
from physicore_collector.investigator.cdr import decoder_info
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

AL = "actionlib_msgs/msg/GoalStatusArray"
AM = "action_msgs/msg/GoalStatusArray"
ID = "tierA.goal_status"
C = cfg({ID: {"enabled": True}})
needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")


def uuid(n):
    return [n] * 16


def am(status, u=1, stamp=None, uu=...):
    gi = {"goal_id": {"uuid": uuid(u) if uu is ... else uu}, "stamp": stamp or {"sec": 1, "nanosec": 2}}
    return {"goal_info": gi, "status": status}


def al(status, gid="g1", text="", stamp=None):
    g = {"stamp": stamp or {"sec": 1, "nanosec": 2}}
    if gid is not ...:
        g["id"] = gid
    return {"goal_id": g, "status": status, "text": text}


def arr(*els):
    return {"status_list": list(els)}


def stream(payloads, start=F.BASE, step=F.MS):
    return [(start + i * step, p) for i, p in enumerate(payloads)]


PAD = stream([arr(am(1, 200 + i)) for i in range(40)])
PAD2 = stream([arr(am(1, 100 + i)) for i in range(40)], start=F.BASE + 200 * F.MS)


def go(tmp_path, segments, *, typ=AM, config=C, alternate=None, topic="/status"):
    bag = X.write_typed_bag(tmp_path / "bag", [{topic: s} for s in segments], {topic: typ}, alternate=alternate)
    out, ev, inv = run_detectors(bag, [GoalStatusDetector()], config)
    return out, result_of(out, ID), out.findings


def test_01_action_msgs_statuses(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(*[am(s, u=10 + s) for s in range(7)])])])
    got = sorted((f.observed_value["status"], f.observed_value["status_name"], f.severity, f.severity_basis) for f in fs)
    assert got == [(5, "CANCELED", "notice", "detector_policy"), (6, "ABORTED", "error", "detector_policy")]


def test_02_actionlib_only_aborted(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(*[al(s, gid=f"g{s}") for s in range(10)])])], typ=AL)
    assert [f.observed_value["status"] for f in fs] == [4] and fs[0].observed_value["status_name"] == "ABORTED"
    assert any(r.code == "constant_not_defined" and "CANCELED" in r.detail for r in res.reasons)     # D1
    assert res.status == "findings_reported"


def test_03_cross_family_trap(tmp_path):
    out, res, fs = go(tmp_path / "a", [stream([arr(am(4, 1), am(5, 2))])], typ=AM)
    assert [f.observed_value["status_name"] for f in fs] == ["CANCELED"]            # 4 is SUCCEEDED in action_msgs
    out, res, fs = go(tmp_path / "b", [stream([arr(al(4, "a"), al(5, "b"))])], typ=AL)
    assert [f.observed_value["status_name"] for f in fs] == ["ABORTED"]             # 5 is REJECTED in actionlib


def test_04_same_goal_200_messages_one_finding(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, 3))] * 200)])
    (f,) = fs
    assert f.occurrence_count == 200 and f.first_observed_at.value_ns == F.BASE and f.last_observed_at.value_ns == F.BASE + 199 * F.MS
    assert f.observed_value["goal_id"] == "03" * 16


def test_05_executing_then_aborted(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(2, 3))] * 3 + [arr(am(6, 3))] * 2)])
    (f,) = fs
    assert f.occurrence_count == 2 and f.first_observed_at.value_ns == F.BASE + 3 * F.MS
    assert not any(n.code == "first_seen_may_precede_recording" for n in f.uncertainty)


def test_06_two_goals_distinct(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, 1), am(6, 2))] * 3)])
    assert sorted(f.observed_value["goal_id"] for f in fs) == ["01" * 16, "02" * 16] and all(f.occurrence_count == 3 for f in fs)


def test_07_actionlib_empty_id_per_occurrence(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(al(4, ""))] * 4)], typ=AL)
    assert len(fs) == 4 and all(f.occurrence_count == 1 and f.observations[0].identity_state == "empty" for f in fs)
    assert all(any(n.code == "identity_unknown" for n in f.uncertainty) for f in fs)


def test_08_first_seen_already_terminal(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, 3))] * 2)])
    assert any(n.code == "first_seen_may_precede_recording" for n in fs[0].uncertainty)


def test_09_canceled_then_aborted_two_findings(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(5, 3)), arr(am(6, 3))])])
    assert sorted(f.observed_value["status_name"] for f in fs) == ["ABORTED", "CANCELED"]


def test_10_uuid_hex_identical_json_and_cdr_forms(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, uu=list(range(16))))])])
    assert fs[0].observed_value["goal_id"] == bytes(range(16)).hex()


@pytest.mark.parametrize("u,state", [(list(range(15)), "malformed"), (list(range(17)), "malformed"), ("0123456789abcdef", "malformed")])
def test_19_bad_uuid_still_a_finding(tmp_path, u, state):
    out, res, fs = go(tmp_path, [stream([arr(am(6, uu=u))])])
    (f,) = fs
    assert f.observations[0].identity_state == state and res.identity_field_malformed == {"goal_info.goal_id.uuid": 1}
    assert f.observed_value["goal_id_raw"]


def test_19_absent_uuid(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr({"goal_info": {"stamp": {"sec": 0, "nanosec": 0}}, "status": 6})])])
    assert fs[0].observations[0].identity_state == "absent" and res.identity_field_malformed == {"goal_info.goal_id.uuid": 1}


@pytest.mark.parametrize("gid,state", [(..., "absent"), (7, "malformed"), (None, "malformed")])
def test_20_actionlib_bad_id(tmp_path, gid, state):
    out, res, fs = go(tmp_path, [stream([arr(al(4, gid))])], typ=AL)
    assert fs[0].observations[0].identity_state == state and res.identity_field_malformed == {"goal_id.id": 1}


@pytest.mark.parametrize("bad", ["6", 6.0, True, None])
def test_21_status_wrong_type_not_a_finding(tmp_path, bad):
    out, res, fs = go(tmp_path, [stream([arr(am(bad, 3))])])
    assert fs == [] and res.payload_malformed_field == 1 and res.status == "insufficient_evidence"


def test_11_goal_stamp_secondary_only(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, 1, stamp={"sec": 0, "nanosec": 0})), arr(am(6, 2, stamp={"sec": -5, "nanosec": 7}))])])
    byid = {f.observed_value["goal_id"]: f for f in fs}
    assert byid["01" * 16].observed_value["goal_stamp"] == {"sec": 0, "nanosec": 0}
    assert byid["02" * 16].observed_value["goal_stamp"] == {"sec": -5, "nanosec": 7}
    assert all(f.clock_domain == "mcap_log_time" and f.first_observed_at.value_ns == F.BASE for f in fs[:1])


def test_12_out_of_set_status(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(-1, 1), am(7, 2), am(127, 3))])])
    assert fs == [] and res.unrecognised_values == {"-1": 1, "127": 1, "7": 1} and res.status == "insufficient_evidence"


def test_13_empty_status_list(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr()] * 3)])
    assert res.status == "no_anomaly_observed"


def test_14_canceled_wording(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(5, 1))])])
    assert "not by itself a failure indicator" in fs[0].explanation and find_forbidden(fs[0].explanation) == []
    out, res, fs = go(tmp_path / "x", [stream([arr(am(6, 1))])])
    assert "failure indicator" not in fs[0].explanation and find_forbidden(fs[0].explanation) == []


def test_15_two_empty_ids_same_message(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(al(4, ""), al(4, ""))])], typ=AL)
    assert len(fs) == 2 and sorted(f.observations[0].field_path for f in fs) == ["status_list[0].status", "status_list[1].status"]


def test_16_all_zero_uuid_never_merged(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, uu=[0] * 16))] * 3)])
    assert len(fs) == 3 and all(f.observations[0].identity_state == "empty" for f in fs)
    assert all(f.observed_value["goal_id_raw"].startswith("all-zero") or "0, 0" in f.observed_value["goal_id_raw"] for f in fs)
    assert all("unset" not in f.explanation.lower() for f in fs)


def test_17_two_nonzero_uuids(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, 1), am(6, 2))])])
    assert len(fs) == 2


def test_18_same_uuid_two_segments(tmp_path):
    s1 = PAD + stream([arr(am(6, 3))], start=F.BASE + 100 * F.MS)
    s2 = PAD2 + stream([arr(am(6, 3))], start=F.BASE + 300 * F.MS)
    ids = {n: go(tmp_path / n, [s1, s2], alternate=alt) for n, alt in (("a", None), ("b", None), ("c", {0}), ("d", {1}))}
    (f,) = ids["a"][2]
    assert f.occurrence_count == 2 and len(f.source) == 2
    fid = {n: v[2][0].finding_id for n, v in ids.items()}
    assert fid["a"] == fid["b"] and len({fid["a"], fid["c"], fid["d"]}) == 3


def test_actionlib_text_retained_truncated(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(al(4, "g", text="t" * 300))])], typ=AL)
    assert len(fs[0].observed_value["text"].encode()) <= 64 and any(n.code == "text_truncated" for n in fs[0].uncertainty)


def test_builtin_constants_note_for_json(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, 1))])])
    assert any(n.code == "constants_from_builtin_table" for n in fs[0].uncertainty)


def test_cap_unknown_identity(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(am(6, uu=[0] * 16))] * 4)], config=cfg({ID: {"enabled": True}}, {"max_findings_per_detector": 1}))
    assert len(fs) == 1 and res.findings_truncated.suppressed_exact_count == 3


@needs_decoder
def test_cdr_action_msgs_uuid_hex_matches_json(tmp_path):
    import numpy as np
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    def gs(status, u):
        return T["action_msgs/msg/GoalStatus"](goal_info=T["action_msgs/msg/GoalInfo"](goal_id=T["unique_identifier_msgs/msg/UUID"](uuid=np.array(u, dtype="uint8")),
                                               stamp=T["builtin_interfaces/msg/Time"](sec=1, nanosec=2)), status=status)
    m = T[AM](status_list=[gs(6, list(range(16))), gs(6, [0] * 16), gs(5, [9] * 16), gs(2, [8] * 16)])
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/status", AM, [(F.BASE, m)])]])
    out, ev, inv = run_detectors(bag, [GoalStatusDetector()], C)
    fs = out.findings
    assert sorted(f.observed_value["goal_id"] or "" for f in fs) == sorted(["", "09" * 16, bytes(range(16)).hex()])
    zero = next(f for f in fs if f.observed_value["goal_id"] is None)
    assert zero.observations[0].identity_state == "empty" and len(fs) == 3
    assert not any(n.code == "constants_from_builtin_table" for f in fs for n in f.uncertainty)


@needs_decoder
def test_cdr_actionlib_embedded_constants_mismatch(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    schema = cf.schema_text(AL).replace(b"ABORTED=4", b"ABORTED=14")
    m = T[AL](header=T["std_msgs/msg/Header"](stamp=T["builtin_interfaces/msg/Time"](sec=1, nanosec=2), frame_id=""), status_list=[])
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/status", AL, [(F.BASE, m)])]], schema_override={AL: schema})
    out, ev, inv = run_detectors(bag, [GoalStatusDetector()], C)
    assert result_of(out, ID).status == "blind_spot"
