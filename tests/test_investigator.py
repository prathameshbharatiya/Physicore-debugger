"""Phase 2: the deterministic first-divergence Investigator (physicore_collector.investigator + `evidence investigate`).

Everything is synthetic and OFFLINE: MCAP files written by tests/investigator_fixtures.py with chosen JSON payloads. Nothing here
shows that live ROS 2 capture works (tests/ros2_integration stays skipped without ROS 2)."""

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app
from physicore_collector.export import export_package
from physicore_collector.checksums import sha256_file
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator import (
    Status, exit_code, investigate, investigation_json, load_contract, parse_contract, render_investigation,
)
from physicore_collector.investigator.engine import invalid_result
from physicore_collector.session import analyze_session
from tests import make_fixtures as mf
from tests.bagtools import write_bag
from tests.investigator_fixtures import (
    BASE, MS, SEC, build_json_mcap, contract, expectation, series, write_json_bag, write_mcap,
)
from tests.test_incident import NOW, prep
from tests.test_inventory import forbid_writes, tree_state

runner = CliRunner()
T = lambda i: BASE + i * 100 * MS  # noqa: E731  (the i-th sample of a default `series`)


def run(path, exps, **kw):
    return investigate(inspect_evidence(path), parse_contract(contract(exps, **kw)))


def by_id(r, eid):
    return next(x for x in r.expectation_results if x.expectation_id == eid)


@pytest.fixture
def one(tmp_path):
    """A single .mcap whose /temp is 1,2,3,12,4,15 (limit 10 is first exceeded by the 4th sample, 12)."""
    return write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 3, 12, 4, 15])})


# ------------------------------------------------------------------ 1. a clear divergence, known output

def test_clear_divergence_has_exactly_the_expected_output(one):
    r = run(one, [expectation("TEMP-LIMIT", check={"kind": "range", "max": 10}, description="temperature at most 10")])
    assert r.status is Status.DIVERGENCE_FOUND and r.status_reason_code == "earliest_divergence_defended"
    f = r.first_divergence
    assert f is not None and r.candidates == [f]
    assert f.finding_id.startswith("FND-") and len(f.finding_id) == 20
    assert (f.expectation_id, f.expectation_kind) == ("TEMP-LIMIT", "range")
    assert f.time.model_dump() == {"ns": T(3), "clock_domain": "mcap_log_time", "timestamp_basis": "mcap_log_time", "kind": "observation"}
    assert f.expected == "at most 10"
    assert f.observed == f"/temp.v = 12 at {T(3)} ns (mcap_log_time)"
    assert f.discrepancy == "12 is above the maximum 10 by 2"
    assert f.rule.check_kind == "range" and f.rule.parameters == {"max": "10"} and "inclusive" in f.rule.comparison
    assert f.ordering == "earliest_defended" and f.uncertainty == []
    assert "not a statement about cause" in f.explanation
    o = f.observations[0]
    assert (o.observation_id, o.topic, o.signal, o.value, o.value_type, o.timestamp_ns, o.clock_domain, o.quality, o.parser_status) == \
        ("obs:r.mcap#3", "/temp", "v", Decimal("12"), "number", T(3), "mcap_log_time", "ok", "readable")
    assert by_id(r, "TEMP-LIMIT").evaluated_observation_count == 6
    assert exit_code(r) == 1


def test_only_the_first_mismatch_of_an_expectation_is_reported(one):
    r = run(one, [expectation(check={"kind": "range", "max": 10})])
    assert r.first_divergence.time.ns == T(3) and r.first_divergence.observations[0].value == Decimal("12")  # not the later 15


def test_the_discrepancy_for_a_low_value_names_the_minimum(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([5, 4, 1])})
    r = run(p, [expectation(check={"kind": "range", "min": 3})])
    assert r.first_divergence.discrepancy == "1 is below the minimum 3 by 2" and r.first_divergence.expected == "at least 3"


# ------------------------------------------------------------------ 2. several divergences: the earliest is defensible

def test_earliest_of_several_divergences_across_topics(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 3, 12, 4, 15]), "/pressure": series([9, 1, 9, 9, 9, 9])})
    r = run(p, [expectation("TEMP", topic="/temp", check={"kind": "range", "max": 10}),
                expectation("PRESS", topic="/pressure", check={"kind": "range", "min": 5})])
    assert r.status is Status.DIVERGENCE_FOUND
    assert r.first_divergence.expectation_id == "PRESS" and r.first_divergence.time.ns == T(1)
    assert [f.expectation_id for f in r.candidates] == ["PRESS", "TEMP"]
    assert [f.ordering for f in r.candidates] == ["earliest_defended", "later_than_earliest"]


def test_the_earliest_is_found_regardless_of_the_order_of_expectations_in_the_contract(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 3, 12]), "/pressure": series([9, 1, 9, 9])})
    a = expectation("TEMP", topic="/temp", check={"kind": "range", "max": 10})
    b = expectation("PRESS", topic="/pressure", check={"kind": "range", "min": 5})
    assert run(p, [a, b]).first_divergence.finding_id == run(p, [b, a]).first_divergence.finding_id


def test_a_divergence_is_not_hidden_by_an_expectation_that_could_not_be_evaluated(one):
    r = run(one, [expectation("E1", check={"kind": "range", "max": 10}),
                  expectation("E2", topic="/absent", check={"kind": "range", "max": 1})])
    assert r.status is Status.DIVERGENCE_FOUND and r.not_evaluated == ["E2"]
    assert any("E2" in u and "earlier divergence among them cannot be excluded" in u for u in r.first_divergence.uncertainty)


# ------------------------------------------------------------------ 3. no divergence in the covered window

def test_no_divergence_is_reported_with_its_covered_window_and_a_disclaimer(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 3, 4, 5, 6])})
    r = run(p, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.NO_DIVERGENCE and r.first_divergence is None and r.candidates == []
    x = by_id(r, "E1")
    assert x.covered_window.model_dump() == {"clock_domain": "mcap_log_time", "start_ns": T(0), "end_ns": T(5)}
    assert x.evaluated_observation_count == 6 and "does not show" in x.reason
    assert "does not show the system is correct" in r.status_reason
    assert exit_code(r) == 0


def test_a_divergence_outside_the_window_is_not_a_divergence_of_the_window(one):
    r = run(one, [expectation(check={"kind": "range", "max": 10}, window={"start_ns": T(0), "end_ns": T(2)})])
    assert r.status is Status.NO_DIVERGENCE
    assert by_id(r, "E1").covered_window.model_dump() == {"clock_domain": "mcap_log_time", "start_ns": T(0), "end_ns": T(2)}


def test_the_covered_window_is_clipped_to_the_evidence(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 3])})
    r = run(p, [expectation(check={"kind": "range", "max": 10}, window={"start_ns": T(1), "end_ns": T(99)})])
    assert r.status is Status.NO_DIVERGENCE
    assert by_id(r, "E1").covered_window.model_dump() == {"clock_domain": "mcap_log_time", "start_ns": T(1), "end_ns": T(2)}


def test_no_divergence_needs_every_expectation_evaluated(one):
    r = run(one, [expectation("E1", check={"kind": "range", "max": 100}), expectation("E2", topic="/absent")])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "not_all_expectations_evaluable"
    assert by_id(r, "E1").status is Status.NO_DIVERGENCE


def test_a_window_with_no_evidence_is_insufficient_not_no_divergence(one):
    r = run(one, [expectation(check={"kind": "range", "max": 10}, window={"start_ns": T(50), "end_ns": T(60)})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and by_id(r, "E1").reason_code == "no_evaluable_observations_in_window"


# ------------------------------------------------------------------ 4. missing required observations

def test_a_topic_that_is_not_in_the_recording_is_insufficient_evidence(one):
    r = run(one, [expectation(topic="/absent")])
    x = by_id(r, "E1")
    assert r.status is Status.INSUFFICIENT_EVIDENCE and x.reason_code == "required_observations_missing"
    assert "not in the recording" in x.missing_evidence[0] and exit_code(r) == 2


def test_a_missing_prerequisite_topic_is_insufficient_evidence(one):
    r = run(one, [expectation(applies_when=[{"topic": "/mode", "signal": "v", "op": "eq", "value": "AUTO"}])])
    assert by_id(r, "E1").reason_code == "required_observations_missing" and "/mode" in by_id(r, "E1").missing_evidence[0]


def test_a_signal_that_never_appears_is_insufficient_not_a_pass(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 3], field="other")})
    r = run(p, [expectation(signal="v")])
    x = by_id(r, "E1")
    assert r.status is Status.INSUFFICIENT_EVIDENCE and x.reason_code == "no_evaluable_observations_in_window"
    assert x.blind_spot_count == 3 and x.blind_spots[0].code == "signal_missing"


# ------------------------------------------------------------------ 5. truncated / unreadable evidence

def _long(n=400, bad_at=None):
    vals = [1] * n
    if bad_at is not None:
        vals[bad_at] = 99
    return {"/temp": series(vals, step=10 * MS)}


def test_truncated_recording_never_yields_no_divergence(tmp_path):
    full = build_json_mcap(_long())
    p = tmp_path / "t.mcap"
    p.write_bytes(full[: int(len(full) * 0.6)])
    inv = inspect_evidence(p)
    assert inv.recording.readability.value == "partially_readable"
    r = investigate(inv, parse_contract(contract([expectation(check={"kind": "range", "max": 10})])))
    x = by_id(r, "E1")
    assert r.status is Status.INSUFFICIENT_EVIDENCE and x.reason_code == "evidence_incomplete"
    assert x.evaluated_observation_count > 0 and x.evidence_complete is False  # what could be read was evaluated and found clean
    assert r.evidence.segments[0].state == "usable_partial"


def test_a_mismatch_in_a_truncated_recording_is_reported_but_not_claimed_as_the_earliest(tmp_path):
    full = build_json_mcap(_long(bad_at=5))
    p = tmp_path / "t.mcap"
    p.write_bytes(full[: int(len(full) * 0.6)])
    r = run(p, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"
    assert r.first_divergence is None and len(r.candidates) == 1 and r.candidates[0].ordering == "earliest_not_defended"
    assert "incomplete" in r.status_reason
    assert any("evidence is incomplete" in u for u in r.candidates[0].uncertainty)


def test_an_unreadable_file_gives_insufficient_evidence(tmp_path):
    p = tmp_path / "bad.mcap"
    p.write_bytes(mf.not_mcap_bytes())
    r = run(p, [expectation()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.evidence.segments[0].state == "unusable"


def test_a_missing_segment_of_a_split_bag_blocks_no_divergence_and_the_earliest_claim(tmp_path):
    s0 = {"/temp": series([1, 2, 3], start=BASE)}
    s1 = {"/temp": series([4, 99, 5], start=BASE + 3 * 100 * MS)}
    bag = write_json_bag(tmp_path / "bagx", [s0, s1])
    (bag / "bagx_1.mcap").unlink()
    r = run(bag, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and by_id(r, "E1").reason_code == "evidence_incomplete"
    assert any("bagx_1.mcap" in s.relative_path and s.state == "unusable" for s in r.evidence.segments)
    bag2 = write_json_bag(tmp_path / "bagy", [{"/temp": series([1, 99, 3], start=BASE)}, s1])
    (bag2 / "bagy_1.mcap").unlink()
    r2 = run(bag2, [expectation(check={"kind": "range", "max": 10})])
    assert r2.status is Status.INSUFFICIENT_EVIDENCE and r2.status_reason_code == "earliest_divergence_not_defensible" and r2.candidates


def test_a_failed_inventory_yields_insufficient_evidence_not_a_traceback(tmp_path):
    r = run(tmp_path / "nothing_here", [expectation()])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and by_id(r, "E1").reason_code == "evidence_unavailable"
    assert r.evidence.blocked


# ------------------------------------------------------------------ 6. incomparable clock domains

def test_a_window_written_in_another_clock_is_incomparable_and_not_converted(one):
    r = run(one, [expectation(clock_domain="capture_supervisor_wall_clock", window={"start_ns": 0, "end_ns": 10**18})])
    x = by_id(r, "E1")
    assert r.status is Status.INCOMPARABLE_TIMEBASES and x.reason_code == "window_clock_domain_differs"
    assert x.evaluated_observation_count == 0 and "no offset" in x.reason and exit_code(r) == 2


def _stamped(tmp_path, offset_ns=0):
    """/aux carries its own stamp field (a different clock from log_time)."""
    msgs = [(T(i), {"v": v, "stamp_ns": 5_000 + i * 1000 + offset_ns}) for i, v in enumerate([1, 1, 50, 1])]
    return write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 3, 12, 4]), "/aux": msgs})


def _aux(eid="AUX", **kw):
    return expectation(eid, topic="/aux", check={"kind": "range", "max": 10}, time_source="payload_field", time_field="stamp_ns",
                       clock_domain="payload_stamp:/aux:stamp_ns", **kw)


def test_a_payload_stamp_clock_is_its_own_domain_and_reports_its_own_time(tmp_path):
    r = run(_stamped(tmp_path), [_aux()])
    f = r.first_divergence
    assert r.status is Status.DIVERGENCE_FOUND
    assert (f.time.ns, f.time.clock_domain, f.time.timestamp_basis) == (7_000, "payload_stamp:/aux:stamp_ns", "payload_field:stamp_ns")


def test_divergences_on_two_clocks_cannot_be_ordered(tmp_path):
    r = run(_stamped(tmp_path), [expectation("LOGCLK", topic="/temp", check={"kind": "range", "max": 10}), _aux()])
    assert r.status is Status.INCOMPARABLE_TIMEBASES and r.status_reason_code == "divergences_on_incomparable_clocks"
    assert r.first_divergence is None and len(r.candidates) == 2
    assert {f.ordering for f in r.candidates} == {"unordered_across_clock_domains"}
    assert "mcap_log_time" in r.status_reason and "payload_stamp:/aux:stamp_ns" in r.status_reason


def test_the_ordering_between_clocks_does_not_depend_on_the_numbers(tmp_path):
    """The stamp 7000 is 'earlier' than the log_time 1.7e18 as a number; that comparison is never made."""
    r = run(_stamped(tmp_path, offset_ns=10**19 // 10), [expectation("LOGCLK", topic="/temp", check={"kind": "range", "max": 10}), _aux()])
    assert r.status is Status.INCOMPARABLE_TIMEBASES and r.first_divergence is None


def test_declaring_a_payload_clock_as_log_time_is_incomparable(tmp_path):
    r = run(_stamped(tmp_path), [_aux() | {"clock_domain": "mcap_log_time"}])
    assert r.status is Status.INCOMPARABLE_TIMEBASES


def test_a_missing_payload_stamp_is_a_blind_spot(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/aux": [(T(0), {"v": 1, "stamp_ns": 1}), (T(1), {"v": 50}), (T(2), {"v": 60, "stamp_ns": 3})]})
    r = run(p, [_aux()])
    assert any(b.code == "timestamp_missing" for b in by_id(r, "AUX").blind_spots)
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"  # the unplaceable sample may be earlier


# ------------------------------------------------------------------ 7. equal timestamps / ambiguous ordering

def test_two_divergences_at_the_same_timestamp_have_no_defensible_earliest(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 99]), "/pressure": series([1, 99])})
    r = run(p, [expectation("A", topic="/temp", check={"kind": "range", "max": 10}),
                expectation("B", topic="/pressure", check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"
    assert r.first_divergence is None and {f.ordering for f in r.candidates} == {"tied_for_earliest"}
    assert "share the timestamp" in r.status_reason and "order is not established" in r.status_reason


def test_equal_timestamps_on_one_topic_for_one_expectation_still_give_one_time(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), {"v": 50}), (T(1), {"v": 60})]})
    r = run(p, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.DIVERGENCE_FOUND and r.first_divergence.time.ns == T(1)
    assert r.first_divergence.observations[0].observation_id == "obs:r.mcap#1"  # deterministic, and never presented as 'the first'


def test_a_prerequisite_sampled_at_the_same_instant_cannot_be_ordered(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 99, 3]), "/mode": series(["AUTO", "AUTO", "AUTO"])})
    r = run(p, [expectation(check={"kind": "range", "max": 10}, applies_when=[{"topic": "/mode", "signal": "v", "op": "eq", "value": "AUTO"}])])
    x = by_id(r, "E1")
    assert r.status is Status.INSUFFICIENT_EVIDENCE and x.evaluated_observation_count == 0
    assert {b.code for b in x.blind_spots} == {"prerequisite_ordering_ambiguous"}


def test_a_transition_whose_ends_share_a_timestamp_is_ambiguous(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/state": [(T(0), {"v": "IDLE"}), (T(1), {"v": "MOVING"}), (T(1), {"v": "DONE"}), (T(9), {"v": "DONE"})]})
    r = run(p, [expectation(topic="/state", check={"kind": "transition", "from_value": "MOVING", "to_value": "DONE", "deadline_ns": 5 * MS})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE
    assert any("ambiguous" in b.code for b in by_id(r, "E1").blind_spots)


# ------------------------------------------------------------------ 8. tolerance and window boundaries

@pytest.mark.parametrize("value, diverges", [
    ("5.5", False), ("4.5", False), ("5.50", False), ("5.5000000000000001", True), ("4.4999999999999999", True), ("5.6", True),
])
def test_tolerance_boundary_is_exact_and_inclusive(tmp_path, value, diverges):
    raw = ('{"v": %s}' % value).encode()
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 5}), (T(1), raw)]})
    r = run(p, [expectation(check={"kind": "tolerance", "target": 5, "tolerance": 0.5})])
    assert (r.status is Status.DIVERGENCE_FOUND) == diverges
    if diverges:
        assert r.first_divergence.rule.tolerance_used == "0.5" and "exceeds the tolerance 0.5" in r.first_divergence.discrepancy


def test_binary_float_rounding_cannot_move_a_value_across_a_limit(tmp_path):
    """0.1 + 0.2 is 0.30000000000000004 in binary floating point; as written in the message it is a different decimal from 0.3."""
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 0.1 + 0.2})]})
    r = run(p, [expectation(check={"kind": "tolerance", "target": 0.3, "tolerance": 0})])
    assert r.status is Status.DIVERGENCE_FOUND and r.first_divergence.observations[0].value == Decimal("0.30000000000000004")
    ok = write_mcap(tmp_path / "ok.mcap", {"/temp": [(T(0), {"v": 0.3})]})
    assert run(ok, [expectation(check={"kind": "tolerance", "target": 0.3, "tolerance": 0})]).status is Status.NO_DIVERGENCE


@pytest.mark.parametrize("value, diverges", [(10, False), ("10.0", False), ("10.0000000000000001", True), (0, False), (-1, True)])
def test_range_bounds_are_inclusive(tmp_path, value, diverges):
    raw = ('{"v": %s}' % value).encode()
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), raw)]})
    assert (run(p, [expectation(check={"kind": "range", "min": 0, "max": 10})]).status is Status.DIVERGENCE_FOUND) == diverges


def test_window_bounds_are_inclusive_to_the_nanosecond(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), {"v": 99})]})
    chk = {"kind": "range", "max": 10}
    assert run(p, [expectation(check=chk, window={"end_ns": T(1)})]).status is Status.DIVERGENCE_FOUND   # exactly at the end: inside
    assert run(p, [expectation(check=chk, window={"end_ns": T(1) - 1})]).status is Status.NO_DIVERGENCE  # one ns before: outside
    assert run(p, [expectation(check=chk, window={"start_ns": T(1)})]).status is Status.DIVERGENCE_FOUND
    assert run(p, [expectation(check=chk, window={"start_ns": T(1) + 1})]).status is Status.INSUFFICIENT_EVIDENCE  # nothing left to evaluate


def _state_file(tmp_path, states, step=100 * MS):
    return write_mcap(tmp_path / "r.mcap", {"/state": series(states, step=step)})


TRANS = {"kind": "transition", "from_value": "MOVING", "to_value": "DONE", "deadline_ns": 300 * MS}


def test_transition_deadline_boundary_is_inclusive(tmp_path):
    # MOVING entered at +100 ms; deadline is +400 ms
    on_time = _state_file(tmp_path / "a", ["IDLE", "MOVING", "MOVING", "MOVING", "DONE", "DONE"])
    assert run(on_time, [expectation(topic="/state", check=TRANS)]).status is Status.NO_DIVERGENCE
    late = _state_file(tmp_path / "b", ["IDLE", "MOVING", "MOVING", "MOVING", "MOVING", "DONE"])
    r = run(late, [expectation(topic="/state", check=TRANS)])
    assert r.status is Status.DIVERGENCE_FOUND
    f = r.first_divergence
    assert f.time.model_dump() == {"ns": T(4), "clock_domain": "mcap_log_time", "timestamp_basis": "mcap_log_time", "kind": "deadline_expiry"}
    assert f.sources[0].role == "transition_entry" and f.observations[0].timestamp_ns == T(1)
    assert "was not observed by" in f.discrepancy


def test_transition_not_covered_by_the_evidence_is_not_a_divergence(tmp_path):
    p = _state_file(tmp_path, ["IDLE", "MOVING", "MOVING"])  # the recording ends before the deadline (+400 ms)
    r = run(p, [expectation(topic="/state", check=TRANS)])
    assert r.status is Status.INSUFFICIENT_EVIDENCE
    assert any(b.code == "deadline_beyond_covered_evidence" for b in by_id(r, "E1").blind_spots)


def test_a_transition_that_never_starts_is_not_evaluated_as_a_pass(tmp_path):
    p = _state_file(tmp_path, ["IDLE", "IDLE", "IDLE"])
    r = run(p, [expectation(topic="/state", check=TRANS)])
    assert r.status is Status.NO_DIVERGENCE and by_id(r, "E1").evaluated_observation_count == 3  # examined, never entered


def test_an_undecodable_sample_inside_a_transition_deadline_blocks_the_conclusion(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/state": [(T(0), {"v": "MOVING"}), (T(1), b"{not json"), (T(2), {"v": "MOVING"}), (T(9), {"v": "MOVING"})]})
    r = run(p, [expectation(topic="/state", check=TRANS)])
    assert r.status is Status.INSUFFICIENT_EVIDENCE


# ------------------------------------------------------------------ 9. invalid expectation contracts

def _bad(mutate):
    d = contract([expectation()])
    mutate(d)
    return d


INVALID = {
    "wrong_version": lambda d: d.update(contract_schema_version="2.0"),
    "no_version": lambda d: d.pop("contract_schema_version"),
    "not_a_mapping": lambda d: None,  # replaced below
    "unknown_field": lambda d: d["expectations"][0].update(oops=1),
    "no_expectations": lambda d: d.update(expectations=[]),
    "missing_source": lambda d: d["expectations"][0].pop("source"),
    "empty_source_reference": lambda d: d["expectations"][0]["source"].update(reference=""),
    "bad_source_kind": lambda d: d["expectations"][0]["source"].update(kind="inferred_from_recording"),
    "bad_id": lambda d: d["expectations"][0].update(expectation_id="has space"),
    "duplicate_ids": lambda d: d["expectations"].append(expectation("E1")),
    "unknown_check": lambda d: d["expectations"][0].update(check={"kind": "magic"}),
    "range_without_bounds": lambda d: d["expectations"][0].update(check={"kind": "range"}),
    "range_min_gt_max": lambda d: d["expectations"][0].update(check={"kind": "range", "min": 5, "max": 1}),
    "negative_tolerance": lambda d: d["expectations"][0].update(check={"kind": "tolerance", "target": 1, "tolerance": -0.1}),
    "number_as_text": lambda d: d["expectations"][0].update(check={"kind": "range", "max": "10"}),
    "bool_as_number": lambda d: d["expectations"][0].update(check={"kind": "range", "max": True}),
    "transition_same_values": lambda d: d["expectations"][0].update(check={"kind": "transition", "from_value": "A", "to_value": "A", "deadline_ns": 1}),
    "transition_zero_deadline": lambda d: d["expectations"][0].update(check={"kind": "transition", "from_value": "A", "to_value": "B", "deadline_ns": 0}),
    "window_reversed": lambda d: d["expectations"][0].update(window={"start_ns": 9, "end_ns": 1}),
    "window_negative": lambda d: d["expectations"][0].update(window={"start_ns": -1}),
    "empty_signal": lambda d: d["expectations"][0].update(signal=""),
    "signal_with_brackets": lambda d: d["expectations"][0].update(signal="a[0]"),
    "signal_wildcard": lambda d: d["expectations"][0].update(signal="a.*"),
    "no_clock_domain": lambda d: d["expectations"][0].pop("clock_domain"),
    "payload_time_without_field": lambda d: d["expectations"][0].update(time_source="payload_field"),
    "time_field_without_payload_time": lambda d: d["expectations"][0].update(time_field="stamp"),
    "prereq_with_payload_clock": lambda d: d["expectations"][0].update(
        time_source="payload_field", time_field="s", applies_when=[{"topic": "/m", "signal": "v", "op": "eq", "value": 1}]),
    "prereq_ordering_op_on_text": lambda d: d["expectations"][0].update(applies_when=[{"topic": "/m", "signal": "v", "op": "lt", "value": "x"}]),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_an_invalid_contract_is_reported_and_nothing_is_evaluated(name, one, monkeypatch):
    d = _bad(INVALID[name])
    if name == "not_a_mapping":
        d = ["a", "list"]
    loaded = parse_contract(d)
    assert loaded.contract is None and loaded.problems
    inv = inspect_evidence(one)
    import physicore_collector.investigator.engine as eng
    monkeypatch.setattr(eng, "load_evidence", lambda *a, **k: (_ for _ in ()).throw(AssertionError("evidence was read for an invalid contract")))
    r = investigate(inv, loaded)
    assert r.status is Status.INVALID_EXPECTATION and r.status_reason_code == "contract_invalid"
    assert r.expectation_results == [] and r.first_divergence is None and r.candidates == [] and r.contract is None and r.evidence is None
    assert r.contract_problems and exit_code(r) == 3


def test_one_invalid_expectation_fails_the_whole_contract_closed(one):
    d = contract([expectation("GOOD", check={"kind": "range", "max": 10}), expectation("BAD", check={"kind": "range"})])
    r = investigate(inspect_evidence(one), parse_contract(d))
    assert r.status is Status.INVALID_EXPECTATION and r.first_divergence is None  # GOOD would have diverged; it is not evaluated
    assert any("expectations.1" in (p.location or "") for p in r.contract_problems)


def test_loading_contract_files(tmp_path):
    good = contract([expectation()])
    jp = tmp_path / "c.json"
    jp.write_text(json.dumps(good))
    lj = load_contract(jp)
    assert lj.contract is not None and len(lj.sha256) == 64
    import yaml
    yp = tmp_path / "c.yaml"
    yp.write_text(yaml.safe_dump(good))
    ly = load_contract(yp)
    assert ly.contract is not None and ly.sha256 == lj.sha256  # the same contract in either syntax has the same digest
    for name, content, code in [("m.json", "{not json", "contract_malformed"), ("n.json", '{"a": NaN}', "contract_malformed"),
                                ("l.json", "[1]", "contract_not_a_mapping"), ("b.json", b"\xff\xfe", "contract_unreadable")]:
        p = tmp_path / name
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
        out = load_contract(p)
        assert out.contract is None and out.problems[0].code == code, name
    assert load_contract(tmp_path / "absent.json").problems[0].code == "contract_unreadable"
    (tmp_path / "ln.json").symlink_to(jp)
    assert load_contract(tmp_path / "ln.json").problems[0].code == "contract_unreadable"
    assert load_contract(tmp_path).problems[0].code == "contract_unreadable"
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (1024 * 1024 + 1))
    assert load_contract(big).problems[0].code == "contract_too_large"


def test_numbers_keep_their_exact_text_and_types(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"contract_schema_version":"1.0","contract_id":"x","expectations":[{"expectation_id":"E","description":"d","topic":"/t","signal":"v",'
                 '"clock_domain":"mcap_log_time","check":{"kind":"equals","value":"5"},"source":{"kind":"operator_supplied","reference":"r"}}]}')
    c = load_contract(p).contract
    assert c.expectations[0].check.value == "5" and isinstance(c.expectations[0].check.value, str)  # the text "5" is not the number 5
    c2 = parse_contract(_bad(lambda d: d["expectations"][0].update(check={"kind": "equals", "value": True}))).contract
    assert c2.expectations[0].check.value is True


# ------------------------------------------------------------------ typed comparisons, blind spots, prerequisites

def test_equals_is_type_strict(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/s": [(T(0), {"v": 5}), (T(1), {"v": "5"})]})
    r = run(p, [expectation(topic="/s", check={"kind": "equals", "value": 5})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE  # "5" is text, not the number 5: it cannot be judged, and it is not called a mismatch
    assert by_id(r, "E1").blind_spots[0].code == "value_type_mismatch"
    q = write_mcap(tmp_path / "q.mcap", {"/s": [(T(0), {"v": "OK"}), (T(1), {"v": "FAULT"})]})
    f = run(q, [expectation(topic="/s", check={"kind": "equals", "value": "OK"})]).first_divergence
    assert f.discrepancy == "observed 'FAULT' differs from 'OK'" and f.time.ns == T(1)


def test_a_blind_spot_before_a_mismatch_stops_the_earliest_claim(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), b"{broken"), (T(2), {"v": 99})]})
    r = run(p, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"
    assert r.candidates and r.candidates[0].uncertainty and "earlier mismatch cannot be excluded" in r.candidates[0].uncertainty[0]


def test_a_blind_spot_after_the_mismatch_does_not_matter(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), {"v": 99}), (T(2), b"{broken")]})
    r = run(p, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.DIVERGENCE_FOUND and r.first_divergence.time.ns == T(1)


def test_non_json_payloads_are_not_interpreted(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/cdr": series([1, 2, 3])}, encodings={"/cdr": "cdr"})
    r = run(p, [expectation(topic="/cdr")])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and by_id(r, "E1").reason_code == "payload_encoding_not_supported"


def test_nested_and_indexed_signal_paths(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/js": [(T(0), {"pos": [0.1, 0.2], "a": {"b": 3}}), (T(1), {"pos": [0.1, 7.5], "a": {"b": 3}})]})
    r = run(p, [expectation(topic="/js", signal="pos.1", check={"kind": "range", "max": 1})])
    assert r.first_divergence.time.ns == T(1) and r.first_divergence.observations[0].signal == "pos.1"
    assert run(p, [expectation(topic="/js", signal="a.b", check={"kind": "equals", "value": 3})]).status is Status.NO_DIVERGENCE


MODE = [{"topic": "/mode", "signal": "v", "op": "eq", "value": "AUTO"}]


def _mode_file(tmp_path, temps, modes):
    # mode samples sit 50 ms BEFORE the temp samples, so each temp sample has an unambiguous latest-earlier mode
    return write_mcap(tmp_path / "r.mcap", {"/temp": series(temps, start=BASE + 50 * MS), "/mode": series(modes)})


def test_prerequisites_restrict_where_the_expectation_applies(tmp_path):
    p = _mode_file(tmp_path, [1, 99, 99, 99], ["AUTO", "MANUAL", "MANUAL", "AUTO"])
    r = run(p, [expectation(check={"kind": "range", "max": 10}, applies_when=MODE)])
    f = r.first_divergence
    assert f.time.ns == BASE + 50 * MS + 3 * 100 * MS  # the 99 samples under MANUAL do not count
    assert by_id(r, "E1").not_applicable_count == 2
    pre = [s for s in f.sources if s.role == "prerequisite"]
    assert len(pre) == 1 and pre[0].topic == "/mode" and pre[0].observation_id.startswith("obs:r.mcap#")
    assert [o.signal for o in f.observations] == ["v", "v"] and f.observations[1].topic == "/mode"
    assert "while /mode.v eq 'AUTO'" in f.explanation


def test_a_prerequisite_with_no_earlier_sample_is_unknown_not_assumed(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([99], start=BASE), "/mode": series(["AUTO"], start=BASE + 10 * MS)})
    r = run(p, [expectation(check={"kind": "range", "max": 10}, applies_when=MODE)])
    assert r.status is Status.INSUFFICIENT_EVIDENCE
    assert by_id(r, "E1").blind_spots[0].code == "prerequisite_state_unknown_before_window"


# ------------------------------------------------------------------ 10. missing / altered artifact references

def test_a_segment_without_an_artifact_reference_is_not_read(one):
    inv = inspect_evidence(one)
    inv.recording.segments[0].artifact_ref = None
    r = investigate(inv, parse_contract(contract([expectation(check={"kind": "range", "max": 10})])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "no artifact reference" in r.evidence.segments[0].reason


def test_an_artifact_missing_from_the_inventory_is_not_read(one):
    inv = inspect_evidence(one)
    inv.artifacts = []
    r = investigate(inv, parse_contract(contract([expectation()])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "no artifact reference" in r.evidence.segments[0].reason


def test_a_file_deleted_after_the_inventory_is_reported(one):
    inv = inspect_evidence(one)
    one.unlink()
    r = investigate(inv, parse_contract(contract([expectation()])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "missing" in r.evidence.segments[0].reason


def test_a_file_changed_after_the_inventory_is_not_trusted(one):
    inv = inspect_evidence(one)
    one.write_bytes(build_json_mcap({"/temp": series([1, 2, 3, 4, 5, 6])}))  # different bytes: no divergence in them
    r = investigate(inv, parse_contract(contract([expectation(check={"kind": "range", "max": 10})])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "no longer match the digest" in r.evidence.segments[0].reason


def test_a_message_count_that_disagrees_with_the_inventory_is_not_used(one):
    inv = inspect_evidence(one)
    inv.recording.segments[0].observed_message_count = 7
    r = investigate(inv, parse_contract(contract([expectation()])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "the two disagree" in r.evidence.segments[0].reason


def test_a_symlinked_segment_is_not_followed(tmp_path):
    real = write_mcap(tmp_path / "elsewhere" / "r.mcap", {"/temp": series([99])})
    bag = tmp_path / "bagl"
    bag.mkdir()
    inv_src = write_mcap(tmp_path / "plain" / "r.mcap", {"/temp": series([99])})
    shutil.copy(inv_src, bag / "r.mcap")
    inv = inspect_evidence(bag)
    (bag / "r.mcap").unlink()
    (bag / "r.mcap").symlink_to(real)  # identical bytes, but reached through a symlink
    r = investigate(inv, parse_contract(contract([expectation(check={"kind": "range", "max": 10})])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "symlink" in r.evidence.segments[0].reason


# ------------------------------------------------------------------ packages, incidents, split bags: provenance

def _package(tmp_path, streams, synth_profile, name="pkg"):
    src = write_mcap(tmp_path / "src" / "rec.mcap", streams)
    export_package(analyze_session(src, synth_profile), tmp_path / name, now=NOW)
    return tmp_path / name


def test_package_findings_point_at_the_packaged_recording(tmp_path, synth_profile):
    pkg = _package(tmp_path, {"/temp": series([1, 2, 3, 12])}, synth_profile)
    r = run(pkg, [expectation(check={"kind": "range", "max": 10})])
    f = r.first_divergence
    assert r.status is Status.DIVERGENCE_FOUND and r.evidence.input_kind == "package" and r.evidence.integrity_state == "verified_now"
    rel = f.sources[0].relative_path
    assert rel.startswith("recording/") and rel.endswith("rec.mcap")
    assert f.sources[0].artifact_sha256 == sha256_file(pkg / rel)
    assert f.sources[0].artifact_ref == f"artifact:{rel}"


def test_a_tampered_package_artifact_is_not_used(tmp_path, synth_profile):
    pkg = _package(tmp_path, {"/temp": series([1, 2, 3, 12])}, synth_profile)
    rel = next(a.relative_path for a in inspect_evidence(pkg).artifacts if a.kind == "mcap_recording")
    inv_clean = inspect_evidence(pkg)
    f = pkg / rel
    f.write_bytes(build_json_mcap({"/temp": series([1, 2, 3, 4])}))
    r = run(pkg, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.evidence.integrity_state == "tampered"
    assert "integrity is 'mismatch'" in r.evidence.segments[0].reason
    r2 = investigate(inv_clean, parse_contract(contract([expectation()])))  # stale inventory, changed bytes
    assert "no longer match" in r2.evidence.segments[0].reason


def test_incident_input_is_investigated_through_its_package(tmp_path, synth_profile):
    src = write_mcap(tmp_path / "src" / "rec.mcap", {"/temp": series([1, 2, 3, 12])})
    inc_dir = prep(src, synth_profile, tmp_path / "inc").incident_dir
    r = run(inc_dir, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.DIVERGENCE_FOUND and r.evidence.input_kind == "incident"
    assert r.first_divergence.sources[0].relative_path.startswith("package/recording/")
    assert Path(inc_dir / r.first_divergence.sources[0].relative_path).is_file()


def test_split_bag_findings_name_the_segment_that_holds_the_sample(tmp_path):
    s0 = {"/temp": series([1, 2, 3], start=BASE)}
    s1 = {"/temp": series([4, 99, 5], start=BASE + 300 * MS)}
    bag = write_json_bag(tmp_path / "bagz", [s0, s1])
    r = run(bag, [expectation(check={"kind": "range", "max": 10})])
    f = r.first_divergence
    assert f.sources[0].relative_path == "bagz_1.mcap" and f.sources[0].artifact_sha256 == sha256_file(bag / "bagz_1.mcap")
    assert f.observations[0].observation_id == "obs:bagz_1.mcap#1" and f.time.ns == BASE + 400 * MS
    assert [s.state for s in r.evidence.segments] == ["usable_complete", "usable_complete"]


def test_segments_are_merged_by_timestamp_not_by_file_order(tmp_path):
    s0 = {"/temp": series([1, 99], start=BASE + 500 * MS)}  # first file holds the LATER samples
    s1 = {"/temp": series([1, 99], start=BASE)}
    bag = write_json_bag(tmp_path / "bagr", [s0, s1])
    r = run(bag, [expectation(check={"kind": "range", "max": 10})])
    assert r.first_divergence.time.ns == BASE + 100 * MS and r.first_divergence.sources[0].relative_path == "bagr_1.mcap"


def test_finding_ids_depend_on_the_evidence_bytes_and_the_contract(tmp_path):
    a = write_mcap(tmp_path / "a" / "r.mcap", {"/temp": series([1, 99])})
    b = write_mcap(tmp_path / "b" / "r.mcap", {"/temp": series([1, 98])})
    e = [expectation(check={"kind": "range", "max": 10})]
    ia, ib = run(a, e).first_divergence.finding_id, run(b, e).first_divergence.finding_id
    assert ia != ib
    assert ia != run(a, [expectation(check={"kind": "range", "max": 11})]).first_divergence.finding_id


# ------------------------------------------------------------------ 11. repeatability

def test_identical_inputs_give_identical_output(one):
    e = [expectation("A", check={"kind": "range", "max": 10}), expectation("B", check={"kind": "range", "min": 2})]
    outs = {investigation_json(run(one, e)) for _ in range(3)}
    assert len(outs) == 1
    assert render_investigation(run(one, e)) == render_investigation(run(one, e))


def test_output_is_the_same_for_a_byte_identical_copy_elsewhere(tmp_path):
    a = write_mcap(tmp_path / "x" / "r.mcap", {"/temp": series([1, 99])})
    b = tmp_path / "y" / "deeper"
    b.mkdir(parents=True)
    shutil.copy(a, b / "r.mcap")
    e = [expectation(check={"kind": "range", "max": 10})]
    assert investigation_json(run(a, e)) == investigation_json(run(b / "r.mcap", e))  # no host path or time leaks into the result


def test_no_wall_clock_or_randomness_in_the_output(one, monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: (_ for _ in ()).throw(AssertionError("wall clock read")))
    monkeypatch.setattr(time, "time_ns", lambda: (_ for _ in ()).throw(AssertionError("wall clock read")))
    run(one, [expectation(check={"kind": "range", "max": 10})])


# ------------------------------------------------------------------ honesty of the output schema

def _keys(x, acc=None):
    acc = set() if acc is None else acc
    if isinstance(x, dict):
        for k, v in x.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(x, list):
        for v in x:
            _keys(v, acc)
    return acc


def test_the_output_asserts_no_root_cause_confidence_or_fix(one):
    d = json.loads(investigation_json(run(one, [expectation(check={"kind": "range", "max": 10})])))
    assert not {k for k in _keys(d) if any(w in k.lower() for w in ("confidence", "probab", "fix", "remediation", "cause_of", "likely"))}
    assert d["provenance"]["root_cause_claimed"] is False and d["provenance"]["live_ros2_capture_verified"] is False
    assert d["provenance"]["read_only"] is True and d["investigation_schema_version"] == "1.0"
    assert any("not a root cause" in s for s in d["not_established"]) and any("does not show the system behaved correctly" in s for s in d["not_established"])


def test_every_finding_carries_what_the_brief_requires(one):
    f = json.loads(investigation_json(run(one, [expectation(check={"kind": "range", "max": 10})])))["first_divergence"]
    for k in ("finding_id", "expectation_id", "expected", "observed", "discrepancy", "time", "sources", "observations", "rule", "explanation", "uncertainty"):
        assert k in f, k
    assert {"ns", "clock_domain", "timestamp_basis"} <= set(f["time"]) and {"artifact_ref", "relative_path", "topic", "observation_id"} <= set(f["sources"][0])
    assert {"check_kind", "comparison", "parameters"} <= set(f["rule"])


def test_all_five_statuses_exist_and_are_exercised(one, tmp_path):
    seen = {run(one, [expectation(check={"kind": "range", "max": 10})]).status, run(one, [expectation(check={"kind": "range", "max": 100})]).status,
            run(one, [expectation(topic="/absent")]).status, run(one, [expectation(clock_domain="other")]).status,
            invalid_result([]).status}
    assert seen == set(Status)


# ------------------------------------------------------------------ read-only

def test_investigation_is_read_only_for_every_input_kind(tmp_path, synth_profile, monkeypatch):
    raw = write_mcap(tmp_path / "raw" / "r.mcap", {"/temp": series([1, 99])})
    bag = write_json_bag(tmp_path / "bag", [{"/temp": series([1, 99])}])
    pkg = _package(tmp_path, {"/temp": series([1, 99])}, synth_profile)
    inc = prep(write_mcap(tmp_path / "s2" / "rec.mcap", {"/temp": series([1, 99])}), synth_profile, tmp_path / "inc").incident_dir
    exps = [expectation(check={"kind": "range", "max": 10})]
    cpath = tmp_path / "c.json"
    cpath.write_text(json.dumps(contract(exps)))
    targets = [raw, bag, pkg, inc, cpath]
    before = [tree_state(t) for t in targets]
    parent_before = tree_state(tmp_path / "raw")
    forbid_writes(monkeypatch)  # raises on any write, rename, delete, chmod, utime, lock...
    for t in (raw, bag, pkg, inc):
        r = investigate(inspect_evidence(t), load_contract(cpath))
        assert r.status is Status.DIVERGENCE_FOUND
    monkeypatch.undo()
    assert [tree_state(t) for t in targets] == before and tree_state(tmp_path / "raw") == parent_before


def test_cli_investigation_is_read_only_and_writes_nothing_into_the_package(tmp_path, synth_profile):
    pkg = _package(tmp_path, {"/temp": series([1, 99])}, synth_profile)
    cpath = tmp_path / "c.json"
    cpath.write_text(json.dumps(contract([expectation(check={"kind": "range", "max": 10})])))
    before = tree_state(pkg)
    res = runner.invoke(app, ["evidence", "investigate", str(pkg), "-e", str(cpath), "--json"])
    assert res.exit_code == 1 and tree_state(pkg) == before
    assert not list(pkg.rglob("investigation*"))


# ------------------------------------------------------------------ CLI

def test_cli_exit_codes_and_json(tmp_path, one):
    def call(exps, *extra):
        c = tmp_path / "c.json"
        c.write_text(json.dumps(contract(exps)) if exps is not None else "{broken")
        return runner.invoke(app, ["evidence", "investigate", str(one), "-e", str(c), *extra])

    r = call([expectation(check={"kind": "range", "max": 10})], "--json")
    assert r.exit_code == 1 and json.loads(r.output)["status"] == "divergence_found"
    assert call([expectation(check={"kind": "range", "max": 100})]).exit_code == 0
    assert call([expectation(topic="/absent")]).exit_code == 2
    assert call([expectation(clock_domain="x")]).exit_code == 2
    r = call(None)
    assert r.exit_code == 3 and "invalid_expectation" in r.output and "contract_malformed" in r.output
    rj = call(None, "--json")
    assert json.loads(rj.output)["status"] == "invalid_expectation"
    assert "Traceback" not in rj.output
    assert runner.invoke(app, ["evidence", "investigate", str(one)]).exit_code != 0  # --expectations is required


def test_cli_with_a_missing_input_is_a_structured_result(tmp_path):
    c = tmp_path / "c.json"
    c.write_text(json.dumps(contract([expectation()])))
    r = runner.invoke(app, ["evidence", "investigate", str(tmp_path / "nope"), "-e", str(c)])
    assert r.exit_code == 2 and "insufficient_evidence" in r.output and "Traceback" not in r.output


def test_cli_accepts_a_yaml_contract(tmp_path, one):
    import yaml
    c = tmp_path / "c.yaml"
    c.write_text(yaml.safe_dump(contract([expectation(check={"kind": "range", "max": 10})])))
    assert runner.invoke(app, ["evidence", "investigate", str(one), "-e", str(c)]).exit_code == 1


# ------------------------------------------------------------------ the Collector is untouched

def test_the_investigator_does_not_modify_collector_modules():
    """The Investigator only imports from the Collector. It must not be imported BY the Collector's core modules."""
    import importlib
    src = Path(importlib.import_module("physicore_collector").__file__).parent
    core = [p for p in src.glob("*.py") if p.name != "cli.py"]
    assert core and not [p.name for p in core if "investigator" in p.read_text()]


# ------------------------------------------------------------------ exactness of the blind-spot rule, input forms

def test_many_blind_spots_after_the_mismatch_do_not_block_the_earliest_claim(tmp_path):
    msgs = [(T(0), {"v": 99})] + [(T(i), b"{broken") for i in range(1, 40)]  # 39 undecodable samples, all AFTER the mismatch
    r = run(write_mcap(tmp_path / "r.mcap", {"/temp": msgs}), [expectation(check={"kind": "range", "max": 10})])
    assert by_id(r, "E1").blind_spot_count == 39 and len(by_id(r, "E1").blind_spots) == 20  # the list is capped; the count is not
    assert r.status is Status.DIVERGENCE_FOUND


def test_many_blind_spots_before_the_mismatch_block_it_even_when_the_list_is_capped(tmp_path):
    msgs = [(T(i), b"{broken") for i in range(0, 39)] + [(T(50), {"v": 99})]
    r = run(write_mcap(tmp_path / "r.mcap", {"/temp": msgs}), [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"
    assert by_id(r, "E1").first_blind_spot_ns == T(0)


def test_a_blind_spot_in_another_expectation_before_the_mismatch_blocks_the_claim(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": series([1, 2, 99]), "/pressure": [(T(0), {"v": 1}), (T(1), b"{broken"), (T(2), {"v": 1})]})
    r = run(p, [expectation("A", topic="/temp", check={"kind": "range", "max": 10}), expectation("B", topic="/pressure", check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "expectations: B" in r.status_reason  # B may have diverged at T(1), before A's T(2)


def test_an_incident_can_be_given_as_its_incident_json(tmp_path, synth_profile):
    src = write_mcap(tmp_path / "src" / "rec.mcap", {"/temp": series([1, 99])})
    inc_dir = prep(src, synth_profile, tmp_path / "inc").incident_dir
    r = run(inc_dir / "incident.json", [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.DIVERGENCE_FOUND and r.first_divergence.sources[0].relative_path.startswith("package/recording/")


def test_a_blind_spot_at_the_same_instant_as_the_mismatch_blocks_the_claim(tmp_path):
    p = write_mcap(tmp_path / "r.mcap", {"/temp": [(T(0), {"v": 1}), (T(1), {"v": 99}), (T(1), b"{broken")]})
    r = run(p, [expectation(check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"  # equal timestamps are never ordered


# ------------------------------------------------------------------ the committed example

def test_committed_example_matches_the_generator_and_the_cli_reproduces_it(tmp_path):
    from tests.make_investigator_example import write_example
    ex = Path(__file__).parent.parent / "examples" / "investigator"
    write_example(tmp_path / "gen")
    for name in ("example_recording.mcap", "expectations.json", "expected_output.json", "expected_report.txt"):
        assert (tmp_path / "gen" / name).read_bytes() == (ex / name).read_bytes(), f"examples/investigator/{name} is stale: run python -m tests.make_investigator_example"
    res = runner.invoke(app, ["evidence", "investigate", str(ex / "example_recording.mcap"), "-e", str(ex / "expectations.json"), "--json"])
    assert res.exit_code == 1 and res.output == (ex / "expected_output.json").read_text()
    out = json.loads(res.output)
    assert out["first_divergence"]["expectation_id"] == "ARM-TEMP-LIMIT" and [c["expectation_id"] for c in out["candidates"]] == ["ARM-TEMP-LIMIT", "GRIPPER-CLOSES-IN-TIME"]
