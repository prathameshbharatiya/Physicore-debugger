"""Symptom normalisation (DESIGN 4.1, FR-SYM). One symptom per source finding, never merged or dropped silently. Times are copied from the
source (never recomputed); the onset rule per category follows the verified table in docs/M3D_LOCALIZATION.md (Step 2 verification record).
This module never opens an artifact and never decodes a payload."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import StrictBool, StrictInt, StrictStr, field_validator, model_validator

from ..anomaly.clock import LOG_TIME_DOMAIN
from ..anomaly.findings import AnomalyReport, Finding as M3cFinding
from ..investigator.engine import Finding as M2Finding, InvestigationResult
from .intervals import Interval
from .report import IntervalModel, Strict, canonical_json
from .vocabulary import NORMALISER_VERSION, ONSET_BASES, SYMPTOM_SOURCES

M3C_CATEGORIES_HANDLED = ("log_severity", "diagnostic_status", "goal_status", "power_status", "publishing_gap", "rate_change",
                          "timestamp_regression", "timestamp_duplicate", "array_length_change", "non_finite_value")


class EvidenceRef(Strict):
    """An observation reference carried verbatim from the source finding."""
    artifact_ref: StrictStr
    sha256: Optional[StrictStr] = None
    topic: StrictStr
    message_index: StrictInt
    time_ns: Optional[StrictInt] = None
    clock_domain: Optional[StrictStr] = None
    role: Optional[StrictStr] = None                     # M2 source role (divergent_observation, trigger_event, ...)


class SourceNote(Strict):
    code: StrictStr
    params: Optional[dict[StrictStr, StrictInt | StrictStr]] = None


class SourceCoverage(Strict):
    topic_coverage: Literal["complete", "incomplete"]
    incomplete_reasons: list[StrictStr] = []
    messages_examined: StrictInt = 0
    messages_undecodable: StrictInt = 0


class Placement(Strict):
    lane: StrictStr
    onset: IntervalModel
    onset_basis: StrictStr

    @model_validator(mode="after")
    def _ok(self) -> "Placement":
        if self.onset.domain != self.lane:
            raise ValueError("a placement's interval must be in its own lane")
        if self.onset_basis not in ONSET_BASES:
            raise ValueError(f"onset_basis must be one of {ONSET_BASES}")
        return self


class Symptom(Strict):
    symptom_id: StrictStr
    source: StrictStr
    source_id: StrictStr
    detector_or_rule: StrictStr
    category: StrictStr
    topic: StrictStr
    node: StrictStr                                      # the topic here; a declared binding may re-home it (Step 6)
    identities: dict[StrictStr, StrictStr] = {}          # kind -> value, only known (untruncated) identities; inert data
    severity: StrictStr
    severity_basis: StrictStr
    extent: IntervalModel
    onset: IntervalModel
    onset_basis: StrictStr
    secondary_onsets: list[Placement] = []
    evidence: list[EvidenceRef]
    source_coverage: SourceCoverage
    source_uncertainty: list[SourceNote] = []
    notes: list[StrictStr] = []                          # M3d uncertainty codes (onset_interval_width, ...)
    truncated: StrictBool = False

    @field_validator("source")
    @classmethod
    def _src(cls, v: str) -> str:
        if v not in SYMPTOM_SOURCES:
            raise ValueError(f"source must be one of {SYMPTOM_SOURCES}")
        return v

    @field_validator("onset_basis")
    @classmethod
    def _basis(cls, v: str) -> str:
        if v not in ONSET_BASES:
            raise ValueError(f"onset_basis must be one of {ONSET_BASES}")
        return v

    @model_validator(mode="after")
    def _lanes_distinct(self) -> "Symptom":
        lanes = [self.onset.domain] + [p.lane for p in self.secondary_onsets]
        if len(lanes) != len(set(lanes)):
            raise ValueError("a symptom has at most one placement per lane")
        if not self.evidence:
            raise ValueError("a symptom needs at least one evidence reference")
        return self

    @property
    def lanes(self) -> list[str]:
        return [self.onset.domain] + [p.lane for p in self.secondary_onsets]

    def onset_in(self, lane: str) -> Interval | None:
        if self.onset.domain == lane:
            return self.onset.to_interval()
        for p in self.secondary_onsets:
            if p.lane == lane:
                return p.onset.to_interval()
        return None


@dataclass
class NormalisationProblem:
    source_id: str
    code: str
    detail: str


@dataclass
class Normalised:
    symptoms: list[Symptom] = field(default_factory=list)
    problems: list[NormalisationProblem] = field(default_factory=list)


def symptom_id(source: str, source_id: str, placements: list[tuple[str, int, int, str]]) -> str:
    ident = {"normaliser": NORMALISER_VERSION, "source": source, "source_id": source_id, "placements": [list(p) for p in sorted(placements)]}
    return "s-" + hashlib.sha256(canonical_json(ident).encode("utf-8")).hexdigest()[:16]


def canonical_key(s: Symptom):
    return (s.onset.domain, s.onset.lower_ns, s.onset.upper_ns, s.symptom_id)


def apply_cap(symptoms: list[Symptom], limit: int) -> tuple[list[Symptom], int]:
    """First `limit` symptoms in canonical order and the EXACT number suppressed (FR-SYM-07)."""
    ordered = sorted(symptoms, key=canonical_key)
    return ordered[:limit], max(0, len(ordered) - limit)


# ---------------------------------------------------------------------------------------------- M3c adapter

def _identities(f: M3cFinding) -> dict[str, str]:
    """Only KNOWN and untruncated identity text (a truncated or unknown identity cannot be matched exactly, so it is withheld: fail closed)."""
    codes = {n.code for n in f.uncertainty}
    if "identity_unknown" in codes or "text_truncated" in codes:
        return {}
    ov = f.observed_value if isinstance(f.observed_value, dict) else {}
    out: dict[str, str] = {}

    def take(kind: str, key: str):
        v = ov.get(key)
        if isinstance(v, str) and v != "":
            out[kind] = v
    c = f.anomaly_category
    if c == "log_severity":
        take("logger_name", "name")
    elif c == "diagnostic_status":
        take("diagnostic_name", "name")
        take("hardware_id", "hardware_id")
    elif c == "goal_status":
        take("goal_id", "goal_id")
    elif c == "power_status":
        take("battery_serial", "serial_number")
        take("battery_location", "location")
    return out


def _evidence(f: M3cFinding) -> list[EvidenceRef]:
    return [EvidenceRef(artifact_ref=o.artifact_ref, sha256=o.sha256, topic=o.topic, message_index=o.message_index, time_ns=o.log_time_ns,
                        clock_domain=LOG_TIME_DOMAIN) for o in f.observations]


def _int_field(ov: Any, key: str) -> int | None:
    v = ov.get(key) if isinstance(ov, dict) else None
    return v if type(v) is int else None


def _m3c_placements(f: M3cFinding) -> tuple[list[Placement], IntervalModel, str | None]:
    """-> (placements, extent, problem). The first placement is the primary one."""
    cat = f.anomaly_category
    dom = f.clock_domain
    first = f.first_observed_at.value_ns
    last = f.last_observed_at.value_ns if f.last_observed_at is not None else first
    if cat in ("log_severity", "diagnostic_status", "goal_status", "power_status", "non_finite_value", "array_length_change"):
        if dom != LOG_TIME_DOMAIN:
            return [], IntervalModel(domain=dom, lower_ns=first, upper_ns=max(first, last)), f"{cat} finding is not in the log-time domain"
        return ([Placement(lane=dom, onset=IntervalModel(domain=dom, lower_ns=first, upper_ns=first), onset_basis="received_message")],
                IntervalModel(domain=dom, lower_ns=first, upper_ns=max(first, last)), None)
    if cat == "publishing_gap":
        if f.last_observed_at is None or last < first:
            return [], IntervalModel(domain=dom, lower_ns=first, upper_ns=first), "publishing_gap finding lacks both bounding messages"
        iv = IntervalModel(domain=dom, lower_ns=first, upper_ns=last)
        return [Placement(lane=dom, onset=iv, onset_basis="silence_between_captured_messages")], iv, None
    if cat == "rate_change":
        w, s0, sl = _int_field(f.observed_value, "window_ns"), _int_field(f.observed_value, "first_window_start_ns"), _int_field(f.observed_value, "last_window_start_ns")
        if w is None or w <= 0 or s0 is None or sl is None or sl < s0:
            return [], IntervalModel(domain=dom, lower_ns=first, upper_ns=first), "rate_change finding lacks window_ns / first_window_start_ns / last_window_start_ns"
        return ([Placement(lane=dom, onset=IntervalModel(domain=dom, lower_ns=s0, upper_ns=s0 + w), onset_basis="window_span")],
                IntervalModel(domain=dom, lower_ns=s0, upper_ns=sl + w), None)
    if cat in ("timestamp_regression", "timestamp_duplicate"):
        # the finding's own times are payload stamps; receipt times come from the retained observations
        extent = IntervalModel(domain=dom, lower_ns=first, upper_ns=first)
        out: list[Placement] = []
        idx = 1 if len(f.observations) >= 2 else None              # regression: the offending message is the 2nd retained; duplicate: the first repeat
        if idx is not None:
            t = f.observations[idx].log_time_ns
            out.append(Placement(lane=LOG_TIME_DOMAIN, onset=IntervalModel(domain=LOG_TIME_DOMAIN, lower_ns=t, upper_ns=t), onset_basis="received_message"))
        out.append(Placement(lane=dom, onset=IntervalModel(domain=dom, lower_ns=first, upper_ns=first), onset_basis="payload_stamp_value"))
        return out, extent, None
    return [], IntervalModel(domain=dom, lower_ns=first, upper_ns=first), f"unknown category '{cat}'"


def from_m3c(report: AnomalyReport) -> Normalised:
    out = Normalised()
    for f in report.findings:
        pl, extent, problem = _m3c_placements(f)
        if problem:
            out.problems.append(NormalisationProblem(f.finding_id, "unplaceable_finding", problem))
            continue
        out.symptoms.append(_build(
            source="m3c_finding", source_id=f.finding_id, rule=f.detector_id, category=f.anomaly_category, topic=f.topic, ident=_identities(f),
            severity=f.severity, basis=f.severity_basis, extent=extent, placements=pl, evidence=_evidence(f),
            coverage=SourceCoverage(topic_coverage=f.coverage.topic_coverage, incomplete_reasons=list(f.coverage.incomplete_reasons),
                                    messages_examined=f.coverage.messages_examined, messages_undecodable=f.coverage.messages_undecodable),
            notes_in=[SourceNote(code=n.code, params=n.params) for n in f.uncertainty], truncated=f.observations_truncated))
    return out


def _build(*, source, source_id, rule, category, topic, ident, severity, basis, extent, placements, evidence, coverage, notes_in, truncated) -> Symptom:
    primary, rest = placements[0], placements[1:]
    m3d_notes = ["onset_interval_width"] if any(p.onset.upper_ns > p.onset.lower_ns for p in placements) else []
    sid = symptom_id(source, source_id, [(p.lane, p.onset.lower_ns, p.onset.upper_ns, p.onset_basis) for p in placements])
    return Symptom(symptom_id=sid, source=source, source_id=source_id, detector_or_rule=rule, category=category, topic=topic, node=topic,
                   identities=ident, severity=severity, severity_basis=basis, extent=extent, onset=primary.onset, onset_basis=primary.onset_basis,
                   secondary_onsets=rest, evidence=evidence, source_coverage=coverage, source_uncertainty=notes_in, notes=m3d_notes, truncated=truncated)


# ---------------------------------------------------------------------------------------------- M2 adapter

def _m2_evidence(f: M2Finding) -> list[EvidenceRef]:
    obs = {o.observation_id: o for o in f.observations}
    refs: list[EvidenceRef] = []
    for s in f.sources:
        o = obs.get(s.observation_id)
        refs.append(EvidenceRef(artifact_ref=s.artifact_ref, sha256=s.artifact_sha256, topic=s.topic,
                                message_index=(o.message_index if o is not None else _index_from_obs_id(s.observation_id)),
                                time_ns=(o.timestamp_ns if o is not None else None), clock_domain=(o.clock_domain if o is not None else None), role=s.role))
    return refs


def _index_from_obs_id(obs_id: str) -> int:
    return int(obs_id.rsplit("#", 1)[1])


def from_m2(result: InvestigationResult) -> Normalised:
    out = Normalised()
    by_id = {r.expectation_id: r for r in result.expectation_results}
    seen: set[str] = set()
    for f in result.candidates:
        if f.finding_id in seen:
            continue
        seen.add(f.finding_id)
        dom, t = f.time.clock_domain, f.time.ns
        er = by_id.get(f.expectation_id)
        if f.time.kind == "observation":
            pl = [Placement(lane=dom, onset=IntervalModel(domain=dom, lower_ns=t, upper_ns=t), onset_basis="received_sample")]
            extent = IntervalModel(domain=dom, lower_ns=t, upper_ns=t)
        else:
            start = None
            for s, e in zip(f.sources, _m2_evidence(f)):
                if s.role in ("trigger_event", "transition_entry") and e.time_ns is not None and e.clock_domain == dom:
                    start = e.time_ns if start is None else min(start, e.time_ns)
            if start is None or start > t:
                out.problems.append(NormalisationProblem(f.finding_id, "unplaceable_finding", "deadline-expiry finding without a trigger/entry observation in the finding's clock domain"))
                continue
            iv = IntervalModel(domain=dom, lower_ns=start, upper_ns=t)
            pl = [Placement(lane=dom, onset=iv, onset_basis="deadline_window")]
            extent = iv
        ev = _m2_evidence(f)
        topic = f.sources[0].topic if f.sources else ""
        cov = SourceCoverage(topic_coverage=("complete" if (er is not None and er.evidence_complete) else "incomplete"),
                             incomplete_reasons=list(er.missing_evidence) if er is not None else [],
                             messages_examined=(er.evaluated_observation_count if er is not None else 0))
        out.symptoms.append(_build(
            source="m2_finding", source_id=f.finding_id, rule=f"m2.{f.expectation_kind}", category=f"m2_{f.expectation_kind}", topic=topic, ident={},
            severity="not_applicable", basis="not_applicable", extent=extent, placements=pl, evidence=ev, coverage=cov,
            notes_in=[SourceNote(code="m2_uncertainty", params={"text": u}) for u in f.uncertainty], truncated=False))
    return out
