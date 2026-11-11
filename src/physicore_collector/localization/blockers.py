"""Blockers and caveats of M3d (Step 5; DESIGN 4.3, FR-BLK-*, clarification A and B).

A blocker is a recorded fact that makes an earliest-symptom claim undefendable in the lane(s) it applies to. Every blocker resolves to its actual source record
(kind, reference, field), the digests of the artifacts in scope, and an observation reference ONLY when the source genuinely holds one; an observation
reference is never constructed. Text is built from fixed templates over closed codes; nothing is copied from free-text source fields. Blockers fail closed
within a lane. A caveat is a scope statement; it never blocks and never says "nothing happened"."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Optional

from pydantic import StrictInt, StrictStr, field_validator, model_validator

from ..anomaly.clock import LOG_TIME_DOMAIN
from ..anomaly.findings import AnomalyReport
from ..inventory import EvidenceInventory, exit_code
from ..investigator.engine import InvestigationResult
from .report import Strict, canonical_json
from .symptoms import EvidenceRef, Symptom
from .vocabulary import BLOCKER_CODES, BLOCKER_SOURCE_KINDS, CAVEAT_CODES

STAMP_DETECTOR = "tierB.stamp_order"
STAMP_PREFIX = "payload_stamp:"
# detector reason codes that mean "this detector could not look at (part of) its scope"
BLIND_REASONS = frozenset({
    "topic_not_recorded", "type_not_supported", "no_rule_for_recorded_topic", "payload_not_decoded", "payload_too_large", "message_encoding_unsupported",
    "evidence_not_inspectable", "no_topic_of_supported_type",
})
# inventory gap codes that state a specific deficiency of the evidence that was read (the others are standing statements of what the tool does not establish)
INCOMPLETE_GAP_CODES = frozenset({"recording_incomplete", "bag_metadata_unusable", "bag_segments_not_fully_inspected", "bag_metadata_conflicts",
                                  "bag_unaccounted_entries", "recorder_reported_message_loss", "capture_not_cleanly_ended"})
# blockers about links, paths and chains: they downgrade link / component / chain claims, never the earliest-symptom claim of a lane
CHAIN_LEVEL_CODES = frozenset({"link_cap_reached", "chains_truncated", "path_work_limit_reached"})
BASELINE_DETECTORS = ("tierB.publishing_gap", "tierB.rate_change")

DETAIL = {
    "recording_incomplete": "The recording or its inventory is incomplete or unverified; earlier symptoms may exist in evidence that was not read.",
    "detector_blind_spot": "A detector could not examine part of its scope.",
    "detector_insufficient_evidence": "A detector had too little usable evidence to reach a conclusion.",
    "undecodable_messages": "Messages that could not be decoded were not examined.",
    "unparseable_messages": "Messages that could not be parsed were not examined.",
    "findings_truncated": "A detector's findings were capped; the suppressed findings were not examined here.",
    "observations_truncated": "The symptom's own observation list was capped.",
    "m2_expectation_not_evaluated": "An expectation could not be evaluated.",
    "m2_blind_spot_before_or_unplaced": "Samples that could not be evaluated may lie at or before this time, or have no usable timestamp.",
    "symptom_cap_reached": "The symptom limit was reached; later symptoms were not considered.",
    "link_cap_reached": "A link or path limit was reached; some links were not considered.",
    "chains_truncated": "Only the first reported chains are listed.",
    "path_work_limit_reached": "The path-search work bound was reached; path results are bounded, not complete.",
}
CAVEAT_DETAIL = {
    "boundary_gaps_not_evaluated": "Gaps at the start and end of the recording were not evaluated.",
    "receipt_order_not_occurrence_order": "Recorder log time is receipt order, not occurrence order.",
    "capture_loss_indistinguishable_from_silence": "A gap in the recording cannot be told apart from capture loss.",
    "baseline_from_recording": "The baseline was taken from the same recording it is compared with.",
    "baseline_contaminated_possible": "The baseline may include the behaviour under investigation.",
    "detector_not_configured": "A detector was not configured; nothing is said about what it would have seen.",
    "detector_not_applicable": "A detector does not apply to this evidence; nothing is said about what it would have seen.",
    "real_recording_validation_outstanding": "Validation against real robot recordings is outstanding.",
}


class ArtifactDigest(Strict):
    artifact_ref: StrictStr
    sha256: Optional[StrictStr] = None


class SourceRecord(Strict):
    """Where the blocker came from: kind of record, its reference, and the field that carried it."""
    kind: StrictStr
    ref: StrictStr
    field: StrictStr

    @field_validator("kind")
    @classmethod
    def _k(cls, v: str) -> str:
        if v not in BLOCKER_SOURCE_KINDS:
            raise ValueError(f"source kind must be one of {BLOCKER_SOURCE_KINDS}")
        return v


class ObservationRef(Strict):
    """An observation that genuinely exists in the source record (never constructed)."""
    artifact_ref: StrictStr
    sha256: Optional[StrictStr] = None
    topic: Optional[StrictStr] = None
    message_index: StrictInt
    time_ns: Optional[StrictInt] = None
    clock_domain: Optional[StrictStr] = None


class BlockerScope(Strict):
    lane: Optional[StrictStr] = None          # None: every lane (fail closed)
    topic: Optional[StrictStr] = None
    detector: Optional[StrictStr] = None
    expectation: Optional[StrictStr] = None


class Blocker(Strict):
    blocker_id: StrictStr
    code: StrictStr
    scope: BlockerScope
    count: Optional[StrictInt] = None         # exact where the source states one; otherwise absent
    detail: StrictStr
    source: SourceRecord
    artifacts: list[ArtifactDigest] = []
    observations: list[ObservationRef] = []

    @field_validator("code")
    @classmethod
    def _c(cls, v: str) -> str:
        if v not in BLOCKER_CODES:
            raise ValueError(f"blocker code must be one of {BLOCKER_CODES}")
        return v

    @model_validator(mode="after")
    def _t(self) -> "Blocker":
        if self.detail != DETAIL[self.code]:
            raise ValueError("a blocker's detail is the fixed template of its code")
        if self.count is not None and self.count < 0:
            raise ValueError("count is not negative")
        return self


class Caveat(Strict):
    code: StrictStr
    scope: BlockerScope = BlockerScope()
    detail: StrictStr

    @field_validator("code")
    @classmethod
    def _c(cls, v: str) -> str:
        if v not in CAVEAT_CODES:
            raise ValueError(f"caveat code must be one of {CAVEAT_CODES}")
        return v

    @model_validator(mode="after")
    def _t(self) -> "Caveat":
        if self.detail != CAVEAT_DETAIL[self.code]:
            raise ValueError("a caveat's detail is the fixed template of its code")
        return self


def blocker_id(code: str, scope: BlockerScope, source: SourceRecord, count: Optional[int]) -> str:
    ident = {"code": code, "scope": scope.model_dump(), "source": source.model_dump(), "count": count}
    return "b-" + hashlib.sha256(canonical_json(ident).encode("utf-8")).hexdigest()[:16]


def make_blocker(code: str, source: SourceRecord, scope: BlockerScope | None = None, count: Optional[int] = None,
                 artifacts: Iterable[ArtifactDigest] = (), observations: Iterable[ObservationRef] = ()) -> Blocker:
    scope = scope or BlockerScope()
    arts = sorted({(a.artifact_ref, a.sha256) for a in artifacts}, key=lambda t: (t[0], t[1] or ""))
    obs = sorted({(o.artifact_ref, o.message_index, o.sha256, o.topic, o.time_ns, o.clock_domain) for o in observations},
                 key=lambda t: (t[0], t[1], t[2] or "", t[3] or "", -1 if t[4] is None else t[4], t[5] or ""))
    return Blocker(blocker_id=blocker_id(code, scope, source, count), code=code, scope=scope, count=count, detail=DETAIL[code], source=source,
                   artifacts=[ArtifactDigest(artifact_ref=r, sha256=s) for r, s in arts],
                   observations=[ObservationRef(artifact_ref=a, message_index=i, sha256=s, topic=t, time_ns=tn, clock_domain=cd) for a, i, s, t, tn, cd in obs])


@dataclass
class BlockerSet:
    blockers: list[Blocker] = field(default_factory=list)
    caveats: list[Caveat] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------------------------ lane mapping

def lanes_of(b: Blocker, known_lanes: Iterable[str]) -> list[str]:
    """The lanes a blocker applies to. Lane-scoped when the scope names a lane; a detector blocker applies to the log lane (and, for the stamp-order detector,
    to every payload-stamp lane); anything else applies to every known lane (fail closed)."""
    known = sorted(set(known_lanes))
    s = b.scope
    if b.code in CHAIN_LEVEL_CODES:
        return []
    if s.lane is not None:
        return [s.lane] if s.lane in known else []
    if s.detector is not None:
        want = [d for d in known if d == LOG_TIME_DOMAIN or (s.detector == STAMP_DETECTOR and d.startswith(STAMP_PREFIX))]
        return want
    return known


def blockers_by_lane(blockers: Iterable[Blocker], known_lanes: Iterable[str]) -> dict[str, list[str]]:
    known = sorted(set(known_lanes))
    out: dict[str, list[str]] = {}
    for b in blockers:
        for lane in lanes_of(b, known):
            out.setdefault(lane, []).append(b.blocker_id)
    return {k: sorted(set(v)) for k, v in sorted(out.items())}


# ------------------------------------------------------------------------------------------------------------------ construction

def _digests(inv: EvidenceInventory) -> dict[str, Optional[str]]:
    return {a.ref_id: a.sha256 for a in inv.artifacts}


def _by_path(inv: EvidenceInventory) -> dict[str, tuple[str, Optional[str]]]:
    return {a.relative_path: (a.ref_id, a.sha256) for a in inv.artifacts}


def _inventory_blockers(inv: EvidenceInventory, report_arts: list[ArtifactDigest]) -> list[Blocker]:
    out: list[Blocker] = []
    digests = _digests(inv)

    def add(field_: str, ref: str, arts: list[ArtifactDigest]) -> None:
        out.append(make_blocker("recording_incomplete", SourceRecord(kind="inventory_record", ref=ref, field=field_), artifacts=arts or report_arts))

    if inv.inspection.state != "complete":
        add("inspection.state", "inspection", [])
    if inv.integrity.state in ("tampered", "unverifiable"):
        add("integrity.state", "integrity", [])
    if inv.session_status is not None and getattr(inv.session_status, "value", str(inv.session_status)) in ("partial", "failed"):
        add("session_status", "session_status", [])
    if inv.session_status is None and inv.recording.readability is not None and getattr(inv.recording.readability, "value", str(inv.recording.readability)) != "readable":
        add("recording.readability", "recording", [])
    for g in inv.gaps:
        if g.kind == "segment_not_readable" or (g.kind == "known_gap" and g.code in INCOMPLETE_GAP_CODES):
            arts = [ArtifactDigest(artifact_ref=g.artifact_ref, sha256=digests.get(g.artifact_ref))] if g.artifact_ref else []
            add("gaps", g.gap_id, arts)
    if not out and exit_code(inv) != 0:                       # the inventory itself says the evidence is not clean, by a route not listed above
        add("exit_code", "inventory", [])
    return out


def _detector_blockers(report: AnomalyReport, arts: list[ArtifactDigest]) -> tuple[list[Blocker], list[Caveat]]:
    bl: list[Blocker] = []
    cv: list[Caveat] = []
    for r in report.results:
        scope = BlockerScope(detector=r.detector_id)
        rec = lambda f: SourceRecord(kind="detector_result", ref=r.detector_id, field=f)  # noqa: E731
        codes = sorted({x.code for x in r.reasons})
        blind = sorted(c for c in codes if c in BLIND_REASONS or c.startswith("health_unknown")) if r.status not in ("not_applicable", "not_configured") else []
        if r.status == "blind_spot" or blind:
            bl.append(make_blocker("detector_blind_spot", rec("reasons" if blind else "status"), scope, artifacts=arts))
        if r.status == "insufficient_evidence":
            bl.append(make_blocker("detector_insufficient_evidence", rec("status"), scope, artifacts=arts))
        for x in r.reasons:
            if x.code == "undecodable_messages":
                bl.append(make_blocker("undecodable_messages", SourceRecord(kind="detector_counter", ref=r.detector_id, field="reasons.undecodable_messages"),
                                       scope, count=x.count if x.count is not None else r.messages_undecodable, artifacts=arts))
            elif x.code == "unparseable_messages":
                bl.append(make_blocker("unparseable_messages", SourceRecord(kind="detector_counter", ref=r.detector_id, field="reasons.unparseable_messages"),
                                       scope, count=x.count, artifacts=arts))
            elif x.code == "recording_incomplete":
                bl.append(make_blocker("recording_incomplete", rec("reasons.recording_incomplete"), scope, artifacts=arts))
            elif x.code == "boundary_gaps_not_evaluated":
                cv.append(Caveat(code="boundary_gaps_not_evaluated", scope=scope, detail=CAVEAT_DETAIL["boundary_gaps_not_evaluated"]))
        if r.findings_truncated is not None:
            bl.append(make_blocker("findings_truncated", SourceRecord(kind="detector_counter", ref=r.detector_id, field="findings_truncated.suppressed_exact_count"),
                                   scope, count=r.findings_truncated.suppressed_exact_count, artifacts=arts))
        if r.status == "not_configured":
            cv.append(Caveat(code="detector_not_configured", scope=scope, detail=CAVEAT_DETAIL["detector_not_configured"]))
        if r.status == "not_applicable":
            cv.append(Caveat(code="detector_not_applicable", scope=scope, detail=CAVEAT_DETAIL["detector_not_applicable"]))
        if r.detector_id in BASELINE_DETECTORS and r.status in ("findings_reported", "no_anomaly_observed"):
            cv.append(Caveat(code="baseline_from_recording", scope=scope, detail=CAVEAT_DETAIL["baseline_from_recording"]))
            cv.append(Caveat(code="baseline_contaminated_possible", scope=scope, detail=CAVEAT_DETAIL["baseline_contaminated_possible"]))
        if r.detector_id == "tierB.publishing_gap" and r.status in ("findings_reported", "no_anomaly_observed"):
            cv.append(Caveat(code="capture_loss_indistinguishable_from_silence", scope=scope, detail=CAVEAT_DETAIL["capture_loss_indistinguishable_from_silence"]))
    return bl, cv


def _evidence_digests(ev: Iterable[EvidenceRef]) -> list[ArtifactDigest]:
    return [ArtifactDigest(artifact_ref=e.artifact_ref, sha256=e.sha256) for e in ev]


def _symptom_blockers(symptoms: Iterable[Symptom]) -> list[Blocker]:
    out: list[Blocker] = []
    for s in symptoms:
        if s.source_coverage.topic_coverage == "incomplete":
            out.append(make_blocker("recording_incomplete", SourceRecord(kind="finding", ref=s.source_id, field="coverage"),
                                    BlockerScope(topic=s.topic), artifacts=_evidence_digests(s.evidence)))
        if s.truncated:
            obs = [ObservationRef(artifact_ref=e.artifact_ref, sha256=e.sha256, topic=e.topic, message_index=e.message_index, time_ns=e.time_ns,
                                  clock_domain=e.clock_domain) for e in s.evidence]
            out.append(make_blocker("observations_truncated", SourceRecord(kind="finding", ref=s.source_id, field="observations_truncated"),
                                    BlockerScope(topic=s.topic), artifacts=_evidence_digests(s.evidence), observations=obs))
    return out


def _parse_obs_id(obs_id: str) -> Optional[tuple[str, int]]:
    if not obs_id.startswith("obs:") or "#" not in obs_id:
        return None
    path, _, idx = obs_id[4:].rpartition("#")
    return (path, int(idx)) if path and idx.isdigit() else None


def _m2_blockers(m2: InvestigationResult, inv: EvidenceInventory) -> list[Blocker]:
    out: list[Blocker] = []
    paths = _by_path(inv)
    for er in m2.expectation_results:
        scope = BlockerScope(lane=er.clock_domain, expectation=er.expectation_id)
        src = lambda f: SourceRecord(kind="m2_result", ref=er.expectation_id, field=f)  # noqa: E731
        arts = [ArtifactDigest(artifact_ref=r, sha256=s) for r, s in paths.values()]
        status = getattr(er.status, "value", str(er.status))
        if status in ("insufficient_evidence", "incomparable_timebases", "invalid_expectation"):
            out.append(make_blocker("m2_expectation_not_evaluated", src("status"), scope, artifacts=arts))
        if not er.evidence_complete and status != "invalid_expectation":
            out.append(make_blocker("recording_incomplete", src("evidence_complete"), scope, artifacts=arts))
        if er.blind_spot_count > 0 or er.blind_spot_unplaced:
            obs: list[ObservationRef] = []
            for b in er.blind_spots:
                p = _parse_obs_id(b.observation_id) if b.observation_id else None
                if p is None or p[0] not in paths:
                    continue                                   # not resolvable to a recording artifact: stays metadata-only, never invented
                ref, sha = paths[p[0]]
                obs.append(ObservationRef(artifact_ref=ref, sha256=sha, message_index=p[1], time_ns=b.time_ns, clock_domain=er.clock_domain))
            out.append(make_blocker("m2_blind_spot_before_or_unplaced", src("blind_spots"), scope, count=er.blind_spot_count, artifacts=arts, observations=obs))
    return out


def build_blockers(report: AnomalyReport, inventory: EvidenceInventory, m2: InvestigationResult | None = None, symptoms: Iterable[Symptom] = (),
                   suppressed_symptoms: int = 0) -> BlockerSet:
    arts = [ArtifactDigest(artifact_ref=e.artifact_ref, sha256=e.sha256) for e in report.evidence]
    bl: list[Blocker] = []
    bl += _inventory_blockers(inventory, arts)
    db, cv = _detector_blockers(report, arts)
    bl += db
    bl += _symptom_blockers(symptoms)
    if m2 is not None:
        bl += _m2_blockers(m2, inventory)
    if suppressed_symptoms > 0:
        bl.append(make_blocker("symptom_cap_reached", SourceRecord(kind="m3d_limit", ref="symptoms", field="limits.symptoms"), count=suppressed_symptoms, artifacts=arts))
    cv.append(Caveat(code="receipt_order_not_occurrence_order", detail=CAVEAT_DETAIL["receipt_order_not_occurrence_order"]))
    cv.append(Caveat(code="real_recording_validation_outstanding", detail=CAVEAT_DETAIL["real_recording_validation_outstanding"]))
    seen: dict[str, Blocker] = {}
    for b in bl:
        seen.setdefault(b.blocker_id, b)
    cseen: dict[str, Caveat] = {}
    for c in cv:
        cseen.setdefault(canonical_json(c.model_dump()), c)
    return BlockerSet(blockers=[seen[k] for k in sorted(seen)], caveats=[cseen[k] for k in sorted(cseen)])
