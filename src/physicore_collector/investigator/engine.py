"""Deterministic first-divergence engine (result schema 1.0).

Input: an EvidenceInventory (Phase 1) and an explicit expectation contract. Output: an InvestigationResult that says, per
expectation and overall, one of five things and why:

  divergence_found                         a mismatch is established AND the earliest one can be defended
  no_divergence_observed_in_covered_window nothing contradicted the expectations in the evidence actually evaluated.
                                           This is NOT a statement that the system is correct.
  insufficient_evidence                    evidence missing/incomplete/unreadable/undecodable, or a mismatch exists but
                                           which one came first cannot be established
  incomparable_timebases                   the expectation's clock cannot be related to the evidence's clock (or confirmed
                                           divergences sit on clocks that cannot be ordered against each other)
  invalid_expectation                      the contract does not validate; nothing was evaluated (fail closed)

Rules that keep it honest (each has a test):
  * No clock is merged, shifted or converted. Two clock domains are comparable only when they are the same domain.
  * Equal timestamps are never ordered. Ties between divergences, and prerequisites sampled at the same instant, are
    reported as ambiguous, not resolved by file order.
  * A sample that cannot be evaluated (undecodable, field missing, wrong type, no timestamp) is a BLIND SPOT. A blind spot at
    or before a mismatch means that mismatch is not defended as the earliest; a blind spot in the window prevents a
    "no divergence" conclusion.
  * Incomplete evidence (truncated / missing / changed segment) never yields "no divergence", and never an earliest claim.
  * Expectations that could not be evaluated do not hide a confirmed mismatch, but are listed as uncertainty on it.
  * No root cause, confidence score, fix or causal claim is produced. The first divergence is a first divergence only.
  * Read-only; output depends only on the inputs (no wall clock, no randomness).
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_serializer

from ..inventory import EvidenceInventory
from ..schemas import ToolInfo, _Strict
from .contract import (
    CONTRACT_SCHEMA_VERSION, Contract, ContractProblem, EqualsCheck, EventResponseCheck, Expectation, LoadedContract, Prerequisite,
    RangeCheck, ToleranceCheck, TransitionCheck, canonical_json,
)
from . import cdr as cdrmod
from .observations import (
    LOG_TIME_DOMAIN, TIME_TYPE, Evidence, Observation, Sample, decode_message, extract, load_evidence, value_type,
)
from .signal_registry import RegistryError, SignalRegistry

INVESTIGATION_SCHEMA_VERSION = "1.0"
MAX_LISTED_BLIND_SPOTS = 20


class Status(str, Enum):
    DIVERGENCE_FOUND = "divergence_found"
    NO_DIVERGENCE = "no_divergence_observed_in_covered_window"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INCOMPARABLE_TIMEBASES = "incomparable_timebases"
    INVALID_EXPECTATION = "invalid_expectation"


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

class TimePoint(_Strict):
    ns: int
    clock_domain: str
    timestamp_basis: str
    kind: Literal["observation", "deadline_expiry"]  # deadline_expiry: the instant by which an expected event had not happened


class Rule(_Strict):
    check_kind: str
    comparison: str  # plain-language statement of the comparison, including inclusivity
    parameters: dict[str, str]
    tolerance_used: str | None = None


class SourceRef(_Strict):
    artifact_ref: str
    relative_path: str | None
    artifact_sha256: str | None
    topic: str
    channel_id: int
    observation_id: str
    role: Literal["divergent_observation", "transition_entry", "prerequisite", "trigger_event", "late_response"]


class BlindSpot(_Strict):
    code: str
    message: str
    observation_id: str | None = None
    time_ns: int | None = None


class CoveredWindow(_Strict):
    clock_domain: str
    start_ns: int
    end_ns: int


class Finding(_Strict):
    finding_id: str  # "FND-<16 hex>": a hash of the expectation + segment bytes + observation; stable for the same inputs
    expectation_id: str
    expectation_kind: str
    description: str
    rule: Rule
    expected: str
    observed: str
    discrepancy: str
    time: TimePoint
    sources: list[SourceRef]
    observations: list[Observation]
    explanation: str
    uncertainty: list[str] = Field(default_factory=list)
    ordering: Literal["earliest_defended", "earliest_not_defended", "tied_for_earliest", "later_than_earliest",
                      "unordered_across_clock_domains"] = "earliest_not_defended"


class ExpectationResult(_Strict):
    expectation_id: str
    status: Status
    reason_code: str
    reason: str
    clock_domain: str | None = None  # the domain of the samples
    declared_window_domain: str | None = None
    covered_window: CoveredWindow | None = None
    evaluated_observation_count: int = 0
    not_applicable_count: int = 0  # samples skipped because applies_when did not hold
    blind_spot_count: int = 0
    blind_spots: list[BlindSpot] = Field(default_factory=list)  # first MAX_LISTED_BLIND_SPOTS, in time order
    first_blind_spot_ns: int | None = None  # earliest placed blind spot (over ALL of them, not only the listed ones)
    blind_spot_unplaced: bool = False  # some blind spot has no usable timestamp, so it may lie before anything
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_complete: bool = False
    finding: Finding | None = None
    trigger_event_count: int | None = None  # event_response only: trigger events found in the window (omitted from JSON otherwise)
    response_matched_count: int | None = None  # event_response only: of those, how many had a response inside the deadline
    # header_stamp expectations only: whether the stamp field's ROS type was verified from an embedded schema, or only its JSON structure was checked
    stamp_type_check: str | None = None

    @model_serializer(mode="wrap")
    def _omit_event_response_counts(self, handler: Any) -> Any:  # other checks keep their Phase 2 JSON bytes
        d = handler(self)
        for k in ("trigger_event_count", "response_matched_count", "stamp_type_check"):
            if d.get(k) is None:
                d.pop(k, None)
        return d


class SegmentSummary(_Strict):
    artifact_ref: str | None
    relative_path: str
    state: Literal["usable_complete", "usable_partial", "unusable"]
    reason: str | None = None
    sha256: str | None = None
    messages_read: int = 0


class DecoderStatus(_Strict):
    name: str
    available: bool
    version: str | None = None
    reason: str | None = None


class TopicDecoding(_Strict):
    """How one (topic, message encoding, schema) was decoded. Counts are for the messages actually decoded during this investigation."""

    topic: str
    message_encoding: str
    schema_name: str | None
    schema_encoding: str | None
    schema_sha256: str | None  # of the schema bytes embedded in the file
    status: str  # cdr.DecodeStatus: `ok`, or why this topic cannot be decoded
    detail: str | None = None
    messages_attempted: int = 0
    messages_decoded: int = 0
    failures: dict[str, int] = Field(default_factory=dict)


class PayloadDecoding(_Strict):
    decoder: DecoderStatus
    topics: list[TopicDecoding]


class EvidenceSummary(_Strict):
    input_kind: str
    input_name: str
    inventory_schema_version: str
    inspection_state: str
    integrity_state: str
    content_basis: str
    log_time_basis: str
    recording_readability: str | None
    evidence_complete: bool
    log_time_coverage: CoveredWindow | None = None
    segments: list[SegmentSummary] = Field(default_factory=list)
    incomplete_reasons: list[str] = Field(default_factory=list)
    blocked: str | None = None
    payload_decoding: PayloadDecoding | None = None  # present only when some non-JSON (CDR) payload was involved

    @model_serializer(mode="wrap")
    def _omit_absent_payload_decoding(self, handler: Any) -> Any:  # JSON-payload investigations keep their Phase 2 bytes
        d = handler(self)
        if d.get("payload_decoding") is None:
            d.pop("payload_decoding", None)
        return d


class ContractSummary(_Strict):
    contract_id: str
    schema_version: str
    sha256: str
    expectation_count: int


class InvestigationProvenance(_Strict):
    tool: ToolInfo = Field(default_factory=ToolInfo)
    read_only: Literal[True] = True
    root_cause_claimed: Literal[False] = False
    live_ros2_capture_verified: Literal[False] = False


class InvestigationResult(_Strict):
    investigation_schema_version: str = INVESTIGATION_SCHEMA_VERSION
    status: Status
    status_reason_code: str
    status_reason: str
    first_divergence: Finding | None = None  # present only when status is divergence_found
    candidates: list[Finding] = Field(default_factory=list)  # every confirmed mismatch (one per expectation), earliest first
    expectation_results: list[ExpectationResult] = Field(default_factory=list)  # contract order
    contract: ContractSummary | None = None
    contract_problems: list[ContractProblem] = Field(default_factory=list)
    evidence: EvidenceSummary | None = None
    not_evaluated: list[str] = Field(default_factory=list)  # expectation ids that could not be evaluated
    not_established: list[str] = Field(default_factory=list)
    provenance: InvestigationProvenance = Field(default_factory=InvestigationProvenance)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any) -> str:
    return str(v) if isinstance(v, Decimal) else ("true" if v is True else "false" if v is False else repr(v) if isinstance(v, str) else "none")


def _same_type(a: Any, b: Any) -> bool:
    return value_type(a) == value_type(b) and value_type(a) != "none"


def _eq(a: Any, b: Any) -> bool:
    return _same_type(a, b) and a == b


class _Ctx:
    """Per-investigation caches. Decoding each message once keeps evaluation O(messages)."""

    def __init__(self, ev: Evidence, inv: EvidenceInventory, registry: SignalRegistry | None = None) -> None:
        self.ev = ev
        self.inv = inv
        self.registry = registry
        self._decoded: dict[tuple[str, int], Any] = {}
        self._series: dict[tuple[str, str], tuple[list[int], list[Sample]]] = {}
        self.paths = {a.ref_id: a.relative_path for a in inv.artifacts}

    def decoded(self, m) -> Any:
        k = (m.artifact_ref, m.index)
        if k not in self._decoded:
            self._decoded[k] = decode_message(m)
        return self._decoded[k]

    def samples(self, topic: str, signal: str, time_source: str, time_field: str | None) -> list[Sample]:
        out: list[Sample] = []
        definition = self.registry.resolve(signal) if self.registry is not None else None
        if definition is not None and definition.topic != topic:
            raise RegistryError(
                f"registered signal {signal!r} belongs to topic {definition.topic!r}, not contract topic {topic!r}"
            )
        field_path = definition.field_path if definition is not None else signal
        for m in self.ev.messages.get(topic, []):
            value, quality, ts, _basis, _dom, ros_type = extract(m, self.decoded(m), field_path, time_source, time_field)
            source_value = str(value) if definition is not None and value is not None else None
            source_unit = definition.source_unit or definition.unit if definition is not None else None
            normalized_unit = definition.unit if definition is not None else None
            conversion = "identity" if definition is not None and definition.unit is not None and isinstance(value, Decimal) else None
            robot_family = self.registry.robot_family if definition is not None and self.registry is not None else None
            canonical_signal = definition.canonical_name if definition is not None else None
            signal_semantic = definition.semantic if definition is not None else None
            source_field_path = definition.field_path if definition is not None else None
            if definition is not None and isinstance(value, Decimal) and definition.unit is not None:
                try:
                    normalized = self.registry.normalize(signal, value, source_unit=source_unit)
                    source_value = normalized.source_value
                    normalized_unit = normalized.normalized_unit
                    conversion = normalized.conversion
                    robot_family = normalized.robot_family
                    value = Decimal(normalized.normalized_value)
                except RegistryError:
                    value, quality = None, "type_unsupported"
            out.append(Sample(ts=ts, seq=(m.order, m.index), value=value, quality=quality, channel_id=m.channel_id,
                              artifact_ref=m.artifact_ref, segment_sha256=m.segment_sha256, parser_status=m.parser_status,
                              ros_type=ros_type, source_value=source_value, source_unit=source_unit,
                              normalized_unit=normalized_unit, unit_conversion=conversion, robot_family=robot_family,
                              canonical_signal=canonical_signal, signal_semantic=signal_semantic, source_field_path=source_field_path))
        out.sort(key=lambda s: (s.ts is None, s.ts if s.ts is not None else 0, s.seq))
        return out

    def log_series(self, topic: str, signal: str) -> tuple[list[int], list[Sample]]:
        k = (topic, signal)
        if k not in self._series:
            ss = [s for s in self.samples(topic, signal, "log_time", None)]
            self._series[k] = ([s.ts for s in ss if s.ts is not None], ss)
        return self._series[k]


def _obs_id(ctx: _Ctx, s: Sample) -> str:
    return f"obs:{ctx.paths.get(s.artifact_ref, s.artifact_ref)}#{s.seq[1]}"


def _observation(ctx: _Ctx, s: Sample, topic: str, signal: str, basis: str, domain: str) -> Observation:
    return Observation(observation_id=_obs_id(ctx, s), artifact_ref=s.artifact_ref, topic=topic, channel_id=s.channel_id,
                       message_index=s.seq[1], signal=signal, value=s.value, value_type=value_type(s.value), timestamp_ns=s.ts,
                       timestamp_basis=basis, clock_domain=domain, quality=s.quality, parser_status=s.parser_status, ros_type=s.ros_type,
                       source_value=s.source_value, source_unit=s.source_unit, normalized_unit=s.normalized_unit,
                       unit_conversion=s.unit_conversion, robot_family=s.robot_family,
                       canonical_signal=s.canonical_signal, signal_semantic=s.signal_semantic, source_field_path=s.source_field_path)


def _source(ctx: _Ctx, s: Sample, topic: str, role: str) -> SourceRef:
    return SourceRef(artifact_ref=s.artifact_ref, relative_path=ctx.paths.get(s.artifact_ref), artifact_sha256=s.segment_sha256,
                     topic=topic, channel_id=s.channel_id, observation_id=_obs_id(ctx, s), role=role)  # type: ignore[arg-type]


def _cond(v: Any, op: str, ref: Any) -> bool | None:
    """Does value `v` satisfy `op ref`? None when the two cannot be compared (different types): never a guess."""
    if op in ("eq", "ne"):
        if not _same_type(v, ref):
            return None
        return (v == ref) == (op == "eq")
    if not isinstance(v, Decimal) or isinstance(v, bool):
        return None
    return {"lt": v < ref, "le": v <= ref, "gt": v > ref, "ge": v >= ref}[op]


def _prereq(ctx: _Ctx, p: Prerequisite, t: int, host_topic: str) -> tuple[Literal["true", "false", "unknown"], Sample | None, str]:
    times, ss = ctx.log_series(p.topic, p.signal)
    if not ss:
        return "unknown", None, "prerequisite_no_samples"
    lo, hi = bisect_left(times, t), bisect_right(times, t)
    equal = hi - lo
    if equal > (1 if p.topic == host_topic else 0):
        return "unknown", None, "prerequisite_ordering_ambiguous"  # a sample at the very same instant: which came first is unknown
    if lo == 0:
        return "unknown", None, "prerequisite_state_unknown_before_window"
    s = ss[lo - 1]
    if p.max_age_ns is not None and t - s.ts > p.max_age_ns:  # type: ignore[operator]
        return "unknown", s, "prerequisite_stale"  # the latest state is too old to say what holds NOW; it is not assumed to persist
    if s.quality != "ok":
        return "unknown", s, f"prerequisite_sample_{s.quality}"
    ok = _cond(s.value, p.op, p.value)
    if ok is None:
        return "unknown", s, "prerequisite_type_mismatch"
    return ("true" if ok else "false"), s, ""


def _violation(check: Any, v: Any) -> tuple[bool, str, str] | None:
    """-> None when the value cannot be judged (wrong type); else (violates, expected-text, discrepancy-text)."""
    if isinstance(check, RangeCheck):
        if not isinstance(v, Decimal):
            return None
        lo, hi = check.min, check.max
        if lo is not None and v < lo:
            return True, _range_text(check), f"{v} is below the minimum {lo} by {lo - v}"
        if hi is not None and v > hi:
            return True, _range_text(check), f"{v} is above the maximum {hi} by {v - hi}"
        return False, _range_text(check), ""
    if isinstance(check, ToleranceCheck):
        if not isinstance(v, Decimal):
            return None
        diff = abs(v - check.target)
        exp = f"within {check.tolerance} of {check.target}"
        if diff > check.tolerance:
            return True, exp, f"|{v} - {check.target}| = {diff}, which exceeds the tolerance {check.tolerance} by {diff - check.tolerance}"
        return False, exp, ""
    if isinstance(check, EqualsCheck):
        if not _same_type(v, check.value):
            return None
        if v != check.value:
            return True, f"equal to {_fmt(check.value)}", f"observed {_fmt(v)} differs from {_fmt(check.value)}"
        return False, f"equal to {_fmt(check.value)}", ""
    raise AssertionError("not a per-sample check")


def _range_text(c: RangeCheck) -> str:
    if c.min is not None and c.max is not None:
        return f"between {c.min} and {c.max} inclusive"
    return f"at least {c.min}" if c.min is not None else f"at most {c.max}"


def _rule(e: Expectation) -> Rule:
    c = e.check
    if isinstance(c, RangeCheck):
        params = {k: str(v) for k, v in (("min", c.min), ("max", c.max)) if v is not None}
        return Rule(check_kind="range", comparison="value >= min and value <= max (bounds inclusive; exact decimal arithmetic)", parameters=params)
    if isinstance(c, ToleranceCheck):
        return Rule(check_kind="tolerance", comparison="|value - target| <= tolerance (inclusive; exact decimal arithmetic)",
                    parameters={"target": str(c.target), "tolerance": str(c.tolerance)}, tolerance_used=str(c.tolerance))
    if isinstance(c, EqualsCheck):
        return Rule(check_kind="equals", comparison="value == expected, same type required (a number is never equal to its text)",
                    parameters={"value": _fmt(c.value)})
    if isinstance(c, EventResponseCheck):
        r = c.response
        params = {"trigger": f"{e.topic}.{e.signal} {c.trigger_op} {_fmt(c.trigger_value)} (entry observed)",
                  "response": f"{r.topic}.{r.signal} {r.op} {_fmt(r.value)}", "deadline_ns": str(c.deadline_ns),
                  "clock": "mcap_log_time (recorder receipt time) for both trigger and response"}
        return Rule(check_kind="event_response",
                    comparison="after a trigger sample is observed entering its condition, a response sample satisfying its condition must be received strictly "
                               "later and no later than the deadline; receipt times only, not event times", parameters=params)
    assert isinstance(c, TransitionCheck)
    return Rule(check_kind="transition",
                comparison="after a sample enters from_value, a sample equal to to_value must occur strictly later and no later than the deadline",
                parameters={"from_value": _fmt(c.from_value), "to_value": _fmt(c.to_value), "deadline_ns": str(c.deadline_ns)})


def _in_window(ts: int, e: Expectation) -> bool:
    w = e.window
    return (w.start_ns is None or ts >= w.start_ns) and (w.end_ns is None or ts <= w.end_ns)


# ---------------------------------------------------------------------------
# One expectation
# ---------------------------------------------------------------------------

def _result(e: Expectation, status: Status, code: str, reason: str, **kw: Any) -> ExpectationResult:
    return ExpectationResult(expectation_id=e.expectation_id, status=status, reason_code=code, reason=reason, **kw)


_REASON_FOR_STATUS = {
    "message_encoding_unsupported": "payload_encoding_not_supported",
    "schema_encoding_unsupported": "payload_encoding_not_supported",
    "schema_missing": "payload_schema_missing",
    "schema_invalid": "payload_schema_invalid",
    "decoder_unavailable": "payload_decoder_unavailable",
    "payload_type_out_of_scope": "payload_type_out_of_scope",
}


def _topic_decode_problem(ev: Evidence, topic: str, *, host: bool) -> tuple[str, str, str] | None:
    """Why a topic's payloads cannot be decoded at all (whole-topic conditions), or None. Per-message failures are blind spots instead."""
    if host:  # Phase 2 rule, unchanged: an encoding that is neither json nor cdr on any channel of the evaluated topic
        encs = ev.topic_encodings.get(topic, set())
        odd = sorted(x for x in encs if x not in ("json", "cdr"))
        if odd:
            return ("payload_encoding_not_supported",
                    f"topic {topic} uses message encoding {sorted(encs)}; only 'json' and 'cdr' payloads are decoded here, others are not interpreted",
                    "message_encoding_unsupported")
    for c in ev.topic_codecs.get(topic, ()):
        if c.status != "ok":
            return (_REASON_FOR_STATUS.get(c.status, "payload_encoding_not_supported"),
                    f"topic {topic} cannot be decoded: {c.status}" + (f" ({c.detail})" if c.detail else ""), c.status)
    return None


def _expectation_sha(e: Expectation) -> str:
    """Digest of this expectation alone, so a finding's id does not change when other expectations are added or reordered."""
    return hashlib.sha256(json.dumps(e.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _event_response(e: Expectation, ctx: _Ctx, chk: EventResponseCheck, trig: list[Sample], blind_at: Any, prereqs_hold: Any,
                    mismatches: list, extras: dict[tuple[int, int], Sample | None]) -> tuple[int, int, int, int, int]:
    """Cross-topic event -> response check. Returns (trigger events found in the window, of those the ones whose prerequisites held,
    events answered in time, trigger-stream samples evaluated, events skipped because applies_when did not hold). Appends deadline mismatches to `mismatches`; every undecidable case becomes a blind spot.

    Ordering rules (each has a test): a response only counts when STRICTLY later than the trigger on the same clock; a response-stream
    sample that satisfies the condition at the very same timestamp makes the event undecided (equal timestamps are never ordered by file
    order); a response state that already held before the trigger is undecided (it cannot be told whether the trigger produced it); a response
    sample that cannot be decoded may have been the response, so it blocks a 'missing response' conclusion. Entry into the trigger condition
    must be OBSERVED (a predecessor outside the condition); a first sample already inside it proves nothing. Likewise the response state at the
    trigger must be observed: with no response-stream sample before the trigger it is unknown. Everything is on mcap_log_time (receipt time)."""
    ev = ctx.ev
    r = chk.response
    resp = ctx.samples(r.topic, r.signal, "log_time", None)  # sorted by receipt time; every sample has one
    resp_ok = [x for x in resp if x.ts is not None]
    resp_ts = [x.ts for x in resp_ok]
    cov_r = ev.coverage
    evaluated = n_a = entries = events = matched = 0
    trig_ok = [x for x in trig if x.ts is not None]
    for x in trig:
        if x.ts is None:
            blind_at("timestamp_missing", "a trigger-stream sample has no usable timestamp, so it cannot be placed in time", x, None)
    for i, s in enumerate(trig_ok):
        if not _in_window(s.ts, e):  # type: ignore[arg-type]
            continue
        if s.quality != "ok":
            blind_at(s.quality, f"a trigger-stream sample in the window could not be evaluated ({s.quality})", s, s.ts)
            continue
        c = _cond(s.value, chk.trigger_op, chk.trigger_value)
        if c is None:
            blind_at("value_type_mismatch", f"the observed value is {value_type(s.value)}, which this trigger condition cannot judge", s, s.ts)
            continue
        if not c:
            evaluated += 1
            continue
        prev = trig_ok[i - 1] if i > 0 else None
        if prev is None:
            blind_at("event_entry_unknown", "this is the first trigger-stream sample and it already satisfies the trigger condition: nothing observed shows it entered "
                                            "that condition inside the recording", s, s.ts)
            continue
        if prev is not None and prev.ts == s.ts:
            blind_at("event_ordering_ambiguous", "another trigger-stream sample shares this timestamp; whether this is an entry into the trigger condition is unknown", s, s.ts)
            continue
        if prev is not None:
            pc = _cond(prev.value, chk.trigger_op, chk.trigger_value) if prev.quality == "ok" else None
            if pc is None:
                blind_at("event_entry_unknown", "the previous trigger-stream sample could not be evaluated, so whether this sample entered the trigger condition is unknown", s, s.ts)
                continue
            if pc:
                evaluated += 1
                continue  # the condition already held: not a new event
        entries += 1
        holds, refs = prereqs_hold(s)
        if holds is None:
            continue
        if not holds:
            n_a += 1
            continue
        evaluated += 1
        events += 1
        t0 = s.ts
        deadline = t0 + chk.deadline_ns  # type: ignore[operator]
        pre: list[tuple[str, str, Sample, int | None]] = []  # problems at or before the trigger: the later response could be a pre-existing state
        lo, hi = bisect_left(resp_ts, t0), bisect_right(resp_ts, t0)
        for q in resp_ok[lo:hi]:
            qc = _cond(q.value, r.op, r.value) if q.quality == "ok" else None
            if q.quality != "ok":
                pre.append((q.quality, "a response-stream sample at the trigger's timestamp could not be decoded; its order against the trigger is unknown", q, t0))
            elif qc is None:
                pre.append(("value_type_mismatch", "a response-stream sample at the trigger's timestamp has a type this response condition cannot judge", q, t0))
            elif qc:
                pre.append(("event_response_ordering_ambiguous", "a response-stream sample satisfying the response shares the trigger's timestamp; which came first is unknown", q, t0))
        if lo == 0:
            pre.append(("response_state_unknown", "no response-stream sample precedes the trigger, so the response state at the trigger is unknown and a later "
                                                  "matching sample cannot be attributed to it", s, t0))
        if lo > 0:
            prior = resp_ok[lo - 1]
            pcond = _cond(prior.value, r.op, r.value) if prior.quality == "ok" else None
            if prior.quality != "ok":
                pre.append(("response_state_unknown", "the response-stream sample before the trigger could not be decoded, so the response state at the trigger is unknown", prior, t0))
            elif pcond is None:
                pre.append(("value_type_mismatch", "the response-stream sample before the trigger has a type this response condition cannot judge", prior, t0))
            elif pcond:
                pre.append(("response_already_satisfied", "the response condition already held before the trigger, so a later matching sample cannot be attributed to it", prior, t0))
        if pre:  # the response state at the trigger is not established, so no later sample can be attributed to this trigger
            for code, msg, who, at in pre:
                blind_at(code, msg, who, at)
            continue
        pending: list[tuple[str, str, Sample, int | None]] = []
        found: Sample | None = None
        for q in resp_ok[hi:]:
            if q.ts > deadline:  # type: ignore[operator]
                break
            if q.quality != "ok":
                pending.append((q.quality, "a response-stream sample inside the deadline could not be decoded; it may have been the response", q, q.ts))
                continue
            qc = _cond(q.value, r.op, r.value)
            if qc is None:
                pending.append(("value_type_mismatch", "a response-stream sample inside the deadline has a type this response condition cannot judge", q, q.ts))
            elif qc:
                found = q
                break
        if found is not None:
            matched += 1  # a response inside the deadline settles the event; problems with other samples do not change that
            continue
        if pending:
            for code, msg, who, at in pending:
                blind_at(code, msg, who, at)
            continue
        if not (ev.complete and cov_r is not None and deadline <= cov_r[1]):
            blind_at("deadline_beyond_covered_evidence", f"the deadline ({deadline} ns) is not covered by complete evidence on the response clock, so the absence of a response is not established", s, deadline)
            continue
        late = next((q for q in resp_ok[bisect_right(resp_ts, deadline):] if q.quality == "ok" and _cond(q.value, r.op, r.value) is True), None)
        expected = (f"{r.topic}.{r.signal} {r.op} {_fmt(r.value)} within {chk.deadline_ns} ns after "
                    f"{e.topic}.{e.signal} {chk.trigger_op} {_fmt(chk.trigger_value)} was entered")
        if late is not None:
            disc = (f"the trigger was received at {t0} ns; the first matching response in the recorded stream was received at {late.ts} ns, {late.ts - t0} ns later, "  # type: ignore[operator]
                    f"which exceeds the deadline {chk.deadline_ns} ns by {late.ts - deadline} ns")  # type: ignore[operator]
        else:
            disc = (f"the trigger was received at {t0} ns and no matching response was observed in the recorded stream by {deadline} ns "
                    f"(deadline {chk.deadline_ns} ns, receipt times)")
        extras[s.seq] = late
        mismatches.append((deadline, s.seq, s, expected, disc, refs, "deadline_expiry"))
    return entries, events, matched, evaluated, n_a



def _stamp_type_check(ev: Evidence, topic: str, field: str) -> str:
    """What is actually known about the type of a header_stamp field, from the embedded schema. `schema_verified` only when every message of the
    topic is CDR, every codec decoded, and the schema declares exactly this field as builtin_interfaces/msg/Time. Never describes undecoded CDR as JSON."""
    msgs = ev.messages.get(topic, [])
    codecs: list[cdrmod.Codec] = []
    for m in msgs:
        if m.codec is not None and not any(m.codec is c for c in codecs):
            codecs.append(m.codec)
    if not codecs:
        return (f"structure_only: the payload is not schema-typed (JSON), so the ROS type of '{field}' cannot be verified; only that "
                f"'{field}.sec' and '{field}.nanosec' are in-range integers was checked")
    if any(m.codec is None for m in msgs):
        return f"mixed_payloads: the topic has both JSON and CDR messages, so the type of '{field}' was not verified for all of them"
    steps = cdrmod.parse_signal(field) or []
    verdicts: dict[str, str] = {}
    for c in codecs:
        if c.status != "ok":
            verdicts.setdefault("payload_not_decoded", f"payload_not_decoded: the CDR payloads of this topic could not be decoded ({c.status}), so the type of '{field}' was not checked")
            continue
        kind, ty = cdrmod.field_type_at(c.types, c.root_type, steps)
        if kind == "ok" and ty == TIME_TYPE:
            verdicts.setdefault("schema_verified", f"schema_verified: the embedded schema declares '{field}' as {TIME_TYPE}")
        elif kind == "ok":
            verdicts.setdefault("rejected_type", f"rejected_type: the embedded schema declares '{field}' as {ty}, not {TIME_TYPE}; its values are not used as a clock")
        elif kind == "field_missing":
            verdicts.setdefault("field_not_in_schema", f"field_not_in_schema: the embedded schema of {c.root_type} has no field '{field}'; nothing is used as a clock")
        else:
            verdicts.setdefault("schema_unresolved", f"schema_unresolved: the embedded schema could not be resolved as far as '{field}', so its type is unknown and was not verified")
    for key in ("rejected_type", "field_not_in_schema", "schema_unresolved", "payload_not_decoded", "schema_verified"):
        if key in verdicts:
            if key == "schema_verified" and len(verdicts) > 1:
                continue
            return verdicts[key]
    return "schema_unresolved: no usable schema"


def _evaluate(e: Expectation, ctx: _Ctx) -> ExpectationResult:
    ev, inv = ctx.ev, ctx.inv
    domain = LOG_TIME_DOMAIN if e.time_source == "log_time" else f"payload_stamp:{e.topic}:{e.time_field}"
    basis = {"log_time": "mcap_log_time", "payload_field": f"payload_field:{e.time_field}", "header_stamp": f"header_stamp:{e.time_field}"}[e.time_source]
    er = e.check if isinstance(e.check, EventResponseCheck) else None
    base = dict(clock_domain=domain, declared_window_domain=e.clock_domain, evidence_complete=ev.complete)
    stamp_check = _stamp_type_check(ev, e.topic, e.time_field or "") if e.time_source == "header_stamp" else None
    stamp_note: str | None = None
    if stamp_check is not None:
        base["stamp_type_check"] = stamp_check
        if not stamp_check.startswith("schema_verified"):
            stamp_note = stamp_check  # anything short of a verified Time is stated on the finding too

    if e.clock_domain != domain:
        return _result(e, Status.INCOMPARABLE_TIMEBASES, "window_clock_domain_differs",
                       f"the expectation's window is written in clock domain '{e.clock_domain}' but its samples are in '{domain}'; "
                       "no offset or conversion between clocks is assumed", **base)
    if ev.blocked:
        return _result(e, Status.INSUFFICIENT_EVIDENCE, "evidence_unavailable", ev.blocked, missing_evidence=[ev.blocked], **base)
    topics_known = {t.topic for t in inv.topics}
    needed = [e.topic] + [p.topic for p in e.applies_when] + ([er.response.topic] if er is not None else [])
    missing = []
    for t in dict.fromkeys(needed):
        if not ev.messages.get(t):
            missing.append(f"no messages of topic {t} were available" + ("" if t in topics_known else " (the topic is not in the recording)"))
    if missing:
        return _result(e, Status.INSUFFICIENT_EVIDENCE, "required_observations_missing", "; ".join(missing), missing_evidence=missing, **base)
    for t in dict.fromkeys(needed):
        problem = _topic_decode_problem(ev, t, host=(t == e.topic or (er is not None and t == er.response.topic)))
        if problem is not None:
            code, text, status = problem
            return _result(e, Status.INSUFFICIENT_EVIDENCE, code, text, missing_evidence=[f"decoded values for {t}"], **base)

    samples = ctx.samples(e.topic, e.signal, e.time_source, e.time_field)
    blind: list[BlindSpot] = []
    evaluated = n_a = 0
    mismatches: list[tuple[int, tuple[int, int], Sample, str, str, list[tuple[Sample, Prerequisite]], str]] = []
    entries = events = matched = 0
    extras: dict[tuple[int, int], Sample | None] = {}  # event_response: trigger seq -> the late (after-deadline) matching response, if any
    stamps = [s.ts for s in samples if s.ts is not None]
    if e.time_source == "log_time":
        cov = ev.coverage
    else:
        cov = (min(stamps), max(stamps)) if stamps else None

    def blind_at(code: str, msg: str, s: Sample | None, t: int | None) -> None:
        blind.append(BlindSpot(code=code, message=msg, observation_id=_obs_id(ctx, s) if s is not None else None, time_ns=t))

    def prereqs_hold(s: Sample) -> tuple[bool | None, list[tuple[Sample, Prerequisite]]]:
        """True/False if decided, None if undecidable (a blind spot has been recorded)."""
        refs: list[tuple[Sample, Prerequisite]] = []
        for p in e.applies_when:
            st, ps, why = _prereq(ctx, p, s.ts, e.topic)  # type: ignore[arg-type]
            if st == "unknown":
                blind_at(why, f"prerequisite {p.topic}.{p.signal} {p.op} {_fmt(p.value)} could not be decided at this sample ({why})", s, s.ts)
                return None, refs
            if ps is not None:
                refs.append((ps, p))
            if st == "false":
                return False, refs
        return True, refs

    check = e.check
    if isinstance(check, EventResponseCheck):
        entries, events, matched, evaluated, n_a = _event_response(e, ctx, check, samples, blind_at, prereqs_hold, mismatches, extras)
    elif not isinstance(check, TransitionCheck):
        for s in samples:
            if s.ts is None:
                blind_at("timestamp_missing", "a sample has no usable timestamp, so it cannot be placed in the window", s, None)
                continue
            if not _in_window(s.ts, e):
                continue
            if s.quality != "ok":
                blind_at(s.quality, f"a sample in the window could not be evaluated ({s.quality})", s, s.ts)
                continue
            holds, refs = prereqs_hold(s)
            if holds is None:
                continue
            if not holds:
                n_a += 1
                continue
            judged = _violation(check, s.value)
            if judged is None:
                blind_at("value_type_mismatch", f"the observed value is {value_type(s.value)}, which this {check.kind} check cannot judge", s, s.ts)
                continue
            evaluated += 1
            if judged[0]:
                mismatches.append((s.ts, s.seq, s, judged[1], judged[2], refs, "observation"))
    else:
        ok = [s for s in samples if s.ts is not None]
        for s in samples:
            if s.ts is None:
                blind_at("timestamp_missing", "a sample has no usable timestamp, so it cannot be placed in time", s, None)
        for i, s in enumerate(ok):
            if not _in_window(s.ts, e):  # type: ignore[arg-type]
                continue
            if s.quality != "ok":
                blind_at(s.quality, f"a sample in the window could not be evaluated ({s.quality})", s, s.ts)
                continue
            if not _eq(s.value, check.from_value):
                evaluated += 1
                continue
            prev = ok[i - 1] if i > 0 else None
            if prev is not None and prev.ts == s.ts:
                blind_at("transition_ordering_ambiguous", "another sample shares this timestamp; whether this is an entry into from_value is unknown", s, s.ts)
                continue
            if prev is not None and prev.quality != "ok":
                blind_at("transition_entry_unknown", "the previous sample could not be decoded, so whether this sample entered from_value is unknown", s, s.ts)
                continue
            if prev is not None and _eq(prev.value, check.from_value):
                evaluated += 1
                continue  # already in from_value: not a new entry
            holds, refs = prereqs_hold(s)
            if holds is None:
                continue
            if not holds:
                n_a += 1
                continue
            evaluated += 1
            t0 = s.ts  # type: ignore[assignment]
            deadline = t0 + check.deadline_ns
            found = False
            undecided = False
            for later in ok[i + 1:]:
                if later.ts > deadline:  # type: ignore[operator]
                    break
                if later.quality != "ok":
                    blind_at(later.quality, "a sample inside the transition deadline could not be decoded; it may have carried to_value", later, later.ts)
                    undecided = True
                elif later.ts == t0 and _eq(later.value, check.to_value):  # type: ignore[comparison-overlap]
                    blind_at("transition_ordering_ambiguous", "to_value appears at the same timestamp as the entry into from_value; their order is unknown", later, later.ts)
                    undecided = True
                elif _eq(later.value, check.to_value) and later.ts > t0:  # type: ignore[operator]
                    found = True
                    break
            if found or undecided:
                continue
            if not (ev.complete and cov is not None and deadline <= cov[1]):
                blind_at("deadline_beyond_covered_evidence", f"the deadline ({deadline} ns) is not covered by complete evidence, so the absence of to_value is not established", s, deadline)
                continue
            mismatches.append((deadline, s.seq, s, f"{_fmt(check.to_value)} within {check.deadline_ns} ns after entering {_fmt(check.from_value)}",
                               f"{_fmt(check.from_value)} was entered at {t0} ns and {_fmt(check.to_value)} was not observed by {deadline} ns",
                               refs, "deadline_expiry"))

    blind.sort(key=lambda b: (b.time_ns is None, b.time_ns or 0, b.observation_id or "", b.code))
    covered = None
    if cov is not None:
        lo = cov[0] if e.window.start_ns is None else max(cov[0], e.window.start_ns)
        hi = cov[1] if e.window.end_ns is None else min(cov[1], e.window.end_ns)
        if lo <= hi:
            covered = CoveredWindow(clock_domain=domain, start_ns=lo, end_ns=hi)
    placed = [b.time_ns for b in blind if b.time_ns is not None]
    common = dict(covered_window=covered, evaluated_observation_count=evaluated, not_applicable_count=n_a, blind_spot_count=len(blind),
                  blind_spots=blind[:MAX_LISTED_BLIND_SPOTS], first_blind_spot_ns=min(placed) if placed else None,
                  blind_spot_unplaced=len(placed) != len(blind), **base)
    if er is not None:
        common.update(trigger_event_count=entries, response_matched_count=matched)

    if mismatches:
        mismatches.sort(key=lambda m: (m[0], m[1]))
        t, _seq, s, expected, discrepancy, refs, kind = mismatches[0]
        relevant = [b for b in blind if b.time_ns is None or b.time_ns <= t]
        unc = []
        if stamp_note:
            unc.append(stamp_note)
        if not ev.complete:
            unc.append("the evidence is incomplete: " + "; ".join(ev.incomplete_reasons[:3]) + "; messages not read could be earlier")
        if relevant:
            unc.append(f"{len(relevant)} sample(s) at or before this time could not be evaluated, so an earlier mismatch cannot be excluded")
        obs = _observation(ctx, s, e.topic, e.signal, basis, domain)
        what = ("the observed value" if kind == "observation" else "the trigger event" if er is not None else "the sample that entered the from_value")
        late = extras.get(s.seq) if er is not None else None
        extra_src: list[SourceRef] = []
        extra_obs: list[Observation] = []
        if late is not None and er is not None:
            extra_src.append(_source(ctx, late, er.response.topic, "late_response"))
            extra_obs.append(_observation(ctx, late, er.response.topic, er.response.signal, "mcap_log_time", LOG_TIME_DOMAIN))
        if er is not None:
            unc.append("both times are mcap log_time, the recorder's RECEIPT times: they show the order in which the recorder received the messages, "
                       "not when the events physically occurred, and receipt order can differ from event order")
            if late is None:
                unc.append(NO_RESPONSE_CAVEAT)
        fnd = Finding(
            finding_id="FND-" + hashlib.sha256("|".join([_expectation_sha(e), s.segment_sha256, obs.observation_id, kind]).encode()).hexdigest()[:16],
            expectation_id=e.expectation_id, expectation_kind=check.kind, description=e.description, rule=_rule(e), expected=expected,
            observed=f"{e.topic}.{e.signal} = {_fmt(s.value)} at {s.ts} ns ({basis})", discrepancy=discrepancy,
            time=TimePoint(ns=t, clock_domain=domain, timestamp_basis=basis, kind=kind),  # type: ignore[arg-type]
            sources=[_source(ctx, s, e.topic, "divergent_observation" if kind == "observation" else "trigger_event" if er is not None else "transition_entry")]
                    + extra_src + [_source(ctx, ps, p.topic, "prerequisite") for ps, p in refs],
            observations=[obs] + extra_obs + [_observation(ctx, ps, p.topic, p.signal, "mcap_log_time", LOG_TIME_DOMAIN) for ps, p in refs],
            explanation=(f"Expectation {e.expectation_id} requires " + (f"that {expected}" if er is not None else f"{e.topic}.{e.signal} to be {expected}")
                         + (f" while {', '.join(f'{p.topic}.{p.signal} {p.op} {_fmt(p.value)}' for p in e.applies_when)}" if e.applies_when else "")
                         + f". {what} contradicts it: {discrepancy}. This is the first mismatch of this expectation in the evidence evaluated; "
                           "it is not a statement about cause."),
            uncertainty=unc)
        return _result(e, Status.DIVERGENCE_FOUND, "mismatch_established", discrepancy, finding=fnd, **common)

    if er is not None and events == 0 and not blind:
        return _result(e, Status.INSUFFICIENT_EVIDENCE, "no_trigger_event_in_window",
                       "the trigger event never occurred inside the window while the prerequisites held, so the response requirement was not exercised"
                       + (f" ({n_a} trigger(s) skipped because applies_when did not hold)" if n_a else ""),
                       missing_evidence=["a trigger event inside the window while the prerequisites hold"], **common)
    if evaluated == 0:
        return _result(e, Status.INSUFFICIENT_EVIDENCE, "no_evaluable_observations_in_window",
                       "no sample inside the window could be evaluated" + (f" ({n_a} skipped because applies_when did not hold)" if n_a else "")
                       + (f"; {len(blind)} sample(s) could not be evaluated" if blind else ""),
                       missing_evidence=["evaluable samples of the signal inside the window while the prerequisites hold"], **common)
    if blind:
        return _result(e, Status.INSUFFICIENT_EVIDENCE, "unevaluated_observations_in_window",
                       f"{len(blind)} sample(s) in the window could not be evaluated, so no-divergence cannot be stated for it", **common)
    if not ev.complete:
        return _result(e, Status.INSUFFICIENT_EVIDENCE, "evidence_incomplete",
                       "no mismatch among the messages that could be read, but the evidence is incomplete: " + "; ".join(ev.incomplete_reasons[:3]),
                       missing_evidence=list(ev.incomplete_reasons[:5]), **common)
    if covered is None:
        return _result(e, Status.INSUFFICIENT_EVIDENCE, "window_not_covered", "the declared window lies outside the evidence", **common)
    return _result(e, Status.NO_DIVERGENCE, "no_mismatch_in_covered_window",
                   f"{evaluated} sample(s) were evaluated in {covered.start_ns}..{covered.end_ns} ns ({domain}); none contradicted the expectation. "
                   "This does not show the behaviour was correct outside what was evaluated.", **common)


# ---------------------------------------------------------------------------
# Whole investigation
# ---------------------------------------------------------------------------

def _standing_statements(inv: EvidenceInventory | None) -> list[str]:
    basis = inv.timebase.log_time_basis if inv is not None else "unknown"
    return [
        "A first divergence is the earliest established mismatch with an expectation. It is not a root cause; no causal relationship is asserted.",
        "'No divergence observed' means no mismatch was established in the expectations and evidence actually evaluated. It does not show the system behaved correctly.",
        "Expectations are exactly those supplied in the contract; none was derived from the recording.",
        JSON_ONLY_STATEMENT,
        f"Clock domains are never merged or converted. The relation of MCAP log_time (declared basis: '{basis}') to wall-clock time or any other clock is not established.",
        "Evidence integrity is as established by the inventory and by re-hashing each segment at investigation time; it is not a statement about truthfulness.",
        "Live ROS 2 capture is not verified.",
    ]


NO_RESPONSE_CAVEAT = ("the recording shows that no matching response was received by the deadline; it cannot show whether the response was never published "
                      "(the publisher was silent or stopped) or was published but not captured (dropped, not recorded, lost in transport or storage): the two are "
                      "indistinguishable here")
JSON_ONLY_STATEMENT = "Only json-encoded message payloads are decoded. Other encodings (for example CDR) are not interpreted."
CDR_DECODED_STATEMENT = ("JSON payloads, and CDR payloads that carry an embedded ros2msg schema (with the optional decoder installed), are decoded; "
                         "other encodings and schema formats are not interpreted.")


def _decoding_aware(statements: list[str], ev: Evidence) -> list[str]:
    """The Phase 2 sentence says only JSON is decoded. Replace it, and only it, when CDR messages were actually decoded in this run, so a
    JSON-only investigation keeps its Phase 2 bytes."""
    if any(c.decoded > 0 for cs in ev.topic_codecs.values() for c in cs):
        return [CDR_DECODED_STATEMENT if x == JSON_ONLY_STATEMENT else x for x in statements]
    return statements


def _timing_statements(c: Contract) -> list[str]:
    """Extra standing statements, only for contracts that use cross-topic timing or payload stamps (other contracts keep their Phase 2 text)."""
    uses_er = any(isinstance(e.check, EventResponseCheck) for e in c.expectations)
    uses_stamp = any(e.time_source != "log_time" for e in c.expectations)
    out: list[str] = []
    if uses_er:
        out.append("An event_response result compares mcap log_time, the recorder's receipt time, for both trigger and response. It is not the time an event "
                   "physically occurred, and receipt order is not event order; the result says what the recorder received, not what the robot did.")
        out.append("A response is a later matching sample. It is not shown to be caused by the trigger. A response absent from the recording is not shown to have "
                   "been unpublished: a silent publisher and a message that was published but not captured look the same in a recording.")
    if uses_stamp:
        out.append("A payload stamp (header.stamp) is the publisher's own claim about when something happened. Each payload clock is its own domain and is "
                   "never compared with another clock or with log_time; no offset is inferred. For JSON payloads the ROS type of a stamp field cannot be "
                   "verified, only its sec/nanosec structure; a stamp of zero is read as zero (it may be a simulation time or an unset stamp).")
    return out


def _payload_decoding(ev: Evidence) -> PayloadDecoding | None:
    rows = []
    for topic in sorted(ev.topic_codecs):
        for c in ev.topic_codecs[topic]:
            rows.append(TopicDecoding(
                topic=topic, message_encoding=c.message_encoding, schema_name=c.schema.name or None, schema_encoding=c.schema.encoding or None,
                schema_sha256=c.schema.sha256 or None, status=c.status, detail=c.detail, messages_attempted=c.attempted,
                messages_decoded=c.decoded, failures=dict(sorted(c.failures.items()))))
    if not rows:
        return None
    rows.sort(key=lambda r: (r.topic, r.message_encoding, r.schema_sha256 or ""))
    info = cdrmod.decoder_info()
    return PayloadDecoding(decoder=DecoderStatus(name=info.name, available=info.available, version=info.version, reason=info.reason), topics=rows)


def _summary(inv: EvidenceInventory, ev: Evidence) -> EvidenceSummary:
    return EvidenceSummary(
        input_kind=inv.provenance.input.kind, input_name=inv.provenance.input.name, inventory_schema_version=inv.inventory_schema_version,
        inspection_state=inv.inspection.state, integrity_state=inv.integrity.state, content_basis=inv.provenance.content_basis,
        log_time_basis=inv.timebase.log_time_basis, recording_readability=inv.recording.readability.value if inv.recording.readability else None,
        evidence_complete=ev.complete,
        log_time_coverage=CoveredWindow(clock_domain=LOG_TIME_DOMAIN, start_ns=ev.coverage[0], end_ns=ev.coverage[1]) if ev.coverage else None,
        segments=[SegmentSummary(artifact_ref=s.artifact_ref, relative_path=s.relative_path, state=s.state, reason=s.reason, sha256=s.sha256,
                                 messages_read=s.messages_read) for s in ev.segments],
        incomplete_reasons=list(ev.incomplete_reasons), blocked=ev.blocked, payload_decoding=_payload_decoding(ev))


def invalid_result(problems: list[ContractProblem]) -> InvestigationResult:
    return InvestigationResult(
        status=Status.INVALID_EXPECTATION, status_reason_code="contract_invalid",
        status_reason=f"the expectation contract is not valid ({len(problems)} problem(s)); nothing was evaluated",
        contract_problems=problems, not_established=_standing_statements(None))


def investigate(inventory: EvidenceInventory, loaded: LoadedContract, *, evidence_root: Path | None = None,
                registry: SignalRegistry | None = None) -> InvestigationResult:
    """Run one investigation. Read-only; never raises for bad content (it is reported in the result)."""
    if loaded.contract is None or loaded.sha256 is None:
        return invalid_result(loaded.problems)
    c: Contract = loaded.contract
    summary = ContractSummary(contract_id=c.contract_id, schema_version=c.contract_schema_version, sha256=loaded.sha256,
                              expectation_count=len(c.expectations))
    wanted = ({e.topic for e in c.expectations} | {p.topic for e in c.expectations for p in e.applies_when}
              | {e.check.response.topic for e in c.expectations if isinstance(e.check, EventResponseCheck)})
    ev = load_evidence(inventory, wanted, evidence_root)
    ctx = _Ctx(ev, inventory, registry)
    results = [_evaluate(e, ctx) for e in c.expectations]
    not_eval = [r.expectation_id for r in results if r.status in (Status.INSUFFICIENT_EVIDENCE, Status.INCOMPARABLE_TIMEBASES)]
    findings = [r.finding for r in results if r.finding is not None]
    base = dict(expectation_results=results, contract=summary, evidence=_summary(inventory, ev), not_evaluated=not_eval,
                not_established=_decoding_aware(_standing_statements(inventory), ev) + _timing_statements(c))

    if findings:
        domains = sorted({f.time.clock_domain for f in findings})
        if len(domains) > 1:
            for f in findings:
                f.ordering = "unordered_across_clock_domains"
            return InvestigationResult(
                status=Status.INCOMPARABLE_TIMEBASES, status_reason_code="divergences_on_incomparable_clocks",
                status_reason=f"confirmed mismatches exist on {len(domains)} clock domains that are not established to be comparable ({', '.join(domains)}); "
                              "which came first cannot be established", candidates=sorted(findings, key=lambda f: (f.time.clock_domain, f.time.ns, f.finding_id)),
                **base)
        findings.sort(key=lambda f: (f.time.ns, f.finding_id))
        t = findings[0].time.ns
        tied = [f for f in findings if f.time.ns == t]
        dom = domains[0]
        reasons: list[str] = []
        if len(tied) > 1:
            reasons.append(f"{len(tied)} mismatches share the timestamp {t} ns ({', '.join(f.expectation_id for f in tied)}); their order is not established")
        for r in results:
            if r.clock_domain == dom:
                if not r.evidence_complete:
                    reasons.append("the evidence is incomplete, so a mismatch with an earlier timestamp may exist in messages that could not be read")
                    break
        blinded = sorted(r.expectation_id for r in results if r.clock_domain == dom
                         and (r.blind_spot_unplaced or (r.first_blind_spot_ns is not None and r.first_blind_spot_ns <= t)))
        if blinded:
            reasons.append(f"samples at or before that time could not be evaluated (expectations: {', '.join(blinded)}), so an earlier mismatch cannot be excluded")
        for f in findings:
            f.ordering = "later_than_earliest"
        if reasons:
            for f in tied:
                f.ordering = "tied_for_earliest" if len(tied) > 1 else "earliest_not_defended"
            return InvestigationResult(
                status=Status.INSUFFICIENT_EVIDENCE, status_reason_code="earliest_divergence_not_defensible",
                status_reason="a mismatch with an expectation is established, but the earliest one cannot be defended: " + "; ".join(dict.fromkeys(reasons)),
                candidates=findings, **base)
        first = findings[0]
        first.ordering = "earliest_defended"
        if not_eval:
            first.uncertainty.append(f"{len(not_eval)} expectation(s) could not be evaluated ({', '.join(not_eval)}); an earlier divergence among them cannot be excluded")
        return InvestigationResult(
            status=Status.DIVERGENCE_FOUND, status_reason_code="earliest_divergence_defended",
            status_reason=f"the earliest established mismatch is expectation {first.expectation_id} at {first.time.ns} ns ({first.time.clock_domain}): {first.discrepancy}",
            first_divergence=first, candidates=findings, **base)

    statuses = {r.status for r in results}
    if statuses == {Status.NO_DIVERGENCE}:
        return InvestigationResult(
            status=Status.NO_DIVERGENCE, status_reason_code="no_mismatch_in_covered_window",
            status_reason="no mismatch with any supplied expectation was established in the evidence evaluated. This does not show the system is correct.", **base)
    if Status.INSUFFICIENT_EVIDENCE in statuses:
        return InvestigationResult(status=Status.INSUFFICIENT_EVIDENCE, status_reason_code="not_all_expectations_evaluable",
                                   status_reason=f"no mismatch was established, but {len(not_eval)} expectation(s) could not be evaluated: {', '.join(not_eval)}", **base)
    return InvestigationResult(status=Status.INCOMPARABLE_TIMEBASES, status_reason_code="expectation_clock_not_comparable",
                               status_reason=f"expectation(s) could not be placed on the evidence's clock: {', '.join(not_eval)}", **base)


def investigation_json(r: InvestigationResult) -> str:
    """Deterministic JSON (stable field order, no wall-clock time)."""
    return r.model_dump_json(indent=2) + "\n"


def exit_code(r: InvestigationResult) -> int:
    """0 = no divergence observed in the covered window; 1 = divergence found; 2 = inconclusive (insufficient evidence or
    incomparable timebases); 3 = the contract is invalid."""
    return {Status.NO_DIVERGENCE: 0, Status.DIVERGENCE_FOUND: 1, Status.INSUFFICIENT_EVIDENCE: 2, Status.INCOMPARABLE_TIMEBASES: 2,
            Status.INVALID_EXPECTATION: 3}[r.status]


def render_investigation(r: InvestigationResult) -> str:
    """Concise readable report. Everything shown is in the JSON; this adds no analysis."""
    L: list[str] = []
    a = L.append
    a(f"Investigation: {r.status.value}")
    a(f"  {r.status_reason}")
    if r.contract:
        a(f"Contract: {r.contract.contract_id} (schema {r.contract.schema_version}, {r.contract.expectation_count} expectation(s), sha256 {r.contract.sha256[:16]}...)")
    if r.evidence:
        e = r.evidence
        a(f"Evidence: {e.input_kind} '{e.input_name}'; inspection {e.inspection_state}; integrity {e.integrity_state}; "
          f"{'complete' if e.evidence_complete else 'INCOMPLETE'}; log_time basis '{e.log_time_basis}'")
        for s in e.segments:
            a(f"  segment {s.relative_path}: {s.state}" + (f" ({s.reason})" if s.reason else ""))
    for p in r.contract_problems:
        a(f"  contract problem [{p.code}]{' ' + p.location if p.location else ''}: {p.message}")
    if r.first_divergence:
        f = r.first_divergence
        a("")
        a(f"First divergence {f.finding_id} (expectation {f.expectation_id}, {f.expectation_kind}):")
        a(f"  expected:    {f.expected}")
        a(f"  observed:    {f.observed}")
        a(f"  discrepancy: {f.discrepancy}")
        a(f"  time:        {f.time.ns} ns on {f.time.clock_domain} ({f.time.kind})")
        a(f"  rule:        {f.rule.comparison}  {f.rule.parameters}")
        for s in f.sources:
            a(f"  source:      {s.role}: {s.relative_path} topic {s.topic} [{s.observation_id}]")
        for u in f.uncertainty:
            a(f"  uncertainty: {u}")
    elif r.candidates:
        a("")
        a("Confirmed mismatches (earliest not defended):")
        for f in r.candidates:
            a(f"  {f.finding_id} {f.expectation_id} at {f.time.ns} ns on {f.time.clock_domain}: {f.discrepancy} [{f.ordering}]")
    if r.expectation_results:
        a("")
        a("Per expectation:")
        for x in r.expectation_results:
            a(f"  {x.expectation_id}: {x.status.value} [{x.reason_code}]")
    a("")
    a("Not established:")
    for s in r.not_established:
        a(f"  - {s}")
    return "\n".join(L) + "\n"
