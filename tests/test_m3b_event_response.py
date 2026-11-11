"""M3b: cross-topic event -> response correlation, prerequisite freshness (max_age_ns), header.stamp as its own clock domain.

All synthetic and deterministic. Most tests use JSON payloads (so they run with no optional extra); the CDR ones need extras `ros2` + `xcheck`.
Nothing here shows behaviour on a real robot recording (none was available)."""

import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from mcap.reader import make_reader
from typer.testing import CliRunner

from physicore_collector.cli import app
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator import Status, investigate, investigation_json, parse_contract
from physicore_collector.investigator import cdr
from tests.investigator_fixtures import BASE, MS, SEC, contract, expectation, write_json_bag, write_mcap

runner = CliRunner()
ROOT = Path(__file__).parent.parent
HAVE_DECODER = cdr.decoder_info().available
needs_decoder = pytest.mark.skipif(not HAVE_DECODER, reason="optional extra 'ros2' is not installed")


def at(ms: int) -> int:
    return BASE + ms * MS


def er(eid="ER", *, trig=("/cmd", "v", "eq", "STOP"), resp=("/state", "v", "eq", "STOPPED"), deadline=300, **kw):
    """An event_response expectation: trigger on trig (topic, signal, op, value), response on resp, deadline in ms."""
    r = {"topic": resp[0], "signal": resp[1], "op": resp[2], "value": resp[3]}
    check = {"kind": "event_response", "trigger_op": trig[2], "trigger_value": trig[3], "response": r, "deadline_ns": deadline * MS}
    return expectation(eid, topic=trig[0], signal=trig[1], check=check, **kw)


def run(path, exps):
    return investigate(inspect_evidence(path), parse_contract(contract(exps)))


def res(r, eid="ER"):
    return next(x for x in r.expectation_results if x.expectation_id == eid)


def codes(r, eid="ER"):
    return {b.code for b in res(r, eid).blind_spots}


def rec(tmp_path, cmd, state, name="r.mcap", extra=None):
    streams = {"/cmd": [(at(t), {"v": v}) for t, v in cmd], "/state": [(at(t), {"v": v}) for t, v in state]}
    streams.update(extra or {})
    return write_mcap(tmp_path / name, streams)


# a recording that covers 0..1000 ms on both topics, so absence of a response by a 300 ms deadline IS established
CMD = [(0, "IDLE"), (100, "STOP"), (900, "IDLE")]


def ok_state(resp_at):
    return [(0, "RUN"), (resp_at, "STOPPED"), (1000, "STOPPED")]


# ------------------------------------------------------------------ 1. deadlines

def test_response_inside_the_deadline_is_no_divergence(tmp_path):
    r = run(rec(tmp_path, CMD, ok_state(250)), [er()])
    x = res(r)
    assert r.status is Status.NO_DIVERGENCE and (x.trigger_event_count, x.response_matched_count) == (1, 1)


def test_response_exactly_at_the_deadline_is_inclusive(tmp_path):
    assert run(rec(tmp_path, CMD, ok_state(400)), [er()]).status is Status.NO_DIVERGENCE  # trigger 100 + deadline 300 = 400


def test_response_one_nanosecond_late_is_a_divergence(tmp_path):
    streams = {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
               "/state": [(at(0), {"v": "RUN"}), (at(400) + 1, {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})]}
    r = run(write_mcap(tmp_path / "r.mcap", streams), [er()])
    f = r.first_divergence
    assert r.status is Status.DIVERGENCE_FOUND and f.time.ns == at(400) and f.time.kind == "deadline_expiry"
    assert "exceeds the deadline 300000000 ns by 1 ns" in f.discrepancy and f.expectation_kind == "event_response"
    assert [s.role for s in f.sources] == ["trigger_event", "late_response"]


def test_missing_response_is_a_divergence_at_the_deadline(tmp_path):
    state = [(0, "RUN"), (500, "RUN"), (1000, "RUN")]
    r = run(rec(tmp_path, CMD, state), [er()])
    f = r.first_divergence
    assert r.status is Status.DIVERGENCE_FOUND and f.time.ns == at(400)
    assert "no matching response was observed in the recorded stream by" in f.discrepancy and [s.role for s in f.sources] == ["trigger_event"]
    assert [s.role for s in f.sources] == ["trigger_event"]
    assert res(r).trigger_event_count == 1 and res(r).response_matched_count == 0


def test_a_deadline_beyond_the_evidence_is_not_a_missing_response(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP")]
    state = [(0, "RUN"), (200, "RUN")]  # the recording ends at 200 ms; the deadline is 400 ms
    r = run(rec(tmp_path, cmd, state), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "deadline_beyond_covered_evidence" in codes(r)


def test_only_the_late_event_is_reported_when_an_earlier_one_was_answered(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (200, "IDLE"), (600, "STOP"), (700, "IDLE")]
    state = [(0, "RUN"), (150, "STOPPED"), (300, "RUN"), (1000, "RUN"), (1100, "STOPPED")]
    r = run(rec(tmp_path, cmd, state), [er()])
    assert res(r).trigger_event_count == 2 and res(r).response_matched_count == 1
    f = r.first_divergence
    assert f.time.ns == at(900) and f.observations[0].timestamp_ns == at(600)


def test_trigger_is_an_entry_not_every_sample_that_satisfies_it(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (150, "STOP"), (200, "STOP"), (900, "IDLE")]
    x = res(run(rec(tmp_path, cmd, ok_state(250)), [er()]))
    assert x.trigger_event_count == 1  # one entry, though three samples satisfy the trigger condition


def test_a_second_entry_after_leaving_the_condition_is_a_second_event(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (200, "IDLE"), (300, "STOP"), (400, "IDLE")]
    state = [(0, "RUN"), (150, "STOPPED"), (250, "RUN"), (350, "STOPPED"), (1000, "STOPPED")]
    x = res(run(rec(tmp_path, cmd, state), [er()]))
    assert x.trigger_event_count == 2 and x.response_matched_count == 2


def test_a_trigger_that_never_happens_is_not_a_pass(tmp_path):
    r = run(rec(tmp_path, [(0, "IDLE"), (900, "IDLE")], ok_state(250)), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and res(r).reason_code == "no_trigger_event_in_window"


def test_numeric_conditions_and_the_window_apply_to_the_trigger(tmp_path):
    streams = {"/cmd": [(at(0), {"v": 0}), (at(100), {"v": 5}), (at(500), {"v": 0}), (at(600), {"v": 9})],
               "/state": [(at(0), {"v": 0}), (at(200), {"v": 1}), (at(300), {"v": 0}), (at(1500), {"v": 0})]}
    p = write_mcap(tmp_path / "r.mcap", streams)
    e = er(trig=("/cmd", "v", "ge", 5), resp=("/state", "v", "ge", 1), deadline=200)
    r = run(p, [e])
    assert res(r).trigger_event_count == 2 and r.first_divergence.observations[0].timestamp_ns == at(600)
    e2 = er(trig=("/cmd", "v", "ge", 5), resp=("/state", "v", "ge", 1), deadline=200, window={"start_ns": at(0), "end_ns": at(300)})
    assert run(p, [e2]).status is Status.NO_DIVERGENCE  # the late second event is outside the window


# ------------------------------------------------------------------ 2. equal timestamps and ordering

def test_a_response_at_the_same_timestamp_as_the_trigger_is_ambiguous_not_ordered(tmp_path):
    state = [(0, "RUN"), (100, "STOPPED"), (1000, "STOPPED")]  # STOPPED arrives with the very same timestamp as STOP
    r = run(rec(tmp_path, CMD, state), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"event_response_ordering_ambiguous"}


def test_a_same_instant_response_cannot_be_rescued_by_a_later_matching_sample(tmp_path):
    # STOPPED at 100 (same instant) and again at 200: the state may have pre-dated the trigger, so the later sample is not accepted as the response
    state = [(0, "RUN"), (100, "STOPPED"), (200, "STOPPED"), (1000, "STOPPED")]
    r = run(rec(tmp_path, CMD, state), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "event_response_ordering_ambiguous" in codes(r)


def test_a_same_instant_sample_that_does_not_match_is_harmless(tmp_path):
    state = [(0, "RUN"), (100, "RUN"), (250, "STOPPED"), (1000, "STOPPED")]
    assert run(rec(tmp_path, CMD, state), [er()]).status is Status.NO_DIVERGENCE


def test_file_order_never_decides_an_equal_timestamp(tmp_path):
    a = write_mcap(tmp_path / "a.mcap", {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
                                       "/state": [(at(0), {"v": "RUN"}), (at(100), {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})]})
    b = write_mcap(tmp_path / "b.mcap", {"/state": [(at(0), {"v": "RUN"}), (at(100), {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})],
                                       "/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})]})
    ra, rb = run(a, [er()]), run(b, [er()])
    assert ra.status is rb.status is Status.INSUFFICIENT_EVIDENCE and codes(ra) == codes(rb)


def test_two_trigger_samples_at_one_instant_are_ambiguous(tmp_path):
    streams = {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
               "/state": [(at(0), {"v": "RUN"}), (at(250), {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})]}
    r = run(write_mcap(tmp_path / "r.mcap", streams), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "event_ordering_ambiguous" in codes(r)


def test_a_response_state_that_already_held_is_not_attributed_to_the_trigger(tmp_path):
    state = [(0, "STOPPED"), (150, "STOPPED"), (1000, "STOPPED")]
    r = run(rec(tmp_path, CMD, state), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"response_already_satisfied"}


def test_ties_between_two_expectations_stay_tied(tmp_path):
    state = [(0, "RUN"), (1000, "RUN")]
    r = run(rec(tmp_path, CMD, state), [er("A"), er("B")])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"
    assert {f.ordering for f in r.candidates} == {"tied_for_earliest"}


# ------------------------------------------------------------------ 3. missing and incomplete evidence

def test_missing_response_topic_is_missing_evidence_not_a_missing_response(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"})]})
    x = res(run(p, [er()]))
    assert x.status is Status.INSUFFICIENT_EVIDENCE and x.reason_code == "required_observations_missing" and "/state" in x.reason


def test_missing_trigger_topic(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/state": [(at(0), {"v": "RUN"})]})
    assert res(run(p, [er()])).reason_code == "required_observations_missing"


def test_an_undecodable_response_sample_inside_the_deadline_blocks_a_missing_response_claim(tmp_path):
    streams = {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
               "/state": [(at(0), {"v": "RUN"}), (at(200), b"{not json"), (at(1000), {"v": "RUN"})]}
    r = run(write_mcap(tmp_path / "r.mcap", streams), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"payload_undecodable"} and r.candidates == []


def test_a_good_response_settles_the_event_despite_an_undecodable_neighbour(tmp_path):
    streams = {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
               "/state": [(at(0), {"v": "RUN"}), (at(150), b"{not json"), (at(250), {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})]}
    r = run(write_mcap(tmp_path / "r.mcap", streams), [er()])
    assert r.status is Status.NO_DIVERGENCE and res(r).blind_spot_count == 0


def test_a_response_value_of_the_wrong_type_is_a_blind_spot(tmp_path):
    state = [(0, "RUN"), (200, 7), (1000, "RUN")]
    r = run(rec(tmp_path, CMD, state), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "value_type_mismatch" in codes(r)


def test_an_unknown_state_before_the_trigger_blocks_the_conclusion(tmp_path):
    streams = {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
               "/state": [(at(0), b"garbage"), (at(250), {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})]}
    r = run(write_mcap(tmp_path / "r.mcap", streams), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "response_state_unknown" in codes(r)


def test_a_truncated_recording_never_yields_no_divergence(tmp_path):
    from tests.investigator_fixtures import build_json_mcap
    streams = {"/cmd": [(at(i * 10), {"v": "STOP" if i == 3 else "IDLE"}) for i in range(300)],
               "/state": [(at(i * 10), {"v": "STOPPED" if i >= 5 else "RUN"}) for i in range(300)]}
    full = build_json_mcap(streams, chunk_size=1024)
    p = tmp_path / "t.mcap"
    p.write_bytes(full[: int(len(full) * 0.55)])
    r = run(p, [er(deadline=100)])
    assert r.status is not Status.NO_DIVERGENCE and r.evidence.evidence_complete is False


def test_a_changed_file_is_not_trusted(tmp_path):
    p = rec(tmp_path, CMD, ok_state(250))
    inv = inspect_evidence(p)
    b = bytearray(p.read_bytes())
    b[len(b) // 2] ^= 0xFF
    p.write_bytes(bytes(b))
    r = investigate(inv, parse_contract(contract([er()])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE


# ------------------------------------------------------------------ 4. prerequisites: max_age_ns

def prereq_recording(tmp_path, mode_at):
    """/mode is RUNNING from `mode_at` ms on (one message only); a range check on /temp is exercised at 1000 ms."""
    streams = {"/mode": [(at(mode_at), {"state": "RUNNING"})], "/temp": [(at(1000), {"v": 99}), (at(1100), {"v": 1})]}
    return write_mcap(tmp_path / "p.mcap", streams)


def rng(max_age=None):
    pre = {"topic": "/mode", "signal": "state", "op": "eq", "value": "RUNNING"}
    if max_age is not None:
        pre["max_age_ns"] = max_age * MS
    return expectation("P", topic="/temp", signal="v", check={"kind": "range", "max": 10}, applies_when=[pre])


def test_without_max_age_an_old_prerequisite_still_holds(tmp_path):
    assert run(prereq_recording(tmp_path, 0), [rng()]).status is Status.DIVERGENCE_FOUND  # Phase 2 behaviour, unchanged


def test_a_fresh_prerequisite_applies(tmp_path):
    assert run(prereq_recording(tmp_path, 600), [rng(500)]).status is Status.DIVERGENCE_FOUND  # age 400 ms <= 500 ms


def test_prerequisite_age_equal_to_the_limit_is_fresh_and_one_ns_older_is_stale(tmp_path):
    assert run(prereq_recording(tmp_path, 500), [rng(500)]).status is Status.DIVERGENCE_FOUND  # age exactly 500 ms
    streams = {"/mode": [(at(500) - 1, {"state": "RUNNING"})], "/temp": [(at(1000), {"v": 99}), (at(1100), {"v": 1})]}
    r = run(write_mcap(tmp_path / "q.mcap", streams), [rng(500)])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "prerequisite_stale" in codes(r, "P")


def test_a_stale_prerequisite_is_a_blind_spot_not_a_silent_skip(tmp_path):
    r = run(prereq_recording(tmp_path, 0), [rng(500)])
    x = res(r, "P")
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r, "P") == {"prerequisite_stale"} and x.not_applicable_count == 0
    assert r.candidates == [] and x.reason_code in ("no_evaluable_observations_in_window", "unevaluated_observations_in_window")
    stale = x.blind_spots[0]
    assert stale.observation_id is not None and stale.time_ns == at(1000)


def test_a_stale_prerequisite_blocks_the_earliest_claim_of_another_expectation(tmp_path):
    p = write_mcap(tmp_path / "s.mcap", {"/mode": [(at(0), {"state": "RUNNING"})], "/temp": [(at(500), {"v": 1}), (at(1000), {"v": 99})],
                                       "/other": [(at(1200), {"v": 50})]})
    other = expectation("O", topic="/other", signal="v", check={"kind": "range", "max": 10})
    r = run(p, [rng(100), other])
    # O's mismatch at 1200 ms is established, but P could not be evaluated at 500 ms (stale prerequisite): an earlier mismatch cannot be excluded
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"
    assert [f.expectation_id for f in r.candidates] == ["O"] and "prerequisite_stale" in codes(r, "P")


def test_event_response_uses_prerequisite_freshness_at_the_trigger(tmp_path):
    pre = {"topic": "/mode", "signal": "state", "op": "eq", "value": "RUNNING", "max_age_ns": 200 * MS}
    cmd = {"/cmd": [(at(0), {"v": "IDLE"}), (at(300), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
           "/state": [(at(0), {"v": "RUN"}), (at(1000), {"v": "RUN"})]}
    fresh = write_mcap(tmp_path / "f.mcap", {**cmd, "/mode": [(at(200), {"state": "RUNNING"})]})
    stale = write_mcap(tmp_path / "s.mcap", {**cmd, "/mode": [(at(50), {"state": "RUNNING"})]})
    assert run(fresh, [er(applies_when=[pre])]).status is Status.DIVERGENCE_FOUND  # applies; response never comes
    r = run(stale, [er(applies_when=[pre])])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "prerequisite_stale" in codes(r)


def test_prerequisite_at_the_same_instant_stays_ambiguous_with_max_age(tmp_path):
    streams = {"/mode": [(at(1000), {"state": "RUNNING"})], "/temp": [(at(1000), {"v": 99}), (at(1100), {"v": 1})]}
    r = run(write_mcap(tmp_path / "m.mcap", streams), [rng(500)])
    assert "prerequisite_ordering_ambiguous" in codes(r, "P")


@pytest.mark.parametrize("bad", [0, -5, 1.5, "10", True])
def test_max_age_must_be_a_positive_integer(bad):
    e = rng(1)
    e["applies_when"][0]["max_age_ns"] = bad
    assert parse_contract(contract([e])).contract is None


def test_max_age_is_absent_from_the_canonical_contract_when_not_used():
    a = parse_contract(contract([rng()]))
    assert "max_age_ns" not in json.dumps(a.contract.model_dump(mode="json"))
    b = parse_contract(contract([rng(5)]))
    assert a.sha256 != b.sha256 and "max_age_ns" in json.dumps(b.contract.model_dump(mode="json"))


# ------------------------------------------------------------------ 5. header.stamp as a separate clock domain

def stamped(sec, nsec, **fields):
    return {"header": {"stamp": {"sec": sec, "nanosec": nsec}}, **fields}


def hs_expect(**kw):
    return expectation("H", topic="/t", signal="v", time_source="header_stamp", clock_domain="payload_stamp:/t:header.stamp",
                       check={"kind": "range", "max": 10}, **kw)


def test_header_stamp_is_converted_exactly(tmp_path):
    sec, ns = 1_700_000_123, 987_654_321
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), stamped(sec, ns, v=1)), (at(10), stamped(sec, 999_999_999, v=99))]})
    r = run(p, [hs_expect()])
    f = r.first_divergence
    assert f.time.ns == sec * SEC + 999_999_999 and f.time.clock_domain == "payload_stamp:/t:header.stamp"
    o = f.observations[0]
    assert o.timestamp_ns == sec * SEC + 999_999_999 and o.timestamp_basis == "header_stamp:header.stamp" and o.clock_domain == f.time.clock_domain
    assert o.message_index == 1 and f.sources[0].artifact_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_header_stamp_is_independent_of_log_time(tmp_path):
    # log_time says the bad value came first; the stamps say it came second: the stamp clock decides inside its own domain
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), stamped(2000, 0, v=99)), (at(10), stamped(1000, 5, v=98))]})
    f = run(p, [hs_expect()]).first_divergence
    assert f.time.ns == 1000 * SEC + 5 and f.observations[0].message_index == 1


def test_a_header_stamp_expectation_does_not_use_log_time_windows(tmp_path):
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), stamped(1000, 5, v=99))]})
    e = hs_expect()
    e["clock_domain"] = "mcap_log_time"
    r = run(p, [e])
    assert r.status is Status.INCOMPARABLE_TIMEBASES and res(r, "H").reason_code == "window_clock_domain_differs"


def test_a_window_in_the_stamp_domain_is_applied_to_stamps_not_log_time(tmp_path):
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), stamped(100, 0, v=99)), (at(10), stamped(5000, 0, v=98))]})
    r = run(p, [hs_expect(window={"start_ns": 4000 * SEC})])
    assert r.first_divergence.time.ns == 5000 * SEC


@pytest.mark.parametrize("stamp", [
    {"sec": 5, "nanosec": 1_000_000_000}, {"sec": 5, "nanosec": 2_000_000_000}, {"sec": -1, "nanosec": 0}, {"sec": 5, "nanosec": -1},
    {"sec": 1.5, "nanosec": 0}, {"sec": 5, "nanosec": 0.5}, {"sec": "5", "nanosec": 0}, {"sec": 5}, {"nanosec": 5},
    {"sec": True, "nanosec": 1}, "12345", None, [1, 2],
])
def test_invalid_stamps_are_blind_spots_never_converted(tmp_path, stamp):
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), {"header": {"stamp": stamp}, "v": 99}), (at(10), stamped(100, 1, v=1))]})
    r = run(p, [hs_expect()])
    x = res(r, "H")
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "timestamp_missing" in {b.code for b in x.blind_spots} and x.blind_spot_unplaced is True


def test_a_zero_stamp_is_a_valid_time_by_default(tmp_path):
    p = write_mcap(tmp_path / "z.mcap", {"/t": [(at(0), stamped(0, 0, v=99)), (at(10), stamped(0, 1, v=98)), (at(20), stamped(1, 0, v=97))]})
    r = run(p, [hs_expect()])
    f = r.first_divergence
    assert f.time.ns == 0 and f.observations[0].timestamp_ns == 0 and res(r, "H").blind_spot_count == 0  # sim time zero is a time, not 'unset'
    assert res(r, "H").evaluated_observation_count == 3


def test_json_stamps_state_that_their_ros_type_could_not_be_verified(tmp_path):
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), stamped(100, 5, v=99))]})
    r = run(p, [hs_expect()])
    x = res(r, "H")
    assert x.stamp_type_check.startswith("structure_only") and "cannot be verified" in x.stamp_type_check and "builtin_interfaces" not in x.stamp_type_check.split("cannot")[0]
    assert any(u.startswith("structure_only") for u in r.first_divergence.uncertainty)
    assert any("ROS type of a stamp field cannot be verified" in n for n in r.not_established)
    assert "schema_verified" not in investigation_json(r)


def test_the_largest_legal_stamp_is_exact(tmp_path):
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), stamped(2_147_483_647, 999_999_999, v=99))]})
    assert run(p, [hs_expect()]).first_divergence.time.ns == 2_147_483_647 * SEC + 999_999_999


def test_a_custom_stamp_field_name(tmp_path):
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), {"meas": {"t": {"sec": 7, "nanosec": 8}}, "v": 99})]})
    e = expectation("H", topic="/t", signal="v", time_source="header_stamp", time_field="meas.t", clock_domain="payload_stamp:/t:meas.t",
                    check={"kind": "range", "max": 10})
    assert run(p, [e]).first_divergence.time.ns == 7 * SEC + 8


def test_header_stamp_contract_validation():
    e = hs_expect()
    assert parse_contract(contract([e])).contract.expectations[0].time_field == "header.stamp"
    bad = hs_expect(applies_when=[{"topic": "/m", "signal": "s", "op": "eq", "value": 1}])
    assert parse_contract(contract([bad])).contract is None  # prerequisites need log_time
    bad2 = expectation("H", time_field="header.stamp")
    assert parse_contract(contract([bad2])).contract is None


# ------------------------------------------------------------------ 6. event_response is receipt-time only (mcap_log_time)

def two_clock_recording(tmp_path, *, trig_stamp, resp_stamp, trig_log=100, resp_log=200, name="c.mcap"):
    """/cmd and /state carry header stamps from a clock unrelated to log_time."""
    streams = {"/cmd": [(at(0), stamped(1000, 0, v="IDLE")), (at(trig_log), stamped(*trig_stamp, v="STOP")), (at(900), stamped(1010, 0, v="IDLE"))],
               "/state": [(at(0), stamped(1000, 0, v="RUN")), (at(resp_log), stamped(*resp_stamp, v="STOPPED")), (at(1000), stamped(1020, 0, v="STOPPED"))]}
    return write_mcap(tmp_path / name, streams)


@pytest.mark.parametrize("mutate, why", [
    (lambda e: e.update(time_source="header_stamp", clock_domain="payload_stamp:/cmd:header.stamp"), "trigger on a header stamp"),
    (lambda e: e.update(time_source="payload_field", time_field="t", clock_domain="payload_stamp:/cmd:t"), "trigger on a payload field"),
    (lambda e: e["check"]["response"].update(time_source="header_stamp"), "response clock selectable"),
    (lambda e: e["check"]["response"].update(time_source="log_time"), "response clock field exists at all"),
    (lambda e: e["check"]["response"].update(time_field="header.stamp"), "response time_field"),
    (lambda e: e["check"].update(clock_assertion={"domains": ["a", "b"], "reference": "x"}), "cross-payload clock attestation"),
])
def test_event_response_cannot_use_payload_clocks_or_attestations(mutate, why):
    e = er()
    mutate(e)
    assert parse_contract(contract([e])).contract is None, why


def test_a_wrong_declared_window_clock_is_still_incomparable_not_silently_accepted(tmp_path):
    e = er()
    e["clock_domain"] = "payload_stamp:/cmd:header.stamp"
    r = run(rec(tmp_path, CMD, ok_state(250)), [e])
    assert r.status is Status.INCOMPARABLE_TIMEBASES


def test_header_stamps_in_the_recording_never_influence_event_response(tmp_path):
    # by receipt the response arrives 100 ms after the trigger (on time); the stamps would say 500 ms (late), or even negative: they are not read
    for resp_stamp in ((1000, 600 * MS), (999, 0), (1000, 0)):
        p = two_clock_recording(tmp_path, trig_stamp=(1000, 100 * MS), resp_stamp=resp_stamp, name=f"c{resp_stamp[0]}_{resp_stamp[1]}.mcap")
        r = run(p, [er(deadline=300)])
        assert r.status is Status.NO_DIVERGENCE and res(r).response_matched_count == 1
        assert r.evidence.payload_decoding is None  # no payload clock was even extracted


def test_late_by_receipt_is_a_divergence_whatever_the_stamps_say(tmp_path):
    p = two_clock_recording(tmp_path, trig_stamp=(1000, 100 * MS), resp_stamp=(1000, 200 * MS), trig_log=100, resp_log=800)
    f = run(p, [er(deadline=300)]).first_divergence
    assert f is not None and {o.clock_domain for o in f.observations} == {"mcap_log_time"} and f.time.clock_domain == "mcap_log_time"
    assert all(o.timestamp_basis == "mcap_log_time" for o in f.observations)


def test_every_event_response_result_is_labelled_as_receipt_time(tmp_path):
    ok = run(rec(tmp_path, CMD, ok_state(250)), [er()])
    assert any("receipt time" in x and "not the time an event physically occurred" in x for x in ok.not_established)
    bad = run(rec(tmp_path, CMD, [(0, "RUN"), (1000, "RUN")], "b.mcap"), [er()])
    assert any("RECEIPT" in u for u in bad.first_divergence.uncertainty)
    assert bad.first_divergence.rule.parameters["clock"].startswith("mcap_log_time (recorder receipt time)")


def test_no_offset_is_inferred_between_stamps_and_log_time(tmp_path):
    # stamps exactly 1000 s ahead of log_time: nothing in the output relates the two
    p = two_clock_recording(tmp_path, trig_stamp=(1000, 100 * MS), resp_stamp=(1000, 200 * MS))
    text = investigation_json(run(p, [er()]))
    assert "payload_stamp" not in text and "payload_decoding" not in text and '"clock_domain": "mcap_log_time"' in text


# ------------------------------------------------------------------ 6b. entry into the trigger condition must be observed

def test_recording_that_begins_with_the_trigger_condition_already_true_is_not_an_event(tmp_path):
    cmd = [(0, "STOP"), (100, "STOP"), (900, "IDLE")]  # the condition is true from the first sample on
    state = [(0, "RUN"), (250, "STOPPED"), (1000, "STOPPED")]
    r = run(rec(tmp_path, cmd, state), [er()])
    x = res(r)
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"event_entry_unknown"}
    assert x.trigger_event_count == 0 and x.response_matched_count == 0 and r.candidates == []  # not a confirmed event, so no response was claimed either
    b = x.blind_spots[0]
    assert b.time_ns == at(0) and message_order(tmp_path / "r.mcap")[int(b.observation_id.split("#")[1])][0] in ("/cmd", "/state")


def test_a_first_trigger_sample_that_is_true_with_no_response_is_not_a_confirmed_missing_response(tmp_path):
    cmd = [(0, "STOP"), (900, "IDLE")]
    r = run(rec(tmp_path, cmd, [(0, "RUN"), (1000, "RUN")]), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.candidates == [] and codes(r) == {"event_entry_unknown"}


def test_first_sample_false_then_true_is_a_confirmed_event(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (900, "IDLE")]
    x = res(run(rec(tmp_path, cmd, ok_state(250)), [er()]))
    assert x.trigger_event_count == 1 and x.response_matched_count == 1 and x.blind_spot_count == 0


def test_a_later_false_to_true_transition_is_detected_after_an_unknown_start(tmp_path):
    cmd = [(0, "STOP"), (200, "IDLE"), (500, "STOP"), (800, "IDLE")]  # the first STOP is unknown, the second is a real entry
    state = [(0, "RUN"), (100, "RUN"), (600, "STOPPED"), (1000, "STOPPED")]
    r = run(rec(tmp_path, cmd, state), [er()])
    x = res(r)
    assert x.trigger_event_count == 1 and x.response_matched_count == 1 and codes(r) == {"event_entry_unknown"}
    assert r.status is Status.INSUFFICIENT_EVIDENCE  # the unknown first sample still blocks "no divergence"
    late = run(rec(tmp_path, cmd, [(0, "RUN"), (100, "RUN"), (1000, "RUN")], "l.mcap"), [er()])
    assert late.status is Status.INSUFFICIENT_EVIDENCE and "event_entry_unknown" in codes(late)  # the missing response IS established for the observed entry...
    f = late.candidates[0]
    assert f.observations[0].timestamp_ns == at(500) and f.time.ns == at(800)  # ...but the unknown first sample means it cannot be defended as the earliest


def test_the_first_sample_rule_applies_to_trigger_conditions_of_every_operator(tmp_path):
    streams = {"/cmd": [(at(0), {"v": 9}), (at(100), {"v": 1}), (at(200), {"v": 9})], "/state": [(at(0), {"v": 0}), (at(250), {"v": 1}), (at(1000), {"v": 1})]}
    p = write_mcap(tmp_path / "n.mcap", streams)
    r = run(p, [er(trig=("/cmd", "v", "ge", 5), resp=("/state", "v", "ge", 1), deadline=100)])
    x = res(r)
    assert x.trigger_event_count == 1 and "event_entry_unknown" in codes(r)  # the 9 at 0 ms is unknown; the 9 at 200 ms is an observed entry


# ------------------------------------------------------------------ 6c. the response state at the trigger must be observed

def test_first_response_sample_after_the_trigger_and_matching_is_not_a_confirmed_response(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (900, "IDLE")]
    state = [(150, "STOPPED"), (1000, "STOPPED")]  # the first /state sample arrives after the trigger
    r = run(rec(tmp_path, cmd, state), [er()])
    x = res(r)
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"response_state_unknown"}
    assert x.response_matched_count == 0 and r.candidates == [] and r.first_divergence is None  # neither a confirmed response nor a confirmed miss
    assert x.trigger_event_count == 1


def test_first_response_sample_after_the_trigger_nonmatching_then_matching_is_not_confirmed(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (900, "IDLE")]
    state = [(150, "RUN"), (250, "STOPPED"), (1000, "STOPPED")]
    r = run(rec(tmp_path, cmd, state), [er()])
    x = res(r)
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"response_state_unknown"}
    assert x.response_matched_count == 0 and r.candidates == []


def test_first_response_sample_after_the_trigger_with_no_response_is_not_a_confirmed_miss(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (900, "IDLE")]
    r = run(rec(tmp_path, cmd, [(150, "RUN"), (1000, "RUN")]), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.candidates == [] and codes(r) == {"response_state_unknown"}


def test_the_response_state_blind_spot_points_at_the_trigger_message(tmp_path):
    cmd = [(0, "IDLE"), (100, "STOP"), (900, "IDLE")]
    p = rec(tmp_path, cmd, [(150, "STOPPED"), (1000, "STOPPED")])
    b = res(run(p, [er()])).blind_spots[0]
    assert b.time_ns == at(100) and message_order(p)[int(b.observation_id.split("#")[1])] == ("/cmd", at(100))


def test_with_an_observed_prior_response_sample_the_event_resolves(tmp_path):
    state = [(50, "RUN"), (250, "STOPPED"), (1000, "STOPPED")]  # a sample before the trigger exists: state known
    r = run(rec(tmp_path, [(0, "IDLE"), (100, "STOP"), (900, "IDLE")], state), [er()])
    assert r.status is Status.NO_DIVERGENCE and res(r).response_matched_count == 1


# ------------------------------------------------------------------ 8. provenance

def message_order(path):
    with open(path, "rb") as fh:
        return [(ch.topic, m.log_time) for _s, ch, m in make_reader(fh).iter_messages(log_time_order=False)]


def test_every_result_references_artifact_topic_index_time_and_clock(tmp_path):
    pre = {"topic": "/mode", "signal": "state", "op": "eq", "value": "RUNNING", "max_age_ns": 900 * MS}
    streams = {"/mode": [(at(0), {"state": "RUNNING"})],
               "/cmd": [(at(10), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
               "/state": [(at(10), {"v": "RUN"}), (at(500), {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})]}
    p = write_mcap(tmp_path / "p.mcap", streams)
    r = run(p, [er(applies_when=[pre])])
    f = r.first_divergence
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    order = message_order(p)
    assert [s.role for s in f.sources] == ["trigger_event", "late_response", "prerequisite"]
    ids = {s.observation_id for s in f.sources}
    assert ids == {o.observation_id for o in f.observations}
    for s, o in zip(f.sources, f.observations):
        assert s.relative_path == "p.mcap" and s.artifact_sha256 == sha and s.artifact_ref == o.artifact_ref and s.topic == o.topic
        assert o.timestamp_ns is not None and o.clock_domain == "mcap_log_time" and o.timestamp_basis == "mcap_log_time"
        assert order[o.message_index] == (o.topic, o.timestamp_ns) and o.observation_id == f"obs:p.mcap#{o.message_index}"
    assert [o.topic for o in f.observations] == ["/cmd", "/state", "/mode"]
    assert f.time.clock_domain == "mcap_log_time" and f.time.ns == at(400)


def test_blind_spots_reference_their_message(tmp_path):
    streams = {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"}), (at(900), {"v": "IDLE"})],
               "/state": [(at(0), {"v": "RUN"}), (at(200), b"{bad"), (at(1000), {"v": "RUN"})]}
    p = write_mcap(tmp_path / "b.mcap", streams)
    r = run(p, [er()])
    b = res(r).blind_spots[0]
    assert b.time_ns == at(200) and message_order(p)[int(b.observation_id.split("#")[1])] == ("/state", at(200))


def test_rosbag2_directory_with_split_segments(tmp_path):
    seg1 = {"/cmd": [(at(0), {"v": "IDLE"}), (at(100), {"v": "STOP"})], "/state": [(at(0), {"v": "RUN"})]}
    seg2 = {"/state": [(at(600), {"v": "STOPPED"}), (at(1000), {"v": "STOPPED"})], "/cmd": [(at(900), {"v": "IDLE"})]}
    bag = write_json_bag(tmp_path / "bag", [seg1, seg2])
    r = investigate(inspect_evidence(bag), parse_contract(contract([er()])))
    f = r.first_divergence
    assert f.sources[0].relative_path == "bag_0.mcap" and f.sources[1].relative_path == "bag_1.mcap"  # trigger and late response in different files
    assert f.sources[0].artifact_sha256 != f.sources[1].artifact_sha256 and "exceeds the deadline" in f.discrepancy


# ------------------------------------------------------------------ 9. repeatability

def test_repeated_runs_are_byte_identical(tmp_path):
    p = rec(tmp_path, CMD, [(0, "RUN"), (500, "STOPPED"), (1000, "STOPPED")])
    c = contract([er("A"), er("B", deadline=500), rng(300)])
    outs = {investigation_json(investigate(inspect_evidence(p), parse_contract(c))) for _ in range(3)}
    assert len(outs) == 1


def test_output_does_not_depend_on_the_python_hash_seed(tmp_path):
    p = rec(tmp_path, CMD, [(0, "RUN"), (500, "STOPPED"), (1000, "STOPPED")])
    cfile = tmp_path / "c.json"
    cfile.write_text(json.dumps(contract([er("A"), er("B", deadline=500)])))
    outs = []
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", "from physicore_collector.cli import app; app()", "evidence", "investigate", str(p), "-e", str(cfile), "--json"],
                             capture_output=True, text=True, env=env, cwd=ROOT)
        outs.append((out.returncode, out.stdout))
    assert len({o for o in outs}) == 1 and outs[0][1].startswith("{")


def test_finding_ids_are_stable_and_independent_of_other_expectations(tmp_path):
    p = rec(tmp_path, CMD, [(0, "RUN"), (1000, "RUN")])
    a = run(p, [er("A")]).candidates[0].finding_id
    b = [f for f in run(p, [er("Z", trig=("/cmd", "v", "eq", "NOPE")), er("A")]).candidates if f.expectation_id == "A"][0].finding_id
    assert a == b


def test_reading_is_read_only(tmp_path, monkeypatch):
    from tests.test_inventory import forbid_writes, tree_state
    p = rec(tmp_path, CMD, ok_state(250))
    before = tree_state(p)
    inv = inspect_evidence(p)
    forbid_writes(monkeypatch)
    assert investigate(inv, parse_contract(contract([er()]))).status is Status.NO_DIVERGENCE
    monkeypatch.undo()
    assert tree_state(p) == before


# ------------------------------------------------------------------ 10. contract validation

@pytest.mark.parametrize("mutate", [
    lambda c: c["check"].update(deadline_ns=0), lambda c: c["check"].update(deadline_ns=1.5), lambda c: c["check"].update(trigger_op="between"),
    lambda c: c["check"].update(trigger_op="gt", trigger_value="x"), lambda c: c["check"]["response"].update(op="lt", value="x"),
    lambda c: c["check"]["response"].pop("topic"), lambda c: c["check"]["response"].update(signal="a[0]"), lambda c: c["check"].update(extra=1),
    lambda c: c["check"]["response"].update(time_source="payload_field"), lambda c: c["check"]["response"].update(time_source="log_time", time_field="x"),
    lambda c: c["check"].pop("response"),
])
def test_event_response_contract_problems_are_rejected(mutate):
    e = er()
    mutate(e)
    assert parse_contract(contract([e])).contract is None


def test_event_response_contract_round_trips_with_stable_hash():
    c = contract([er(), er("B", trig=("/a", "v", "gt", 1), resp=("/b", "v", "lt", 2), deadline=5)])
    a, b = parse_contract(c), parse_contract(json.loads(json.dumps(c)))
    assert a.contract is not None and a.sha256 == b.sha256


def test_old_contracts_are_unchanged():
    c = contract([expectation(check={"kind": "transition", "from_value": "A", "to_value": "B", "deadline_ns": 5})])
    txt = json.dumps(parse_contract(c).contract.model_dump(mode="json"))
    assert "max_age_ns" not in txt and "clock_assertion" not in txt and "trigger" not in txt


# ------------------------------------------------------------------ 11. Phase 2 / M3a unchanged

def test_phase2_example_output_is_byte_identical():
    ex = ROOT / "examples" / "investigator"
    res_ = runner.invoke(app, ["evidence", "investigate", str(ex / "example_recording.mcap"), "-e", str(ex / "expectations.json"), "--json"])
    assert res_.exit_code == 1 and res_.output == (ex / "expected_output.json").read_text()


def test_non_event_response_results_carry_no_new_fields(tmp_path):
    p = write_mcap(tmp_path / "j.mcap", {"/temp": [(at(0), {"v": 1}), (at(100), {"v": 99})]})
    text = investigation_json(run(p, [expectation(check={"kind": "range", "max": 10})]))
    for key in ("trigger_event_count", "response_matched_count", "max_age_ns", "clock_assertion", "receipt"):
        assert key not in text


# ------------------------------------------------------------------ 12. CDR payloads (extras `ros2` + `xcheck`)

@pytest.fixture
def cf():
    pytest.importorskip("rosbags")
    from tests import cdr_fixtures
    return cdr_fixtures


def cdr_er(cf, tmp_path, cmd, state, name="c.mcap"):
    chans = [("/cmd", "std_msgs/msg/String", [(at(t), cf.string(v)) for t, v in cmd]),
             ("/state", "std_msgs/msg/String", [(at(t), cf.string(v)) for t, v in state])]
    return cf.write_cdr_mcap(tmp_path / name, chans)


def cdr_er_exp(**kw):
    return er(trig=("/cmd", "data", "eq", "STOP"), resp=("/state", "data", "eq", "STOPPED"), **kw)


@needs_decoder
def test_cdr_event_response_deadlines(tmp_path, cf):
    assert run(cdr_er(cf, tmp_path, CMD, ok_state(250)), [cdr_er_exp()]).status is Status.NO_DIVERGENCE
    r = run(cdr_er(cf, tmp_path, CMD, ok_state(450), "late.mcap"), [cdr_er_exp()])
    f = r.first_divergence
    assert r.status is Status.DIVERGENCE_FOUND and f.time.ns == at(400) and "exceeds the deadline" in f.discrepancy
    assert {o.ros_type for o in f.observations} == {"string"} and r.evidence.payload_decoding is not None
    assert {t.topic for t in r.evidence.payload_decoding.topics} == {"/cmd", "/state"}


@needs_decoder
def test_cdr_equal_timestamps_and_missing_response(tmp_path, cf):
    r = run(cdr_er(cf, tmp_path, CMD, [(0, "RUN"), (100, "STOPPED"), (1000, "STOPPED")]), [cdr_er_exp()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"event_response_ordering_ambiguous"}
    r = run(cdr_er(cf, tmp_path, CMD, [(0, "RUN"), (1000, "RUN")], "m.mcap"), [cdr_er_exp()])
    assert r.status is Status.DIVERGENCE_FOUND and "no matching response was observed" in r.first_divergence.discrepancy


@needs_decoder
def test_cdr_response_by_selector_on_joint_state(tmp_path, cf):
    chans = [("/cmd", "std_msgs/msg/String", [(at(0), cf.string("IDLE")), (at(100), cf.string("MOVE")), (at(900), cf.string("IDLE"))]),
             ("/js", "sensor_msgs/msg/JointState", [(at(t), cf.joint_state(at(t), ["a", "b"], [0.0, p])) for t, p in [(0, 0.0), (200, 0.5), (350, 1.0), (1000, 1.0)]])]
    p = cf.write_cdr_mcap(tmp_path / "j.mcap", chans)
    e = er(trig=("/cmd", "data", "eq", "MOVE"), resp=("/js", "position[name=b]", "ge", 1.0), deadline=300)
    r = run(p, [e])
    assert r.status is Status.NO_DIVERGENCE  # 350 ms is 250 ms after the trigger
    r2 = run(p, [er(trig=("/cmd", "data", "eq", "MOVE"), resp=("/js", "position[name=b]", "ge", 1.0), deadline=200)])
    f = r2.first_divergence
    assert f.observations[1].signal == "position[name=b]" and f.observations[1].value == Decimal("1.0") and f.observations[1].ros_type == "float64"


@needs_decoder
def test_cdr_header_stamp_is_exact_and_its_own_domain(tmp_path, cf):
    sec, ns = 1_700_000_123, 987_654_321
    msgs = [(cf.t(0), cf.joint_state(sec * SEC + ns, ["j"], [99.0]))]
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/js", "sensor_msgs/msg/JointState", msgs)])
    e = expectation("H", topic="/js", signal="position[name=j]", time_source="header_stamp", clock_domain="payload_stamp:/js:header.stamp",
                    check={"kind": "range", "max": 10})
    f = run(p, [e]).first_divergence
    o = f.observations[0]
    assert f.time.ns == sec * SEC + ns and o.timestamp_basis == "header_stamp:header.stamp" and o.clock_domain == "payload_stamp:/js:header.stamp"
    assert o.ros_type == "float64" and o.timestamp_ns != cf.t(0)  # the stamp, not the recorder's receipt time


@needs_decoder
@pytest.mark.parametrize("sec, nsec", [(5, 2_000_000_000), (5, 1_000_000_000)])
def test_cdr_invalid_stamps_are_not_used(tmp_path, cf, sec, nsec):
    ts = cf.typestore()
    T, H = ts.types["builtin_interfaces/msg/Time"], ts.types["std_msgs/msg/Header"]
    import numpy as np
    m = ts.types["sensor_msgs/msg/JointState"](header=H(stamp=T(sec=sec, nanosec=nsec), frame_id="b"), name=["j"], position=np.array([99.0]),
                                               velocity=np.array([]), effort=np.array([]))
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/js", "sensor_msgs/msg/JointState", [(cf.t(0), m)])])
    e = expectation("H", topic="/js", signal="position[name=j]", time_source="header_stamp", clock_domain="payload_stamp:/js:header.stamp",
                    check={"kind": "range", "max": 10})
    r = run(p, [e])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "timestamp_missing" in codes(r, "H")


@needs_decoder
def test_cdr_zero_stamp_is_accepted_and_the_type_is_schema_verified(tmp_path, cf):
    msgs = [(cf.t(0), cf.joint_state(0, ["j"], [99.0])), (cf.t(1), cf.joint_state(5, ["j"], [98.0]))]
    p = cf.write_cdr_mcap(tmp_path / "z.mcap", [("/js", "sensor_msgs/msg/JointState", msgs)])
    e = expectation("H", topic="/js", signal="position[name=j]", time_source="header_stamp", clock_domain="payload_stamp:/js:header.stamp",
                    check={"kind": "range", "max": 10})
    r = run(p, [e])
    x = res(r, "H")
    assert r.first_divergence.time.ns == 0 and x.stamp_type_check.startswith("schema_verified") and "builtin_interfaces/msg/Time" in x.stamp_type_check
    assert not any("structure_only" in u for u in r.first_divergence.uncertainty)


@needs_decoder
def test_a_duration_is_not_accepted_as_a_clock(tmp_path, cf):
    import struct
    schema = (b"builtin_interfaces/Duration stamp\nfloat64 v\n" + b"=" * 80 + b"\nMSG: builtin_interfaces/Duration\nint32 sec\nuint32 nanosec\n")
    payload = b"\x00\x01\x00\x00" + struct.pack("<iI", 5, 7) + struct.pack("<d", 99.0)
    p = cf.write_cdr_mcap(tmp_path / "d.mcap", [("/d", "pkg/msg/D", [(cf.t(0), payload)])], schema_override={"pkg/msg/D": schema})
    plain = expectation("D", topic="/d", signal="v", check={"kind": "range", "max": 10})
    assert run(p, [plain]).status is Status.DIVERGENCE_FOUND  # the message decodes fine on log_time
    e = expectation("D", topic="/d", signal="v", time_source="header_stamp", time_field="stamp", clock_domain="payload_stamp:/d:stamp",
                    check={"kind": "range", "max": 10})
    r = run(p, [e])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "timestamp_missing" in codes(r, "D")  # a Duration (same sec/nanosec shape) is not a Time


@needs_decoder
def test_without_the_decoder_event_response_on_cdr_is_insufficient_evidence(tmp_path, cf, monkeypatch):
    p = cdr_er(cf, tmp_path, CMD, ok_state(250))
    monkeypatch.setattr(cdr, "_load_decoder_module", lambda: (_ for _ in ()).throw(ImportError("nope")))
    r = run(p, [cdr_er_exp()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and res(r).reason_code == "payload_decoder_unavailable"


@needs_decoder
def test_cdr_provenance_points_at_the_right_messages(tmp_path, cf):
    p = cdr_er(cf, tmp_path, CMD, ok_state(450))
    r = run(p, [cdr_er_exp()])
    f = r.first_divergence
    order = message_order(p)
    for o in f.observations:
        assert order[o.message_index] == (o.topic, o.timestamp_ns) and o.clock_domain == "mcap_log_time"
    assert f.sources[0].artifact_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ 13. the committed example (examples/m3b)

EXAMPLE = ROOT / "examples" / "m3b"


@needs_decoder
def test_committed_example_outputs_are_reproduced_byte_for_byte():
    rec = EXAMPLE / "m3b_example.mcap"
    for exp, out in (("expectations.json", "expected_output.json"), ("expectations_stale_prerequisite.json", "expected_output_stale_prerequisite.json")):
        loaded = parse_contract(json.loads((EXAMPLE / exp).read_text()))
        assert investigation_json(investigate(inspect_evidence(rec), loaded)) == (EXAMPLE / out).read_text(), out
        cli = runner.invoke(app, ["evidence", "investigate", str(rec), "-e", str(EXAMPLE / exp), "--json"])
        assert cli.output == (EXAMPLE / out).read_text()


@needs_decoder
def test_committed_example_meaning():
    rec = EXAMPLE / "m3b_example.mcap"
    r = investigate(inspect_evidence(rec), parse_contract(json.loads((EXAMPLE / "expectations.json").read_text())))
    by = {x.expectation_id: x for x in r.expectation_results}
    assert r.status is Status.DIVERGENCE_FOUND and r.first_divergence.expectation_id == "ER-GRIPPER"
    assert by["ER-ESTOP"].status is Status.NO_DIVERGENCE and (by["ER-ESTOP"].trigger_event_count, by["ER-ESTOP"].response_matched_count) == (1, 1)
    assert by["ER-LIFT"].status is Status.NO_DIVERGENCE and (by["ER-LIFT"].trigger_event_count, by["ER-LIFT"].response_matched_count) == (1, 1)
    hs = by["HS-LIFT-LIMIT"]
    assert hs.status is Status.NO_DIVERGENCE and hs.clock_domain == "payload_stamp:/joint_states:header.stamp" and hs.stamp_type_check.startswith("schema_verified")
    f = r.first_divergence
    assert f.time.ns == BASE + 2500 * MS and "exceeds the deadline 500000000 ns by 500000000 ns" in f.discrepancy
    s = investigate(inspect_evidence(rec), parse_contract(json.loads((EXAMPLE / "expectations_stale_prerequisite.json").read_text())))
    assert s.status is Status.INSUFFICIENT_EVIDENCE and {b.code for b in {x.expectation_id: x for x in s.expectation_results}["ER-ESTOP"].blind_spots} == {"prerequisite_stale"}


def test_committed_example_is_what_the_generator_writes(tmp_path):
    pytest.importorskip("rosbags")
    if not HAVE_DECODER:
        pytest.skip("regenerating the expected outputs needs the decoder")
    from tests.make_m3b_example import write_example
    write_example(tmp_path / "gen")
    for p in sorted(EXAMPLE.iterdir()):
        assert (tmp_path / "gen" / p.name).read_bytes() == p.read_bytes(), f"examples/m3b/{p.name} is stale: run python -m tests.make_m3b_example examples/m3b"


# ------------------------------------------------------------------ 14. the decoding standing statement follows what was actually decoded

JSON_ONLY = "Only json-encoded message payloads are decoded. Other encodings (for example CDR) are not interpreted."


def test_json_only_investigations_keep_the_phase_2_statement(tmp_path):
    r = run(rec(tmp_path, CMD, ok_state(250)), [er()])
    assert JSON_ONLY in r.not_established and not any("CDR payloads that carry" in x for x in r.not_established)


@needs_decoder
def test_the_statement_is_corrected_when_cdr_was_decoded(tmp_path, cf):
    r = run(cdr_er(cf, tmp_path, CMD, ok_state(250)), [cdr_er_exp()])
    assert JSON_ONLY not in r.not_established
    assert sum("CDR payloads that carry an embedded ros2msg schema" in x for x in r.not_established) == 1


@needs_decoder
def test_the_original_statement_stays_true_when_the_decoder_is_unavailable(tmp_path, cf, monkeypatch):
    p = cdr_er(cf, tmp_path, CMD, ok_state(250))
    monkeypatch.setattr(cdr, "_load_decoder_module", lambda: (_ for _ in ()).throw(ImportError("nope")))
    r = run(p, [cdr_er_exp()])
    assert JSON_ONLY in r.not_established  # nothing was decoded, so "CDR is not interpreted" is what happened


# ------------------------------------------------------------------ 15. missing-response wording: what the recording establishes, and no more

SILENT_OR_LOST = "cannot show whether the response was never published"


def test_a_missing_response_is_described_as_not_observed_in_the_recording(tmp_path):
    r = run(rec(tmp_path, CMD, [(0, "RUN"), (500, "RUN"), (1000, "RUN")]), [er()])
    f = r.first_divergence
    d = f.discrepancy
    assert d == ("the trigger was received at 1700000000100000000 ns and no matching response was observed in the recorded stream by "
                 "1700000000400000000 ns (deadline 300000000 ns, receipt times)")
    for banned in ("never", "did not respond", "failed to", "no response occurred", "was not published", "occurred by"):
        assert banned not in d and banned not in f.explanation.replace("not a statement about cause", ""), banned
    assert any(SILENT_OR_LOST in u and "silent or stopped" in u and "not captured" in u for u in f.uncertainty)
    assert any("RECEIPT" in u for u in f.uncertainty)  # the receipt-time caveat is kept next to it
    assert any("not shown to have been unpublished" in n for n in r.not_established)
    assert "receipt time" in " ".join(r.not_established)
    assert res(r).reason == d


def test_a_late_response_names_receipt_and_has_no_silence_caveat(tmp_path):
    f = run(rec(tmp_path, CMD, ok_state(450)), [er()]).first_divergence
    assert "the first matching response in the recorded stream was received at" in f.discrepancy and "exceeds the deadline" in f.discrepancy
    assert not any(SILENT_OR_LOST in u for u in f.uncertainty) and any("RECEIPT" in u for u in f.uncertainty)  # a response WAS recorded


def test_the_documentation_states_the_same_limits():
    doc = (ROOT / "docs" / "M3B_EVENT_RESPONSE.md").read_text()
    flat = " ".join(doc.split())
    for phrase in ("no matching response was observed in the recorded stream",
                   "A silent or stopped publisher and a message that was published but not captured (dropped, not recorded, lost in transport or storage) may be indistinguishable in a recording",
                   "recorder receipt order, not when events physically occurred"):
        assert phrase in flat, phrase
    assert "confirmed miss, provided it has" not in flat and "is therefore a confirmed miss" not in flat
    assert "response never published" not in flat.lower().replace("not show", "")


def test_the_existing_safeguards_are_unchanged(tmp_path):
    r = run(rec(tmp_path, [(0, "IDLE"), (100, "STOP"), (900, "IDLE")], [(150, "RUN"), (1000, "RUN")]), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"response_state_unknown"} and r.candidates == []
    r = run(rec(tmp_path, [(0, "STOP"), (900, "IDLE")], [(0, "RUN"), (1000, "RUN")], "f.mcap"), [er()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and codes(r) == {"event_entry_unknown"} and r.candidates == []


# ------------------------------------------------------------------ 16. stamp_type_check says what the schema actually established

DURATION_SCHEMA = (b"builtin_interfaces/Duration stamp\nfloat64 v\n" + b"=" * 80 + b"\nMSG: builtin_interfaces/Duration\nint32 sec\nuint32 nanosec\n")


def hs_cdr(field="header.stamp", topic="/js", signal="position[name=j]"):
    return expectation("H", topic=topic, signal=signal, time_source="header_stamp", time_field=field, clock_domain=f"payload_stamp:{topic}:{field}",
                       check={"kind": "range", "max": 10})


def js_recording(cf, tmp_path, name="js.mcap"):
    return cf.write_cdr_mcap(tmp_path / name, [("/js", "sensor_msgs/msg/JointState", [(cf.t(0), cf.joint_state(5 * SEC, ["j"], [99.0]))])])


@needs_decoder
def test_a_valid_time_field_is_schema_verified(tmp_path, cf):
    r = run(js_recording(cf, tmp_path), [hs_cdr()])
    x = res(r, "H")
    assert x.stamp_type_check == "schema_verified: the embedded schema declares 'header.stamp' as builtin_interfaces/msg/Time"
    assert r.first_divergence.time.ns == 5 * SEC and not any("structure_only" in u or "rejected" in u for u in r.first_divergence.uncertainty)


@needs_decoder
def test_a_duration_is_reported_as_rejected_not_verified(tmp_path, cf):
    import struct
    payload = b"\x00\x01\x00\x00" + struct.pack("<iI", 5, 7) + struct.pack("<d", 99.0)
    p = cf.write_cdr_mcap(tmp_path / "d.mcap", [("/d", "pkg/msg/D", [(cf.t(0), payload)])], schema_override={"pkg/msg/D": DURATION_SCHEMA})
    r = run(p, [hs_cdr("stamp", "/d", "v")])
    x = res(r, "H")
    assert x.stamp_type_check == ("rejected_type: the embedded schema declares 'stamp' as builtin_interfaces/msg/Duration, "
                                  "not builtin_interfaces/msg/Time; its values are not used as a clock")
    assert "schema_verified" not in investigation_json(r) and "timestamp_missing" in codes(r, "H")  # and no sample was placed in time


@needs_decoder
def test_a_field_missing_from_the_schema_is_not_verified(tmp_path, cf):
    r = run(js_recording(cf, tmp_path), [hs_cdr("header.nostamp")])
    x = res(r, "H")
    assert x.stamp_type_check.startswith("field_not_in_schema: the embedded schema of sensor_msgs/msg/JointState has no field 'header.nostamp'")
    assert "schema_verified" not in investigation_json(r) and "timestamp_missing" in codes(r, "H")


@needs_decoder
def test_a_field_whose_type_cannot_be_resolved_is_not_verified(tmp_path, cf, monkeypatch):
    real = cdr.parse_schema_index

    def without_header(name, text):
        idx = real(name, text)
        if idx is not None:
            idx.pop("std_msgs/msg/Header", None)  # the index no longer knows the type that holds the stamp
        return idx
    monkeypatch.setattr(cdr, "parse_schema_index", without_header)
    r = run(js_recording(cf, tmp_path), [hs_cdr()])
    x = res(r, "H")
    assert x.stamp_type_check.startswith("schema_unresolved:") and "unknown and was not verified" in x.stamp_type_check
    assert "schema_verified" not in investigation_json(r) and "timestamp_missing" in codes(r, "H")


@needs_decoder
def test_a_schema_that_could_not_be_indexed_is_not_verified(tmp_path, cf, monkeypatch):
    monkeypatch.setattr(cdr, "parse_schema_index", lambda name, text: None)
    r = run(js_recording(cf, tmp_path), [hs_cdr()])
    assert res(r, "H").stamp_type_check.startswith("schema_unresolved:")


@needs_decoder
def test_decoder_unavailable_cdr_is_not_described_as_json(tmp_path, cf, monkeypatch):
    p = js_recording(cf, tmp_path)
    monkeypatch.setattr(cdr, "_load_decoder_module", lambda: (_ for _ in ()).throw(ImportError("nope")))
    r = run(p, [hs_cdr()])
    x = res(r, "H")
    assert x.reason_code == "payload_decoder_unavailable"
    assert x.stamp_type_check == "payload_not_decoded: the CDR payloads of this topic could not be decoded (decoder_unavailable), so the type of 'header.stamp' was not checked"
    assert "structure_only" not in x.stamp_type_check and "JSON" not in x.stamp_type_check and "schema_verified" not in x.stamp_type_check


def test_json_stays_structure_only_and_never_verified(tmp_path):
    p = write_mcap(tmp_path / "h.mcap", {"/t": [(at(0), stamped(100, 5, v=99))]})
    x = res(run(p, [hs_expect()]), "H")
    assert x.stamp_type_check.startswith("structure_only: the payload is not schema-typed (JSON)") and "schema_verified" not in x.stamp_type_check


def test_the_field_is_absent_for_expectations_that_do_not_use_header_stamp(tmp_path):
    r = run(rec(tmp_path, CMD, ok_state(250)), [er()])
    assert "stamp_type_check" not in investigation_json(r)
