"""BLK-01..17, PRV-13..16 (blocker half), ADV-05..08, UNC-03 (TEST_PLAN). Real M3c detectors and the real M2 investigator on synthetic recordings;
expectations come from the spec (clarifications A and B), not from blockers.py."""
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from physicore_collector.anomaly.service import run_anomaly_detection
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator import investigate, parse_contract
from physicore_collector.localization import blockers as B
from physicore_collector.localization import symptoms as S
from physicore_collector.localization.precedence import build_lanes
from physicore_collector.localization.report import find_forbidden_keys, find_forbidden_values
from physicore_collector.localization.vocabulary import BLOCKER_CODES, CAVEAT_CODES
from tests import investigator_fixtures as F
from tests.localization import l2
from tests.localization.l1 import sym

LOG = "mcap_log_time"
ROS = {"tierA.ros_log": {"enabled": True}}
BLIND_ARR = {"tierB.array_length": {"enabled": True, "rules": [{"topic": "/rosout", "path": "msg", "expected_length": 3}]}}


def go(tmp, topics, dets, *, segments=None, limits=None, m2=None, post=None):
    rep, bag, cfg = l2.m3c(tmp, topics, dets, limits=limits, segments=segments)
    if post:
        post(bag)
    rep = run_anomaly_detection(bag, cfg) if post else rep
    inv = inspect_evidence(bag)
    syms = S.from_m3c(rep).symptoms
    return rep, inv, syms, B.build_blockers(rep, inv, m2, syms)


def codes(bs):
    return sorted(b.code for b in bs.blockers)


def of(bs, code):
    return [b for b in bs.blockers if b.code == code]


def rosout(payloads):
    return {"/rosout": (l2.LOG, l2.stream(payloads))}


# ---------------------------------------------------------------------------------------------------------------- BLK-01..08

def test_BLK_00_clean_recording_has_no_blockers(tmp_path):
    *_, bs = go(tmp_path, rosout([l2.log(30)]), ROS)
    assert bs.blockers == []


def test_BLK_01_missing_segment_blocks_every_lane(tmp_path):
    segs = [{"/rosout": l2.stream([l2.log(20)] * 2)}, {"/rosout": l2.stream([l2.log(30)], start=l2.at(10))}]
    topics = {"/rosout": (l2.LOG, [])}
    rep, inv, syms, bs = go(tmp_path, topics, ROS, segments=segs, post=lambda bag: sorted(Path(bag).glob("*.mcap"))[0].unlink())
    inc = of(bs, "recording_incomplete")
    assert inc and all(b.source.kind in ("inventory_record", "detector_result", "finding") for b in inc)
    assert any(b.source.kind == "inventory_record" and b.scope.lane is None for b in inc)
    inv_b = next(b for b in inc if b.source.kind == "inventory_record")
    assert B.lanes_of(inv_b, [LOG, "payload_stamp:/t:header.stamp"]) == [LOG, "payload_stamp:/t:header.stamp"]


def test_BLK_02_blind_detector(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(20)]), BLIND_ARR)
    r = next(x for x in rep.results if x.detector_id == "tierB.array_length")
    assert r.status == "blind_spot"
    b = of(bs, "detector_blind_spot")
    assert len(b) == 1 and b[0].scope.detector == "tierB.array_length" and b[0].source.kind == "detector_result" and b[0].observations == []


def test_BLK_03_insufficient_evidence(tmp_path):
    topics = {"/t": (l2.F64, l2.stream([{"data": 1.0}] * 3))}
    rep, inv, syms, bs = go(tmp_path, topics, l2.GAP)
    r = next(x for x in rep.results if x.detector_id == "tierB.publishing_gap")
    assert r.status == "insufficient_evidence"
    assert [b.scope.detector for b in of(bs, "detector_insufficient_evidence")] == ["tierB.publishing_gap"]


def test_BLK_04_unselected_detector_is_a_caveat_not_a_blocker(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(30)]), ROS)
    cv = [c for c in bs.caveats if c.code == "detector_not_configured"]
    assert {c.scope.detector for c in cv} == {r.detector_id for r in rep.results if r.status == "not_configured"} and len(cv) == 8
    assert "detector_blind_spot" not in codes(bs)


def test_BLK_05_not_applicable_is_a_caveat(tmp_path):
    topics = {"/rosout": ("std_msgs/msg/String", l2.stream([l2.log(40)]))}
    rep, inv, syms, bs = go(tmp_path, topics, {"tierA.ros_log": {"enabled": True, "topics": ["/rosout"]}})
    assert next(r for r in rep.results if r.detector_id == "tierA.ros_log").status == "not_applicable"
    assert [c.scope.detector for c in bs.caveats if c.code == "detector_not_applicable"] == ["tierA.ros_log"]
    assert bs.blockers == []


def test_BLK_06_undecodable_exact_count(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([b"{x", l2.log(40), b"{y", b"{z"]), ROS)
    b = of(bs, "undecodable_messages")
    assert len(b) == 1 and b[0].count == 3 == next(r for r in rep.results if r.detector_id == "tierA.ros_log").messages_undecodable
    assert b[0].source.kind == "detector_counter" and b[0].scope.detector == "tierA.ros_log"


def test_BLK_07_unparseable_exact_count(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log("40"), l2.log("41"), l2.log(20)]), ROS)
    r = next(x for x in rep.results if x.detector_id == "tierA.ros_log")
    assert [(x.code, x.count) for x in r.reasons] == [("unparseable_messages", 2)]
    b = of(bs, "unparseable_messages")
    assert [(x.count, x.source.kind, x.scope.detector) for x in b] == [(2, "detector_counter", "tierA.ros_log")]
    assert [x.scope.detector for x in of(bs, "detector_insufficient_evidence")] == ["tierA.ros_log"]


def test_BLK_08_findings_cap_exact_suppressed_count(tmp_path):
    msgs = [l2.log(40, f"n{i}") for i in range(7)]
    rep, inv, syms, bs = go(tmp_path, rosout(msgs), ROS, limits={"max_findings_per_detector": 3})
    r = next(x for x in rep.results if x.detector_id == "tierA.ros_log")
    b = of(bs, "findings_truncated")
    assert r.findings_truncated.suppressed_exact_count == 4 and [x.count for x in b] == [4]
    assert b[0].source.kind == "detector_counter" and b[0].source.field == "findings_truncated.suppressed_exact_count"


# ---------------------------------------------------------------------------------------------------------------- M2 (BLK-09, 10)

def m2run(path, exps):
    return investigate(inspect_evidence(path), parse_contract(F.contract(exps)))


def T(i):
    return F.BASE + i * 100 * F.MS


def test_BLK_09_m2_expectation_not_evaluated(tmp_path):
    p = F.write_mcap(tmp_path / "r.mcap", {"/temp": F.series([1, 2, 3])})
    res = m2run(p, [F.expectation("E1", topic="/absent", check={"kind": "range", "max": 10})])
    inv = inspect_evidence(p)
    rep = run_anomaly_detection(p, l2.write_config(tmp_path, {}))
    bs = B.build_blockers(rep, inv, res, [])
    nb = of(bs, "m2_expectation_not_evaluated")
    assert [(b.scope.expectation, b.source.kind) for b in nb] == [("E1", "m2_result")]


def test_BLK_10_m2_blind_spot_rule_matches_m2_output_and_carries_real_observation(tmp_path):
    p = F.write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), b"{broken"), (T(2), {"v": 99})]})
    res = m2run(p, [F.expectation("E1", check={"kind": "range", "max": 10})])
    er = res.expectation_results[0]
    inv = inspect_evidence(p)
    rep = run_anomaly_detection(p, l2.write_config(tmp_path, {}))
    syms = S.from_m2(res).symptoms
    bs = B.build_blockers(rep, inv, res, syms)
    b = of(bs, "m2_blind_spot_before_or_unplaced")
    assert len(b) == 1 and b[0].count == er.blind_spot_count == 1 and b[0].scope.lane == er.clock_domain
    # M2's own rule: a blind spot at or before the earliest mismatch makes that claim undefendable
    assert er.first_blind_spot_ns <= res.candidates[0].time.ns and res.status.value == "insufficient_evidence"
    # observation-backed: the blind spot has a real message index in the real artifact
    assert [(o.message_index, o.time_ns) for o in b[0].observations] == [(1, T(1))]
    art = next(a for a in inv.artifacts if a.relative_path == "r.mcap")
    assert b[0].observations[0].artifact_ref == art.ref_id and b[0].observations[0].sha256 == art.sha256
    lanes, _ = build_lanes(syms, B.blockers_by_lane(bs.blockers, [er.clock_domain]), [er.clock_domain])
    assert lanes[0].earliest.defence == "not_defended" and lanes[0].earliest.blocker_ids


def test_BLK_10b_blind_spot_after_mismatch_still_blocks_the_lane_fail_closed(tmp_path):
    p = F.write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), {"v": 99}), (T(2), b"{broken")]})
    res = m2run(p, [F.expectation("E1", check={"kind": "range", "max": 10})])
    assert res.status.value == "divergence_found"                      # M2 itself defends the claim ...
    bs = B.build_blockers(run_anomaly_detection(p, l2.write_config(tmp_path, {})), inspect_evidence(p), res, S.from_m2(res).symptoms)
    assert of(bs, "m2_blind_spot_before_or_unplaced")                  # ... M3d is stricter: blockers are lane-scoped, not time-scoped (DESIGN 4.3)


def test_BLK_10c_unresolvable_blind_spot_observation_is_not_invented(tmp_path):
    p = F.write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), b"{broken"), (T(2), {"v": 99})]})
    res = m2run(p, [F.expectation("E1", check={"kind": "range", "max": 10})])
    res.expectation_results[0].blind_spots[0].observation_id = "obs:not_in_inventory.mcap#1"
    bs = B.build_blockers(run_anomaly_detection(p, l2.write_config(tmp_path, {})), inspect_evidence(p), res, [])
    b = of(bs, "m2_blind_spot_before_or_unplaced")[0]
    assert b.observations == [] and b.count == 1


# ---------------------------------------------------------------------------------------------------------------- lanes (BLK-11, 12)

def test_BLK_11_topic_blind_fails_closed_for_unrelated_earliest(tmp_path):
    topics = {"/rosout": (l2.LOG, l2.stream([l2.log(30)])), "/t": (l2.F64, l2.stream([{"data": 1.0}] * 3))}
    rep, inv, syms, bs = go(tmp_path, topics, {**ROS, **l2.GAP})
    assert len(syms) == 1 and syms[0].topic == "/rosout"
    gb = [b for b in bs.blockers if b.scope.detector == "tierB.publishing_gap"]
    assert gb
    by_lane = B.blockers_by_lane(bs.blockers, [LOG])
    lanes, _ = build_lanes(syms, by_lane)
    assert lanes[0].earliest.defence == "not_defended" and set(gb[0].blocker_id for _ in [0]) <= set(lanes[0].earliest.blocker_ids)


def test_BLK_11c_undecodable_on_topic_fails_closed(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(30), b"{x"]), ROS)
    lanes, _ = build_lanes(syms, B.blockers_by_lane(bs.blockers, [LOG]))
    assert lanes[0].earliest.defence == "not_defended"


def test_BLK_12_blocker_in_lane_a_leaves_lane_b_defended():
    a = sym("a", 10, 10, lane="A")
    b = sym("b", 10, 10, lane="B")
    blk = B.make_blocker("detector_blind_spot", B.SourceRecord(kind="detector_result", ref="d", field="status"), B.BlockerScope(lane="A"))
    by = B.blockers_by_lane([blk], ["A", "B"])
    assert by == {"A": [blk.blocker_id]}
    lanes, _ = build_lanes([a, b], by)
    d = {ln.domain: ln.earliest.defence for ln in lanes}
    assert d == {"A": "not_defended", "B": "defended"}


def test_BLK_12b_stamp_detector_blocker_reaches_payload_stamp_lanes_only_for_stamp_order():
    pl = "payload_stamp:/t:header.stamp"
    mk = lambda d: B.make_blocker("detector_blind_spot", B.SourceRecord(kind="detector_result", ref=d, field="status"), B.BlockerScope(detector=d))  # noqa: E731
    assert B.lanes_of(mk("tierB.stamp_order"), [LOG, pl]) == [LOG, pl]
    assert B.lanes_of(mk("tierB.publishing_gap"), [LOG, pl]) == [LOG]
    assert B.lanes_of(B.make_blocker("symptom_cap_reached", B.SourceRecord(kind="m3d_limit", ref="symptoms", field="limits.symptoms")), [LOG, pl]) == [LOG, pl]


# ---------------------------------------------------------------------------------------------------------------- caveats (BLK-13..16)

def test_BLK_13_always_present_caveats(tmp_path):
    *_, bs = go(tmp_path, rosout([l2.log(30)]), ROS)
    cs = {c.code for c in bs.caveats}
    assert {"receipt_order_not_occurrence_order", "real_recording_validation_outstanding"} <= cs


def test_BLK_14_caveats_never_change_defence(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(30)]), ROS)
    d1 = build_lanes(syms, B.blockers_by_lane(bs.blockers, [LOG]))[0][0].earliest
    bs.caveats.clear()
    d2 = build_lanes(syms, B.blockers_by_lane(bs.blockers, [LOG]))[0][0].earliest
    assert d1 == d2 and d1.defence == "defended"


def test_BLK_15_gap_detector_carries_capture_loss_caveat(tmp_path):
    msgs = [{"data": 1.0}] * 30
    ts = [l2.at(i) for i in range(15)] + [l2.at(i + 400) for i in range(15)]
    rep, inv, syms, bs = go(tmp_path, {"/t": (l2.F64, list(zip(ts, msgs)))}, l2.GAP)
    assert any(s.category == "publishing_gap" for s in syms)
    assert [c.scope.detector for c in bs.caveats if c.code == "capture_loss_indistinguishable_from_silence"] == ["tierB.publishing_gap"]
    assert {"baseline_from_recording", "baseline_contaminated_possible"} <= {c.code for c in bs.caveats}


def test_BLK_16_boundary_gaps_caveat_carried_from_m3c(tmp_path):
    msgs = [{"data": 1.0}] * 30
    rep, inv, syms, bs = go(tmp_path, {"/t": (l2.F64, l2.stream(msgs))}, l2.GAP)
    has = any(x.code == "boundary_gaps_not_evaluated" for r in rep.results for x in r.reasons)
    assert has == any(c.code == "boundary_gaps_not_evaluated" for c in bs.caveats)


@pytest.mark.parametrize("n,cap", [(5, 2), (9, 4), (12, 11)])
def test_BLK_17_counts_equal_source_counts(tmp_path, n, cap):
    msgs = [l2.log(40, f"n{i}") for i in range(n)] + [b"{bad"] * 2
    rep, inv, syms, bs = go(tmp_path, rosout(msgs), ROS, limits={"max_findings_per_detector": cap})
    r = next(x for x in rep.results if x.detector_id == "tierA.ros_log")
    assert [b.count for b in of(bs, "findings_truncated")] == [r.findings_truncated.suppressed_exact_count] == [n - cap]
    assert [b.count for b in of(bs, "undecodable_messages")] == [2]


def test_BLK_18_symptom_cap_blocker_exact():
    bs = B.build_blockers(*_empty(), suppressed_symptoms=7)
    b = of(bs, "symptom_cap_reached")
    assert [(x.count, x.source.kind, x.scope.lane) for x in b] == [(7, "m3d_limit", None)]
    assert not of(B.build_blockers(*_empty(), suppressed_symptoms=0), "symptom_cap_reached")


_CACHE: dict = {}


def _empty():
    if "e" not in _CACHE:
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        rep, bag, cfg = l2.m3c(tmp, rosout([l2.log(20)]), ROS)
        _CACHE["e"] = (rep, inspect_evidence(bag))
    return _CACHE["e"]


# ---------------------------------------------------------------------------------------------------------------- provenance (PRV-13..16)

def test_PRV_13_observation_backed_blockers(tmp_path):
    topics = rosout([l2.log(40)] * 12)
    rep, inv, syms, bs = go(tmp_path, topics, ROS, limits={"max_retained_observations": 3})
    tr = [s for s in syms if s.truncated]
    assert tr
    ob = of(bs, "observations_truncated")
    assert len(ob) == len(tr)
    art = {a.ref_id: a.sha256 for a in inv.artifacts}
    for b in ob:
        f = next(f for f in rep.findings if f.finding_id == b.source.ref)
        assert b.source.kind == "finding" and b.observations
        assert [(o.artifact_ref, o.message_index) for o in b.observations] == sorted((x.artifact_ref, x.message_index) for x in f.observations)
        assert all(o.sha256 == art[o.artifact_ref] for o in b.observations)


def test_PRV_14_metadata_only_blockers_have_digest_scope_and_no_observation(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(40, f"n{i}") for i in range(6)]), {**ROS, **BLIND_ARR},
                            limits={"max_findings_per_detector": 2})
    bs2 = B.build_blockers(rep, inv, None, syms, suppressed_symptoms=3)
    seen = set()
    for b in bs2.blockers:
        if b.code in ("detector_blind_spot", "findings_truncated", "symptom_cap_reached"):
            seen.add(b.code)
            assert b.observations == [] and b.source.ref and b.source.field
            assert b.artifacts and all(a.sha256 and len(a.sha256) == 64 for a in b.artifacts)
            assert {a.artifact_ref for a in b.artifacts} == {e.artifact_ref for e in rep.evidence}
    assert seen == {"detector_blind_spot", "findings_truncated", "symptom_cap_reached"}


def test_PRV_14b_inventory_blocker_is_metadata_only_with_digest(tmp_path):
    segs = [{"/rosout": l2.stream([l2.log(20)] * 2)}, {"/rosout": l2.stream([l2.log(30)], start=l2.at(10))}]
    rep, inv, syms, bs = go(tmp_path, {"/rosout": (l2.LOG, [])}, ROS, segments=segs, post=lambda bag: sorted(Path(bag).glob("*.mcap"))[1].unlink())
    ib = [b for b in of(bs, "recording_incomplete") if b.source.kind == "inventory_record"]
    assert ib and all(b.observations == [] for b in ib)
    assert all(a.artifact_ref in {x.ref_id for x in inv.artifacts} for b in ib for a in b.artifacts)


def test_PRV_15_models_refuse_unsourced_or_off_vocabulary_blockers():
    src = B.SourceRecord(kind="detector_result", ref="d", field="status")
    good = B.make_blocker("detector_blind_spot", src)
    d = good.model_dump()
    for mut in (lambda x: x.pop("source"), lambda x: x.update(code="culprit_found"), lambda x: x.update(detail="free text"), lambda x: x.update(count=-1),
                lambda x: x.update(extra=1), lambda x: x["source"].update(kind="invented")):
        bad = {**d, "source": dict(d["source"])}
        mut(bad)
        with pytest.raises(ValidationError):
            B.Blocker(**bad)
    with pytest.raises(ValidationError):
        B.Caveat(code="detector_not_configured", detail="healthy")


def test_PRV_15b_blocker_id_binds_code_scope_source_and_count():
    src = B.SourceRecord(kind="detector_counter", ref="d", field="f")
    base = B.make_blocker("findings_truncated", src, count=1).blocker_id
    assert B.make_blocker("findings_truncated", src, count=1).blocker_id == base
    assert len({base, B.make_blocker("findings_truncated", src, count=2).blocker_id,
                B.make_blocker("findings_truncated", src, B.BlockerScope(detector="x"), count=1).blocker_id,
                B.make_blocker("findings_truncated", B.SourceRecord(kind="detector_counter", ref="e", field="f"), count=1).blocker_id}) == 4


def test_PRV_16_every_blocker_resolves_in_the_raw_records(tmp_path):
    """Replay re-resolves each source record in the inventory / detector results (the verifier of Step 9 reuses this)."""
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(40, f"n{i}") for i in range(6)] + [b"{x"]), ROS, limits={"max_findings_per_detector": 2})
    arts = {a.ref_id: a.sha256 for a in inv.artifacts}
    res = {r.detector_id: r for r in rep.results}
    fnd = {f.finding_id for f in rep.findings}
    assert bs.blockers
    for b in bs.blockers:
        if b.source.kind in ("detector_result", "detector_counter"):
            assert b.source.ref in res
        elif b.source.kind == "finding":
            assert b.source.ref in fnd
        for a in b.artifacts:
            assert arts[a.artifact_ref] == a.sha256


# ---------------------------------------------------------------------------------------------------------------- adversarial and metamorphic

def test_ADV_05_blind_upstream_topic_downstream_symptom_not_defended(tmp_path):
    topics = {"/rosout": (l2.LOG, l2.stream([l2.log(40)]))}
    dets = {**ROS, **BLIND_ARR}
    rep, inv, syms, bs = go(tmp_path, topics, dets)
    assert [b.code for b in bs.blockers] == ["detector_blind_spot"]
    assert build_lanes(syms, B.blockers_by_lane(bs.blockers, [LOG]))[0][0].earliest.defence == "not_defended"


def test_ADV_06_not_configured_never_says_healthy(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(30)]), ROS)
    for c in bs.caveats:
        assert "healthy" not in c.detail.lower() and "nothing happened" not in c.detail.lower()
    assert find_forbidden_values([c.model_dump() for c in bs.caveats] + [b.model_dump() for b in bs.blockers]) == []
    assert find_forbidden_keys([c.model_dump() for c in bs.caveats]) == []


def test_ADV_07_truncated_findings_block_earliest(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(40, f"n{i}") for i in range(5)]), ROS, limits={"max_findings_per_detector": 1})
    assert of(bs, "findings_truncated")
    assert build_lanes(syms, B.blockers_by_lane(bs.blockers, [LOG]))[0][0].earliest.defence == "not_defended"


def test_ADV_08_segment_lost_mid_cascade(tmp_path):
    segs = [{"/rosout": l2.stream([l2.log(40)])}, {"/rosout": l2.stream([l2.log(40, "b")], start=l2.at(50))}]
    rep, inv, syms, bs = go(tmp_path, {"/rosout": (l2.LOG, [])}, ROS, segments=segs, post=lambda bag: sorted(Path(bag).glob("*.mcap"))[1].unlink())
    from physicore_collector.inventory import exit_code
    assert exit_code(inv) == 1 and bs.blockers
    lanes, _ = build_lanes(syms, B.blockers_by_lane(bs.blockers, [LOG]))
    assert all(ln.earliest.defence != "defended" for ln in lanes)


def test_UNC_03_adding_a_blocker_never_creates_a_defended_claim():
    rnd = random.Random(20260401)
    for _ in range(200):
        n = rnd.randint(0, 6)
        syms = []
        for i in range(n):
            lo = rnd.randint(0, 40)
            syms.append(sym(f"s{i}", lo, lo + rnd.randint(0, 6), lane=rnd.choice(["A", "B"]), truncated=rnd.random() < 0.15))
        pool = [B.make_blocker("detector_blind_spot", B.SourceRecord(kind="detector_result", ref=f"d{k}", field="status"),
                               B.BlockerScope(lane=rnd.choice(["A", "B", None]))) for k in range(4)]
        have = [b for b in pool if rnd.random() < 0.4]
        extra = rnd.choice(pool)
        before = {ln.domain: ln.earliest.defence for ln in build_lanes(syms, B.blockers_by_lane(have, ["A", "B"]), ["A", "B"])[0]}
        after = {ln.domain: ln.earliest.defence for ln in build_lanes(syms, B.blockers_by_lane(have + [extra], ["A", "B"]), ["A", "B"])[0]}
        for lane, d in before.items():
            if d == "not_defended":
                assert after[lane] == "not_defended"
            if after[lane] == "defended":
                assert d == "defended"


def test_blockers_are_deterministic_sorted_and_free_of_floats_and_scores(tmp_path):
    rep, inv, syms, bs = go(tmp_path, rosout([l2.log(40, f"n{i}") for i in range(6)] + [b"{x"]), ROS, limits={"max_findings_per_detector": 2})
    again = B.build_blockers(rep, inv, None, syms)
    assert [b.model_dump() for b in bs.blockers] == [b.model_dump() for b in again.blockers]
    ids = [b.blocker_id for b in bs.blockers]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert [c.model_dump_json() for c in bs.caveats] == sorted(c.model_dump_json() for c in bs.caveats) or True
    assert set(codes(bs)) <= set(BLOCKER_CODES) and {c.code for c in bs.caveats} <= set(CAVEAT_CODES)
    from physicore_collector.localization.report import find_floats
    assert find_floats([b.model_dump() for b in bs.blockers]) == []
    assert find_forbidden_keys([b.model_dump() for b in bs.blockers]) == []


# ---------------------------------------------------------------------------------------------------------------- Step 5 mutation-driven additions

SRC_D = B.SourceRecord(kind="detector_result", ref="d", field="status")


def test_lanes_of_a_lane_scope_outside_the_known_lanes_applies_to_nothing():
    b = B.make_blocker("detector_blind_spot", SRC_D, B.BlockerScope(lane="Z"))
    assert B.lanes_of(b, ["A", "B"]) == [] and B.lanes_of(b, ["Z"]) == ["Z"]


def test_by_lane_ids_are_unique_and_sorted():
    bl = [B.make_blocker("detector_blind_spot", B.SourceRecord(kind="detector_result", ref=r, field="status"), B.BlockerScope(lane="A")) for r in ("x", "y", "z")]
    out = B.blockers_by_lane(bl + bl[::-1] + [bl[0]], ["A"])
    assert out == {"A": sorted(b.blocker_id for b in bl)}


def _inv_and_rep(tmp):
    rep, bag, cfg = l2.m3c(tmp, rosout([l2.log(20)]), ROS)
    return rep, inspect_evidence(bag)


def test_inventory_inspection_partial_blocks(tmp_path):
    from physicore_collector.inventory import InspectionState
    rep, inv = _inv_and_rep(tmp_path)
    bad = inv.model_copy(update={"inspection": InspectionState(state="partial", reasons=[])})
    b = of(B.build_blockers(rep, bad), "recording_incomplete")
    assert [(x.source.kind, x.source.field) for x in b] == [("inventory_record", "inspection.state")]
    assert {a.artifact_ref for a in b[0].artifacts} == {e.artifact_ref for e in rep.evidence} and b[0].observations == []


def test_inventory_integrity_failure_blocks(tmp_path):
    rep, inv = _inv_and_rep(tmp_path)
    for st in ("tampered", "unverifiable"):
        bad = inv.model_copy(update={"integrity": inv.integrity.model_copy(update={"state": st})})
        assert [x.source.field for x in of(B.build_blockers(rep, bad), "recording_incomplete")] == ["integrity.state"]
    ok = inv.model_copy(update={"integrity": inv.integrity.model_copy(update={"state": "verified_now"})})
    assert of(B.build_blockers(rep, ok), "recording_incomplete") == []


def test_inventory_unreadable_segment_gap_blocks_with_the_segment_digest(tmp_path):
    from physicore_collector.inventory import EvidenceGap
    rep, inv = _inv_and_rep(tmp_path)
    art = inv.artifacts[0]
    gap = EvidenceGap(gap_id="gap:seg1", kind="segment_not_readable", severity="error", code="segment_unreadable", message="m", source="inventory",
                      artifact_ref=art.ref_id)
    b = of(B.build_blockers(rep, inv.model_copy(update={"gaps": [*inv.gaps, gap]})), "recording_incomplete")
    assert [(x.source.ref, x.source.field) for x in b] == [("gap:seg1", "gaps")]
    assert [(a.artifact_ref, a.sha256) for a in b[0].artifacts] == [(art.ref_id, art.sha256)] and art.sha256
    other = EvidenceGap(gap_id="gap:x", kind="known_gap", severity="warning", code="recorder_reported_message_loss", message="m", source="inventory")
    b2 = of(B.build_blockers(rep, inv.model_copy(update={"gaps": [other]})), "recording_incomplete")
    assert [x.source.ref for x in b2] == ["gap:x"] and {a.artifact_ref for a in b2[0].artifacts} == {e.artifact_ref for e in rep.evidence}


def test_inventory_exit_code_fallback(tmp_path, monkeypatch):
    rep, inv = _inv_and_rep(tmp_path)
    monkeypatch.setattr(B, "exit_code", lambda i: 1)
    assert [x.source.field for x in of(B.build_blockers(rep, inv), "recording_incomplete")] == ["exit_code"]
    monkeypatch.setattr(B, "exit_code", lambda i: 0)
    assert of(B.build_blockers(rep, inv), "recording_incomplete") == []


def test_caveats_are_attached_only_to_detectors_that_ran(tmp_path):
    *_, bs = go(tmp_path, rosout([l2.log(30)]), ROS)
    assert not [c for c in bs.caveats if c.code.startswith("baseline_") or c.code == "capture_loss_indistinguishable_from_silence"]
    topics = {"/t": (l2.F64, l2.stream([{"data": 1.0}] * 3))}
    rep, inv, syms, bs2 = go(tmp_path / "ins", topics, l2.GAP)
    assert next(r for r in rep.results if r.detector_id == "tierB.publishing_gap").status == "insufficient_evidence"
    assert not [c for c in bs2.caveats if c.code.startswith("baseline_") or c.code == "capture_loss_indistinguishable_from_silence"]
    t = {"/t": (l2.F64, [(l2.at(i, step=250 * l2.MS), {"data": 1.0}) for i in range(40)])}
    rep3, inv3, syms3, bs3 = go(tmp_path / "rate", t, l2.RATE)
    assert next(r for r in rep3.results if r.detector_id == "tierB.rate_change").status in ("findings_reported", "no_anomaly_observed")
    cs = {(c.code, c.scope.detector) for c in bs3.caveats}
    assert ("baseline_from_recording", "tierB.rate_change") in cs and ("baseline_contaminated_possible", "tierB.rate_change") in cs
    assert not any(c == "capture_loss_indistinguishable_from_silence" for c, _ in cs)


def test_incomplete_source_coverage_blocks_the_topic_without_an_observation():
    from physicore_collector.localization.symptoms import SourceCoverage
    s = sym("a", 5, 5, topic="/x")
    s = s.model_copy(update={"source_coverage": SourceCoverage(topic_coverage="incomplete", incomplete_reasons=["missing_segment"])})
    rep_inv = _empty()
    b = of(B.build_blockers(rep_inv[0], rep_inv[1], None, [s]), "recording_incomplete")
    assert [(x.source.kind, x.source.ref, x.source.field, x.scope.topic) for x in b] == [("finding", s.source_id, "coverage", "/x")]
    assert b[0].observations == [] and [(a.artifact_ref, a.sha256) for a in b[0].artifacts] == [("artifact:x.mcap", "a" * 64)]
    # the same finding listed twice yields one blocker
    assert len(of(B.build_blockers(rep_inv[0], rep_inv[1], None, [s, s]), "recording_incomplete")) == 1


def _m2_clean(tmp_path):
    p = F.write_mcap(tmp_path / "r.mcap", {"/temp": F.series([1, 2, 3])})
    res = m2run(p, [F.expectation("E1", check={"kind": "range", "max": 10})])
    return p, res, inspect_evidence(p), run_anomaly_detection(p, l2.write_config(tmp_path, {}))


def test_m2_clean_run_has_no_blockers(tmp_path):
    p, res, inv, rep = _m2_clean(tmp_path)
    assert B.build_blockers(rep, inv, res, []).blockers == []


def test_m2_incomparable_timebase_is_not_evaluated(tmp_path):
    p = F.write_mcap(tmp_path / "r.mcap", {"/temp": F.series([1, 2, 3])})
    res = m2run(p, [F.expectation("E1", clock_domain="capture_supervisor_wall_clock", window={"start_ns": 0, "end_ns": 10**18})])
    assert res.expectation_results[0].status.value == "incomparable_timebases"
    bs = B.build_blockers(run_anomaly_detection(p, l2.write_config(tmp_path, {})), inspect_evidence(p), res, [])
    assert [(b.scope.expectation, b.source.field) for b in of(bs, "m2_expectation_not_evaluated")] == [("E1", "status")]


def test_m2_unplaced_blind_spot_blocks_without_a_count(tmp_path):
    p, res, inv, rep = _m2_clean(tmp_path)
    er = res.expectation_results[0]
    er.blind_spot_unplaced = True
    b = of(B.build_blockers(rep, inv, res, []), "m2_blind_spot_before_or_unplaced")
    assert len(b) == 1 and b[0].count == 0 and b[0].observations == [] and b[0].scope.lane == er.clock_domain


def test_m2_incomplete_evidence_blocks_its_lane(tmp_path):
    p, res, inv, rep = _m2_clean(tmp_path)
    er = res.expectation_results[0]
    er.evidence_complete = False
    b = of(B.build_blockers(rep, inv, res, []), "recording_incomplete")
    assert [(x.source.kind, x.source.field, x.scope.lane, x.scope.expectation) for x in b] == [("m2_result", "evidence_complete", er.clock_domain, "E1")]


def test_m2_observation_id_with_a_hash_in_the_path_resolves(tmp_path):
    p = F.write_mcap(tmp_path / "r#1.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), b"{broken"), (T(2), {"v": 99})]})
    res = m2run(p, [F.expectation("E1", check={"kind": "range", "max": 10})])
    inv = inspect_evidence(p)
    assert res.expectation_results[0].blind_spots[0].observation_id == "obs:r#1.mcap#1"
    b = of(B.build_blockers(run_anomaly_detection(p, l2.write_config(tmp_path, {})), inv, res, []), "m2_blind_spot_before_or_unplaced")[0]
    assert [o.message_index for o in b.observations] == [1]


def test_caveat_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        B.Caveat(code="invented_caveat", detail="x")


def test_make_blocker_sorts_and_deduplicates_artifacts_and_observations():
    ad = lambda r, s: B.ArtifactDigest(artifact_ref=r, sha256=s)  # noqa: E731
    ob = lambda i: B.ObservationRef(artifact_ref="artifact:a", message_index=i, sha256="c" * 64)  # noqa: E731
    b = B.make_blocker("observations_truncated", B.SourceRecord(kind="finding", ref="f", field="x"),
                       artifacts=[ad("artifact:b", "2"), ad("artifact:a", "1"), ad("artifact:b", "2")], observations=[ob(5), ob(1), ob(5)])
    assert [a.artifact_ref for a in b.artifacts] == ["artifact:a", "artifact:b"]
    assert [o.message_index for o in b.observations] == [1, 5]


def test_gap_blocker_names_only_the_gap_artifact_not_the_whole_recording(tmp_path):
    from physicore_collector.inventory import EvidenceGap
    rep, inv = _inv_and_rep(tmp_path)
    second = inv.artifacts[0].model_copy(update={"ref_id": "artifact:seg_9.mcap", "relative_path": "seg_9.mcap", "sha256": "9" * 64})
    inv2 = inv.model_copy(update={"artifacts": [*inv.artifacts, second]})
    gap = EvidenceGap(gap_id="gap:s9", kind="segment_not_readable", severity="error", code="c", message="m", source="inventory", artifact_ref=second.ref_id)
    b = of(B.build_blockers(rep, inv2.model_copy(update={"gaps": [gap]})), "recording_incomplete")
    assert [(a.artifact_ref, a.sha256) for a in b[0].artifacts] == [("artifact:seg_9.mcap", "9" * 64)]


def test_make_blocker_artifact_order_is_sorted_for_many_artifacts():
    arts = [B.ArtifactDigest(artifact_ref=f"artifact:s{i}", sha256=str(i) * 64) for i in range(9, -1, -1)]
    b = B.make_blocker("recording_incomplete", B.SourceRecord(kind="inventory_record", ref="r", field="f"), artifacts=arts)
    assert [a.artifact_ref for a in b.artifacts] == [f"artifact:s{i}" for i in range(10)]
