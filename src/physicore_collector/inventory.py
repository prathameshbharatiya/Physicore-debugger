"""Evidence inventory: a narrow, READ-ONLY, versioned view of an incident / package / recording for a separate
consumer (the future Investigator). It does not diagnose anything.

This module ORCHESTRATES existing Collector components and implements no parallel logic:

  incident record + stored statements   incident.load_incident                    (unchanged)
  integrity re-check of an incident     incident.verify_incident_detailed         (unchanged)
  integrity re-check of a package       export.verify_package                     (unchanged)
  manifest / validation / known gaps    schemas.Manifest (parsed, not recomputed)
  bag + split-MCAP structure            bag.scan_bag, mcap_reader.scan_recording  (unchanged)
  capture-health sidecar                capture_health.load_health                (unchanged)

Accepted input (`inspect_evidence`):
  incident         an incident directory, its incident.json, or a capture session directory holding `incident/`
  package          an export package directory (manifest.json)
  raw_bag          a rosbag2 directory, or a capture session directory without an incident (bag/ + capture_health.json)
  raw_mcap         a single .mcap file
A raw recording has no manifest and no profile: it is inventoried and structurally checked, but NOT validated against
any robot profile, and its integrity is `not_applicable` (there is no recorded digest to compare with).

Guarantees (each has a test):
  * Read-only. Nothing is written, renamed, locked or touched inside the input.
  * Expected failures never raise: they become structured `errors` and an `inspection.state` of partial/failed.
  * Deterministic: the same bytes give the same JSON (no wall-clock time is recorded; `provenance.input.path` is the
    path as given).
  * Nothing is invented: counts/timestamps come from the existing parsers; an unknown stays null; clock domains are
    listed separately and never merged or converted (see `timebase`).
  * Offline inspection only. `provenance.acquisition.live_ros2_capture_verified` is pinned to False: this module cannot
    show that a recording came from a working live capture.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from .bag import scan_bag
from .capture_health import (
    CaptureHealth, LoadedHealth, build_capture_checks, load_health,
)
from .checksums import FileTooLargeError, read_and_hash, sha256_file
from .errors import HealthError, IncidentError, PhysiCoreError
from .export import MANIFEST_NAME, first_symlink_component, verify_package
from .incident import (
    DEFAULT_INCIDENT_DIR, INCIDENT_FILE, PACKAGE_DIR, SUMMARY_FILE, CaptureHealthStatus, EvidenceCoverage, IntegrityStatus,
    IncidentRecord, load_incident, verify_incident_detailed,
)
from .mcap_reader import scan_recording
from .schemas import (
    ArtifactReadability, ArtifactRole, CadenceFinding, CheckOutcome, CheckResult, Manifest, RecordingWindow,
    SegmentInfo, SessionStatus, ToolInfo, TopicInfo, TriggerComparison, _Strict, relative_path_problem,
)
from .session import CAPTURE_BAG_DIR, CAPTURE_HEALTH_NAME, resolve_recording_input

INVENTORY_SCHEMA_VERSION = "1.0"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_LISTED_LOSS_LINES = 20

InputKind = Literal["incident", "package", "raw_bag", "raw_mcap", "unsupported"]
ArtifactIntegrity = Literal["verified", "mismatch", "missing", "unsafe", "not_checked", "not_applicable", "unlisted"]
Severity = Literal["error", "warning", "limitation"]

REUSED_COMPONENTS = [
    "incident.load_incident", "incident.verify_incident_detailed", "export.verify_package", "bag.scan_bag",
    "mcap_reader.scan_recording", "capture_health.load_health", "schemas.Manifest",
]

# Known-gap codes that describe a SPECIFIC deficiency seen in this evidence (warning). Every other Collector known gap
# is a standing statement of what this tool does not establish (limitation).
_WARNING_GAP_CODES = frozenset({
    "recording_incomplete", "bag_metadata_unusable", "bag_segments_not_fully_inspected", "bag_metadata_conflicts",
    "bag_unaccounted_entries", "capture_health_unavailable", "recorder_reported_message_loss", "capture_not_cleanly_ended",
    "trigger_comparison_not_established", "cadence_check_not_completed",
})
_SEVERITY_RANK = {"error": 0, "warning": 1, "limitation": 2}


# --------------------------------------------------------------------------
# Models (inventory.json, schema 1.0)
# --------------------------------------------------------------------------

class InputInfo(_Strict):
    kind: InputKind
    path: str  # the path AS GIVEN (provenance only; nothing else in the inventory contains a host path)
    name: str


class Acquisition(_Strict):
    """Offline inspection is not live capture. This inventory can never establish that a live ROS 2 capture worked."""

    mode: Literal["offline_inspection"] = "offline_inspection"
    live_ros2_capture_verified: Literal[False] = False
    capture_health_record_present: bool
    statement: str = (
        "This inventory was produced by inspecting files already on disk. A capture-health record, where present, is the "
        "supervisor's own account; this inspection does not show that the recorder ran against a real ROS 2 graph."
    )


class Provenance(_Strict):
    tool: ToolInfo = Field(default_factory=ToolInfo)
    input: InputInfo
    read_only: Literal[True] = True
    integrity_checked_now: bool  # True iff file bytes were re-hashed against recorded digests during THIS inspection
    # Where recording / topics / validation / gaps come from. `stored_manifest`: the manifest's results as written when the
    # package was made (only integrity is re-checked now, so after a failed integrity check they may not describe the bytes on
    # disk). `scanned_now`: the recording files were parsed during this inspection.
    content_basis: Literal["stored_manifest", "scanned_now"]
    source_schema_versions: dict[str, str] = Field(default_factory=dict)
    reused_components: list[str] = Field(default_factory=lambda: list(REUSED_COMPONENTS))
    acquisition: Acquisition


class InspectionState(_Strict):
    """Did the INVENTORY complete? Independent of how good the evidence is (see session_status / integrity)."""

    state: Literal["complete", "partial", "failed"]
    reasons: list[str] = Field(default_factory=list)


class ArtifactRef(_Strict):
    ref_id: str  # "artifact:<relative_path>"; stable, usable as a pointer by later components
    relative_path: str  # POSIX, relative to the input root; never absolute, never contains '..'
    kind: str  # mcap_recording | recording_unsupported | bag_metadata | robot_profile | supplied_metadata | trigger |
    #            validation_report | capture_health | manifest | incident_record | incident_summary | unlisted
    role: str | None = None  # the Collector's ArtifactRole, where the manifest assigns one
    supported: bool  # can the existing Collector parse this artifact type?
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    sha256_basis: Literal["recorded_in_manifest", "computed_now", "not_available"]
    integrity: ArtifactIntegrity
    problems: list[str] = Field(default_factory=list)

    @field_validator("relative_path")
    @classmethod
    def _safe_reference(cls, v: str) -> str:
        why = relative_path_problem(v)
        if why is not None:
            raise ValueError(f"unsafe artifact reference: path {why}")
        return v


class SegmentRef(_Strict):
    order: int = Field(ge=0)
    relative_path: str  # relative to the bag directory
    artifact_ref: str | None = None
    state: str  # existing SegmentState value, or the single file's readability
    size_bytes: int | None = None
    sha256: str | None = None
    observed_message_count: int | None = None
    metadata_message_count: int | None = None
    first_log_time_ns: int | None = None
    last_log_time_ns: int | None = None
    errors: list[str] = Field(default_factory=list)


class RecordingInfo(_Strict):
    kind: Literal["mcap_file", "rosbag2_directory"] | None
    bag_directory: str | None = None  # relative to the input root
    segment_order_basis: str | None = None
    metadata_present: bool | None = None
    metadata_valid: bool | None = None
    metadata_errors: list[str] = Field(default_factory=list)
    segments: list[SegmentRef] = Field(default_factory=list)
    unaccounted_entries: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    # Messages observed (recovered) across ALL segments. None whenever any segment's count is unknown (missing, unreadable,
    # unsupported): a partial sum is never presented as the total.
    total_message_count: int | None = None
    readability: ArtifactReadability | None = None
    read_errors: list[str] = Field(default_factory=list)


class Integrity(_Strict):
    """Artifact integrity only: do bytes match RECORDED digests? Says nothing about truth or completeness."""

    state: Literal["verified_now", "tampered", "unverifiable", "not_applicable"]
    checked_now: bool
    problems: list[str] = Field(default_factory=list)
    manifest_present: bool
    manifest_sha256: str | None = None
    manifest_schema_version: str | None = None
    listed_artifact_count: int | None = None
    not_established: list[str] = Field(default_factory=list)


class ClockDomain(_Strict):
    id: str
    basis: str  # the clock's own label; "unknown" is a valid, reported value
    description: str
    start_ns: int | None = None
    end_ns: int | None = None
    duration_ns: int | None = None
    source: str  # which artifact/record this came from


class ClockAlignment(_Strict):
    """Whether two clock domains are KNOWN to be comparable. Only the Collector's existing statements are used; nothing is
    inferred here. Unless `state` says compatibility was established, the domains must not be merged or ordered together."""

    a: str
    b: str
    state: str
    reason: str


class Timebase(_Strict):
    log_time_basis: str
    recording_window: RecordingWindow | None = None
    clock_domains: list[ClockDomain] = Field(default_factory=list)
    alignments: list[ClockAlignment] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class CaptureHealthInfo(_Strict):
    present: bool
    source: Literal["package_artifact", "supplied_sidecar", "manifest_summary_only", "not_supplied"]
    state: str | None = None
    appears_finalized: bool | None = None  # tri-state; None = unknown, never collapsed into False
    session_id: str | None = None
    health_schema_version: str | None = None
    sidecar_sha256: str | None = None
    recorder_loss_warning_seen: bool | None = None  # the recorder printed a loss warning. False does NOT mean no loss.
    recorder_loss_line_count: int | None = None
    recorder_loss_lines: list[str] = Field(default_factory=list)
    interruptions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    disk_level: str | None = None
    ros_distro: str | None = None
    recorder_statistics_available: Literal[False] = False
    no_loss_claim_permitted: Literal[False] = False
    unknown: list[str] = Field(default_factory=list)


class IncidentSummary(_Strict):
    """What the incident record STORED when it was prepared (not re-derived now)."""

    incident_id: str
    created_at_utc: str
    outcome: str
    outcome_reasons: list[str]
    stored_integrity: IntegrityStatus
    stored_capture_health: CaptureHealthStatus
    evidence_coverage: EvidenceCoverage
    notes: list[str] = Field(default_factory=list)


class EvidenceGap(_Strict):
    gap_id: str
    kind: str  # unsafe_path | known_gap | check_failed | check_inconclusive | cadence_observation | integrity | unsupported_artifact |
    #            unlisted_artifact | segment_not_readable | expected_topic_not_observed | expected_topic_unknown
    severity: Severity
    code: str
    message: str
    source: str  # collector_known_gap | collector_check | collector_cadence | collector_coverage | inventory
    artifact_ref: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class InventoryWarning(_Strict):
    code: str
    message: str


class InventoryError(_Strict):
    code: str
    message: str
    hint: str | None = None
    artifact_ref: str | None = None


class EvidenceInventory(_Strict):
    inventory_schema_version: str = INVENTORY_SCHEMA_VERSION
    provenance: Provenance
    inspection: InspectionState
    session_status: SessionStatus | None = None  # the EXISTING validation taxonomy; None = never validated (raw input)
    session_status_reasons: list[str] = Field(default_factory=list)
    integrity: Integrity
    capture_health: CaptureHealthInfo
    recording: RecordingInfo
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    topics: list[TopicInfo] = Field(default_factory=list)
    timebase: Timebase
    checks: list[CheckResult] = Field(default_factory=list)
    cadence_findings: list[CadenceFinding] = Field(default_factory=list)
    trigger_comparison: TriggerComparison | None = None
    incident: IncidentSummary | None = None
    gaps: list[EvidenceGap] = Field(default_factory=list)
    not_established: list[str] = Field(default_factory=list)
    warnings: list[InventoryWarning] = Field(default_factory=list)
    errors: list[InventoryError] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _join(prefix: str, rel: str) -> str:
    return f"{prefix}/{rel}" if prefix else rel


def _ref_id(rel: str) -> str:
    return f"artifact:{rel}"


def _kind_for(role: ArtifactRole, rel: str) -> tuple[str, bool]:
    if role is ArtifactRole.RECORDING:
        ok = rel.endswith(".mcap")
        return ("mcap_recording" if ok else "recording_unsupported"), ok
    return {
        ArtifactRole.PROFILE: "robot_profile", ArtifactRole.METADATA: "supplied_metadata", ArtifactRole.TRIGGER: "trigger",
        ArtifactRole.VALIDATION_REPORT: "validation_report", ArtifactRole.BAG_METADATA: "bag_metadata",
        ArtifactRole.CAPTURE_HEALTH: "capture_health",
    }[role], True


def _problem_for(rel: str, problems: list[str]) -> list[str]:
    pre = f"{rel}: "
    return [p[len(pre):] for p in problems if p.startswith(pre)]


def _classify_problem(msgs: list[str]) -> ArtifactIntegrity:
    if any("file is missing" in m for m in msgs):
        return "missing"
    if any("symlink" in m for m in msgs):
        return "unsafe"
    return "mismatch"


class _Builder:
    """Accumulates one inventory; every method only reads."""

    def __init__(self, path: Path, kind: InputKind) -> None:
        self.path = path
        self.kind: InputKind = kind
        self.errors: list[InventoryError] = []
        self.warnings: list[InventoryWarning] = []
        self.artifacts: list[ArtifactRef] = []
        self.gaps: list[EvidenceGap] = []
        self.not_established: list[str] = []
        self.integrity = Integrity(state="not_applicable", checked_now=False, manifest_present=False)
        self.versions: dict[str, str] = {}
        self.checks: list[CheckResult] = []
        self.cadence: list[CadenceFinding] = []
        self.trigger_comparison: TriggerComparison | None = None
        self.topics: list[TopicInfo] = []
        self.recording = RecordingInfo(kind=None)
        self.health = CaptureHealthInfo(present=False, source="not_supplied")
        self.health_loaded: LoadedHealth | None = None
        self.session_status: SessionStatus | None = None
        self.session_status_reasons: list[str] = []
        self.incident: IncidentSummary | None = None
        self.log_time_basis = "unknown"
        self.window: RecordingWindow | None = None
        self.manifest: Manifest | None = None
        self.bag_meta_domain: ClockDomain | None = None
        self.trigger_domain: ClockDomain | None = None
        self.state_reasons: list[str] = []
        self.failed = False

    # ---- small adders
    def error(self, code: str, message: str, *, hint: str | None = None, artifact: str | None = None) -> None:
        self.errors.append(InventoryError(code=code, message=message, hint=hint, artifact_ref=artifact))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(InventoryWarning(code=code, message=message))

    def gap(self, kind: str, severity: Severity, code: str, message: str, source: str, *,
            artifact: str | None = None, details: dict[str, Any] | None = None) -> None:
        gid = f"gap:{source}:{code}" + (f":{artifact}" if artifact else "")
        if any(g.gap_id == gid and g.message == message for g in self.gaps):
            return
        self.gaps.append(EvidenceGap(gap_id=gid, kind=kind, severity=severity, code=code, message=message, source=source,
                                     artifact_ref=artifact, details=details or {}))

    def hash_file(self, path: Path, rel: str) -> tuple[str | None, int | None]:
        """SHA-256 and size of a regular, non-symlink file; (None, None) otherwise. A read failure is a structured error."""
        try:
            if path.is_symlink() or not path.is_file():
                return None, None
            return sha256_file(path), path.stat().st_size
        except OSError as e:
            self.error("artifact_unreadable", f"'{rel}' could not be read: {e.strerror or e}", artifact=_ref_id(rel))
            return None, None

    def walk(self, root: Path, prefix: str) -> list[str]:
        """Every file or symlink under root (symlinked directories are listed, never entered), POSIX-relative, sorted. A directory
        that cannot be listed is a structured error, never silently skipped."""
        out: list[str] = []

        def onerror(e: OSError) -> None:
            where = Path(e.filename).relative_to(root).as_posix() if e.filename else "."
            self.error("directory_unreadable", f"'{_join(prefix, where) if where != '.' else prefix or '.'}' could not be listed: {e.strerror or e}",
                       hint="Some files below it may be missing from this inventory.")

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=onerror):
            dirnames.sort()
            for n in filenames:
                out.append((Path(dirpath) / n).relative_to(root).as_posix())
            for n in list(dirnames):
                if (Path(dirpath) / n).is_symlink():
                    out.append((Path(dirpath) / n).relative_to(root).as_posix())
                    dirnames.remove(n)
        return sorted(out)

    def add_artifact(self, rel: str, kind: str, supported: bool, *, role: str | None, size: int | None, sha: str | None,
                     basis: str, integrity: ArtifactIntegrity, problems: list[str] | None = None) -> None:
        why = relative_path_problem(rel)
        if why is not None:  # never emit a reference that could be followed out of the evidence root
            self.error("unsafe_path", f"an entry named {rel!r} was not listed as an artifact: its path {why}",
                       hint="The file is neither read nor referenced by this inventory.")
            self.gap("unsafe_path", "error", "unsafe_path", f"an entry named {rel!r} has an unsafe path ({why}) and was neither read nor referenced.",
                     "inventory", details={"name": rel})
            return
        self.artifacts.append(ArtifactRef(
            ref_id=_ref_id(rel), relative_path=rel, kind=kind, role=role, supported=supported, size_bytes=size, sha256=sha,
            sha256_basis=basis, integrity=integrity, problems=problems or []))  # type: ignore[arg-type]
        if not supported:
            self.gap("unsupported_artifact", "warning", "unsupported_artifact",
                     f"'{rel}' is an artifact type the Collector cannot read; its content is not in this inventory.",
                     "inventory", artifact=_ref_id(rel))

    # ---- shared sections
    def take_checks(self, checks) -> None:
        self.checks = list(checks)
        for c in self.checks:
            if c.outcome in (CheckOutcome.FAIL, CheckOutcome.INCONCLUSIVE):
                kind = "check_failed" if c.outcome is CheckOutcome.FAIL else "check_inconclusive"
                self.gap(kind, "warning" if c.required else "limitation", c.check_id, c.message, "collector_check",
                         details={"required": c.required, "outcome": c.outcome.value, "unknowns": list(c.unknowns)})

    def take_manifest_sections(self, m: Manifest, prefix: str = "") -> None:
        self.manifest = m
        self.log_time_basis = m.profile.log_time_basis.value
        self.window = m.recording_window
        self.topics = sorted(m.topics, key=lambda t: (t.topic, t.channel_id))
        self.session_status = m.session_status
        self.session_status_reasons = list(m.validation.status_reasons)
        self.cadence = list(m.validation.cadence_findings)
        self.trigger_comparison = m.validation.trigger_comparison
        self.take_checks(m.validation.checks)
        for g in m.known_gaps:
            self.gap("known_gap", "warning" if g.code in _WARNING_GAP_CODES else "limitation", g.code, g.description,
                     "collector_known_gap")
        for cf in m.validation.cadence_findings:
            self.gap("cadence_observation", "warning", f"cadence:{cf.topic}", cf.interpretation, "collector_cadence",
                     details={"topic": cf.topic, "threshold_seconds": cf.threshold_seconds,
                              "observed_gap_seconds": cf.observed_gap_seconds,
                              "gap_start_log_time_ns": cf.gap_start_log_time_ns, "gap_end_log_time_ns": cf.gap_end_log_time_ns})
        lc = m.loss_detection_coverage
        self.not_established.append(lc.statement)
        if m.trigger is not None:
            self.trigger_domain = ClockDomain(
                id="trigger", basis=m.trigger.timestamp_basis.value, description=f"trigger '{m.trigger.trigger_id}' timestamp",
                start_ns=m.trigger.timestamp_ns, end_ns=m.trigger.timestamp_ns, source="manifest.trigger")
        self.versions["manifest_schema_version"] = m.manifest_schema_version
        self.versions["report_schema_version"] = m.validation.report_schema_version
        # recording section
        if m.bag is not None:
            self.take_bag_info(m.bag, bag_rel=_join(prefix, f"recording/{m.bag.bag_directory_name}"))
        else:
            self.recording.kind = "mcap_file"
            total = sum(t.message_count for t in m.topics)
            rec = next((a for a in m.artifacts if a.role is ArtifactRole.RECORDING), None)
            self.recording.segments = [SegmentRef(
                order=0, relative_path=rec.relative_path.split("/", 1)[-1] if rec else "(none)",
                artifact_ref=_ref_id(_join(prefix, rec.relative_path)) if rec else None,
                state=m.validation.artifact_readability.value, size_bytes=rec.size_bytes if rec else None,
                sha256=rec.sha256 if rec else None, observed_message_count=total,
                first_log_time_ns=m.recording_window.min_log_time_ns if m.recording_window else None,
                last_log_time_ns=m.recording_window.max_log_time_ns if m.recording_window else None)]
            self.recording.total_message_count = total
        self.recording.readability = m.validation.artifact_readability
        self.recording.read_errors = list(m.validation.read_errors)

    def take_bag_info(self, info, *, bag_rel: str) -> None:
        r = self.recording
        r.kind = "rosbag2_directory"
        r.bag_directory = bag_rel or "."  # relative to the input root
        r.segment_order_basis = info.segment_order_basis
        r.metadata_present, r.metadata_valid = info.metadata_present, info.metadata_valid
        r.metadata_errors = list(info.metadata_errors)
        r.conflicts = list(info.conflicts)
        r.unaccounted_entries = [u.name for u in info.unaccounted]
        r.segments = []
        for i, s in enumerate(info.segments):
            joined = _join(bag_rel, s.relative_path)
            aref = _ref_id(joined) if relative_path_problem(joined) is None else None  # an unsafe name is shown, never referenced
            r.segments.append(self._seg(i, s, aref))
            if s.state.value != "readable":
                self.gap("segment_not_readable", "warning", f"segment_{s.state.value}",
                         f"segment '{s.relative_path}' is {s.state.value.replace('_', ' ')}; its content is unknown.",
                         "inventory", artifact=aref, details={"errors": list(s.errors)})
        if info.metadata is not None:
            md = info.metadata
            self.versions["rosbag2_metadata_version"] = str(md.metadata_version)
            r.total_message_count = md.message_count if not r.segments else None
            if md.starting_time_ns is not None or md.duration_ns is not None:
                self.bag_meta_domain = ClockDomain(
                    id="rosbag2_metadata_time", basis="recorder_clock_unlabelled",
                    description="metadata.yaml starting_time/duration as written by the recorder",
                    start_ns=md.starting_time_ns, duration_ns=md.duration_ns, source="bag metadata.yaml")
        observed = [s.observed_message_count for s in info.segments]
        if observed and all(o is not None for o in observed):
            r.total_message_count = sum(observed)  # type: ignore[arg-type]

    @staticmethod
    def _seg(i: int, s: SegmentInfo, aref: str | None) -> SegmentRef:
        return SegmentRef(order=i, relative_path=s.relative_path, artifact_ref=aref, state=s.state.value, size_bytes=s.size_bytes,
                          sha256=s.sha256, observed_message_count=s.observed_message_count,
                          metadata_message_count=s.metadata_message_count, first_log_time_ns=s.first_log_time_ns,
                          last_log_time_ns=s.last_log_time_ns, errors=list(s.errors))

    def take_health(self, lh: LoadedHealth, source: Literal["package_artifact", "supplied_sidecar"]) -> None:
        h: CaptureHealth = lh.health
        lines = list(h.recorder.reported_loss_lines)
        self.health_loaded = lh
        self.health = CaptureHealthInfo(
            present=True, source=source, state=h.state.value, appears_finalized=h.bag_finalization.appears_finalized,
            session_id=h.session_id, health_schema_version=h.capture_health_schema_version, sidecar_sha256=lh.sha256,
            recorder_loss_warning_seen=bool(lines), recorder_loss_line_count=len(lines),
            recorder_loss_lines=lines[:MAX_LISTED_LOSS_LINES], interruptions=list(h.interruptions), errors=list(h.errors),
            warnings=list(h.warnings), stop_reason=h.timing.stop_reason, disk_level=h.disk.level,
            ros_distro=h.environment.get("ROS_DISTRO"), unknown=list(h.unknown_fields))
        self.versions["capture_health_schema_version"] = h.capture_health_schema_version

    def timebase(self) -> Timebase:
        doms = [ClockDomain(
            id="mcap_log_time", basis=self.log_time_basis,
            description="MCAP message log_time as recorded; the profile's declared basis, which this tool cannot verify",
            start_ns=self.window.min_log_time_ns if self.window else None,
            end_ns=self.window.max_log_time_ns if self.window else None,
            duration_ns=(self.window.max_log_time_ns - self.window.min_log_time_ns) if self.window else None,
            source="recording" if self.manifest is None else "manifest.recording_window")]
        al: list[ClockAlignment] = []
        cav: list[str] = []
        if self.log_time_basis == "unknown":
            cav.append("The clock represented by MCAP log_time is unknown (no profile declaration); it cannot be related to any external clock.")
        else:
            cav.append(f"log_time_basis '{self.log_time_basis}' is declared by the profile and is not verified from the file.")
        if self.bag_meta_domain is not None:
            doms.append(self.bag_meta_domain)
            al.append(ClockAlignment(a="mcap_log_time", b="rosbag2_metadata_time", state="not_compared",
                                     reason="metadata.yaml starting_time/duration are on the recorder's clock and are deliberately not compared with MCAP log_time"))
        h = self.health_loaded.health if self.health_loaded else None
        if h is not None and (h.timing.start_unix_ns is not None or h.timing.end_unix_ns is not None):
            doms.append(ClockDomain(
                id="capture_supervisor_wall_clock", basis=h.timing.wall_clock_basis,
                description="capture start/stop on the supervisor host's system clock",
                start_ns=h.timing.start_unix_ns, end_ns=h.timing.end_unix_ns, source="capture_health"))
            al.append(ClockAlignment(a="mcap_log_time", b="capture_supervisor_wall_clock",
                                     state=h.timing.clock_compatible_with_log_time,
                                     reason="supervisor wall-clock time is not established to be comparable with MCAP log_time"))
        if self.trigger_domain is not None:
            doms.append(self.trigger_domain)
            tc = self.trigger_comparison
            al.append(ClockAlignment(a="mcap_log_time", b="trigger", state=tc.result.value if tc else "unavailable",
                                     reason=tc.reason if tc else "no trigger comparison was recorded"))
        cav.append("Clock domains are listed separately and are never merged, shifted or converted by this inventory; "
                   "only alignments whose state says compatibility was established permit comparing two domains.")
        return Timebase(log_time_basis=self.log_time_basis, recording_window=self.window, clock_domains=doms,
                        alignments=al, caveats=cav)

    def build(self) -> EvidenceInventory:
        self.artifacts.sort(key=lambda a: a.relative_path)
        self.gaps.sort(key=lambda g: (_SEVERITY_RANK[g.severity], g.kind, g.code, g.artifact_ref or "", g.message))
        self.errors.sort(key=lambda e: (e.code, e.artifact_ref or "", e.message))
        if self.failed:
            state: Literal["complete", "partial", "failed"] = "failed"
        elif self.errors:
            state = "partial"
        else:
            state = "complete"
        reasons = self.state_reasons + [e.message for e in self.errors]
        stored = self.manifest is not None
        if stored and self.integrity.state in ("tampered", "unverifiable"):
            self.warn("stored_results_may_not_describe_current_bytes",
                      "Integrity did not verify, and recording/topics/validation below are the manifest's results from when the package "
                      "was made, not a fresh scan. Treat them as a record of what was prepared, not of the bytes now on disk.")
        return EvidenceInventory(
            provenance=Provenance(
                input=InputInfo(kind=self.kind, path=str(self.path), name=self.path.name),
                content_basis="stored_manifest" if stored else "scanned_now",
                integrity_checked_now=self.integrity.checked_now, source_schema_versions=dict(sorted(self.versions.items())),
                acquisition=Acquisition(capture_health_record_present=self.health.present)),
            inspection=InspectionState(state=state, reasons=reasons[:20]),
            session_status=self.session_status, session_status_reasons=self.session_status_reasons,
            integrity=self.integrity, capture_health=self.health, recording=self.recording, artifacts=self.artifacts,
            topics=self.topics, timebase=self.timebase(), checks=self.checks, cadence_findings=self.cadence,
            trigger_comparison=self.trigger_comparison, incident=self.incident, gaps=self.gaps,
            not_established=list(dict.fromkeys(self.not_established)), warnings=self.warnings, errors=self.errors)


# --------------------------------------------------------------------------
# Package / incident
# --------------------------------------------------------------------------

def _read_manifest(b: _Builder, pkg: Path, prefix: str) -> Manifest | None:
    mpath = pkg / MANIFEST_NAME
    mrel = _join(prefix, MANIFEST_NAME)
    if mpath.is_symlink():
        b.error("unsafe_path", f"{mrel} is a symlink; it was not read (packages contain regular files only)", artifact=_ref_id(mrel))
        return None
    if not mpath.is_file():
        b.error("manifest_missing", f"{mrel} is missing or not a regular file", hint="A package is a directory holding manifest.json.",
                artifact=_ref_id(mrel))
        return None
    try:
        raw, digest = read_and_hash(mpath, max_bytes=MAX_MANIFEST_BYTES)
    except (OSError, FileTooLargeError) as e:
        b.error("manifest_unreadable", f"cannot read {mrel}: {getattr(e, 'strerror', None) or e}", artifact=_ref_id(mrel))
        return None
    b.integrity.manifest_present = True
    b.integrity.manifest_sha256 = digest
    try:
        m = Manifest.model_validate_json(raw)
    except (ValidationError, ValueError) as e:
        first = e.errors(include_url=False)[0]["msg"] if isinstance(e, ValidationError) and e.errors() else str(e)
        n = e.error_count() if isinstance(e, ValidationError) else 1
        b.error("manifest_invalid", f"{mrel} is not a valid manifest ({n} error(s); first: {first})", artifact=_ref_id(mrel),
                hint="The manifest could not be used, so recorded digests and validation results are unavailable.")
        b.add_artifact(mrel, "manifest", True, role=None, size=len(raw), sha=digest, basis="computed_now", integrity="not_checked")
        return None
    b.add_artifact(mrel, "manifest", True, role=None, size=len(raw), sha=digest, basis="computed_now", integrity="not_applicable")
    return m


def _package_artifacts(b: _Builder, m: Manifest, pkg: Path, prefix: str, problems: list[str], verified: bool) -> None:
    listed = set()
    for a in sorted(m.artifacts, key=lambda a: a.relative_path):
        rel = _join(prefix, a.relative_path)
        listed.add(a.relative_path)
        kind, ok = _kind_for(a.role, a.relative_path)
        msgs = _problem_for(a.relative_path, problems)
        try:  # defence in depth: do not rely on the reused verifier alone to have refused a symlinked parent
            link = first_symlink_component(pkg, a.relative_path)
        except OSError as e:
            link = None
            b.error("artifact_unreadable", f"'{rel}' could not be examined: {e.strerror or e}", artifact=_ref_id(rel))
        if link is not None and not any("symlink" in m for m in msgs):
            msgs = [*msgs, f"passes through the symlink '{link}' (packages contain regular files only); it was not read"]
        integ: ArtifactIntegrity = _classify_problem(msgs) if msgs else ("verified" if verified else "not_checked")
        b.add_artifact(rel, kind, ok, role=a.role.value, size=a.size_bytes, sha=a.sha256, basis="recorded_in_manifest",
                       integrity=integ, problems=msgs)
        if msgs:
            b.gap("integrity", "error", f"artifact_{integ}", f"'{rel}': " + "; ".join(msgs), "inventory", artifact=_ref_id(rel))
        if integ == "unsafe":
            b.error("unsafe_path", f"'{rel}' " + "; ".join(msgs), artifact=_ref_id(rel))
    # files on disk that the manifest does not list (verify_package reports them as problems too)
    for rel in b.walk(pkg, prefix):
        if rel != MANIFEST_NAME and rel not in listed:
            full = _join(prefix, rel)
            sha, size = b.hash_file(pkg / rel, full)
            b.add_artifact(full, "unlisted", False, role=None, size=size, sha=sha, basis="computed_now" if sha else "not_available",
                           integrity="unlisted", problems=["file is not listed in the manifest"])
            b.gap("unlisted_artifact", "warning", "file_not_in_manifest",
                  f"'{full}' is on disk but not listed in the manifest; it is not covered by any recorded digest.", "inventory",
                  artifact=_ref_id(full))


def _load_package_health(b: _Builder, m: Manifest, pkg: Path, prefix: str) -> None:
    entry = next((a for a in m.artifacts if a.role is ArtifactRole.CAPTURE_HEALTH), None)
    if entry is None:
        if m.capture_health is not None:  # manifest claims a summary but ships no sidecar
            b.health = CaptureHealthInfo(present=True, source="manifest_summary_only", state=m.capture_health.state,
                                         appears_finalized=m.capture_health.appears_finalized, session_id=m.capture_health.session_id,
                                         health_schema_version=m.capture_health.health_schema_version,
                                         sidecar_sha256=m.capture_health.sidecar_sha256)
        return
    path = pkg / entry.relative_path
    try:
        link = first_symlink_component(pkg, entry.relative_path)
        if link is not None:
            b.error("unsafe_path", f"'{_join(prefix, entry.relative_path)}' passes through the symlink '{link}'; it was not read",
                    artifact=_ref_id(_join(prefix, entry.relative_path)))
            raise HealthError("capture-health artifact is reached through a symlink")
        b.take_health(load_health(path), "package_artifact")
    except (PhysiCoreError, OSError) as e:
        if isinstance(e, OSError):
            e = HealthError(f"cannot read capture-health artifact: {e.strerror or e}")
        b.error("capture_health_unreadable", f"{_join(prefix, entry.relative_path)}: {e.message}",
                artifact=_ref_id(_join(prefix, entry.relative_path)))
        if m.capture_health is not None:
            b.health = CaptureHealthInfo(present=True, source="manifest_summary_only", state=m.capture_health.state,
                                         appears_finalized=m.capture_health.appears_finalized, session_id=m.capture_health.session_id,
                                         health_schema_version=m.capture_health.health_schema_version,
                                         sidecar_sha256=m.capture_health.sidecar_sha256)


def _inspect_package_dir(b: _Builder, pkg: Path, prefix: str, *, incident_state=None) -> None:
    n_before = len(b.errors)
    m = _read_manifest(b, pkg, prefix)
    completed = True  # did a re-hash of the package actually finish?
    if incident_state is not None:  # integrity from incident.verify_incident_detailed (a superset of verify_package)
        state, problems = incident_state.state, list(incident_state.problems)
        pkg_problems = [p[len("package: "):] for p in problems if p.startswith("package: ")]
        completed = state != "unverifiable"
    elif m is None:  # without a usable manifest there is nothing to compare with, and nothing is read
        state, pkg_problems, completed = "unverifiable", [], False
        problems = [e.message for e in b.errors[n_before:]]
    else:
        try:
            pkg_problems = verify_package(pkg)
            state = "tampered" if pkg_problems else "verified_now"
        except OSError as e:
            b.error("integrity_check_failed", f"the integrity check could not be completed: {e.strerror or e}",
                    hint="Some package files could not be read; their integrity is unknown.")
            pkg_problems, state, completed = [], "unverifiable", False
        problems = list(pkg_problems)
    if m is None:  # recorded digests cannot be read, so nothing can be compared with them
        state = "unverifiable"
    b.integrity.state = state  # type: ignore[assignment]
    b.integrity.checked_now = completed
    b.integrity.problems = problems
    if m is None:
        b.failed = incident_state is None  # an incident record, if valid, still carries stored results
        b.state_reasons.append("the manifest is missing or invalid, so no recorded digests, validation results or topics are available")
        for rel in b.walk(pkg, prefix):
            if rel != MANIFEST_NAME:
                full = _join(prefix, rel)
                sha, size = b.hash_file(pkg / rel, full)
                b.add_artifact(full, "unlisted", False, role=None, size=size, sha=sha, basis="computed_now" if sha else "not_available",
                               integrity="not_checked")
        return
    b.integrity.manifest_schema_version = m.manifest_schema_version
    b.integrity.listed_artifact_count = len(m.artifacts)
    b.integrity.not_established = [
        "A matching digest shows the bytes are unchanged since the manifest was written; it does not show the data is true, "
        "the recording complete, or that no message was lost.",
    ]
    # verify_package checks every listed file individually, so an artifact without a problem WAS checked and matched even when
    # another artifact failed. Only `unverifiable` (the check could not be completed) leaves artifacts unchecked.
    _package_artifacts(b, m, pkg, prefix, pkg_problems, verified=(state in ("verified_now", "tampered")))
    b.take_manifest_sections(m, prefix)
    _load_package_health(b, m, pkg, prefix)


def _inspect_incident(b: _Builder, path: Path) -> None:
    if path.is_dir():  # the evidence-root layout must not route through a symlink: refuse before anything is read
        candidates = [INCIDENT_FILE] if os.path.lexists(path / INCIDENT_FILE) else [DEFAULT_INCIDENT_DIR, f"{DEFAULT_INCIDENT_DIR}/{INCIDENT_FILE}"]
        for rel in candidates:
            link = first_symlink_component(path, rel)
            if link is not None:
                b.error("unsafe_path", f"'{link}' inside the input is a symlink; it was not followed (evidence must not be read through a symlink)",
                        artifact=_ref_id(link))
                b.failed = True
                b.integrity.state = "unverifiable"
                b.state_reasons.append("the incident could not be read without following a symlink")
                return
    try:
        rec, d = load_incident(path)
    except IncidentError as e:
        b.error(e.code, e.message, hint=e.hint)
        d = path if path.is_dir() else path.parent
        if (d / DEFAULT_INCIDENT_DIR / INCIDENT_FILE).is_file() and not (d / INCIDENT_FILE).is_file():
            d = d / DEFAULT_INCIDENT_DIR
        rec = None
    for name, kind in ((INCIDENT_FILE, "incident_record"), (SUMMARY_FILE, "incident_summary")):
        sha, size = b.hash_file(d / name, name)
        if sha is not None:
            b.add_artifact(name, kind, True, role=None, size=size, sha=sha, basis="computed_now", integrity="not_applicable")
    if rec is not None:
        b.versions["incident_schema_version"] = rec.incident_schema_version
        b.incident = IncidentSummary(
            incident_id=rec.incident_id, created_at_utc=rec.created_at_utc, outcome=rec.outcome, outcome_reasons=list(rec.outcome_reasons),
            stored_integrity=rec.artifact_integrity, stored_capture_health=rec.capture_health, evidence_coverage=rec.evidence_coverage,
            notes=list(rec.notes))
        for t in rec.evidence_coverage.expected_topics:
            if t.observed != "present":
                b.gap("expected_topic_not_observed" if t.observed == "absent" else "expected_topic_unknown",
                      "warning" if t.required else "limitation", f"topic:{t.topic}",
                      f"expected topic {t.topic} was {'not observed' if t.observed == 'absent' else 'not determinable (recording only partly readable)'}"
                      + (" (required by the profile)" if t.required else ""), "collector_coverage", details={"topic": t.topic, "required": t.required})
        b.not_established.extend(rec.evidence_coverage.not_established)
        b.not_established.extend(rec.artifact_integrity.not_established)
        b.not_established.extend(rec.capture_health.not_established)
    pkg = d / PACKAGE_DIR
    if pkg.is_symlink():
        b.error("unsafe_path", f"'{PACKAGE_DIR}' is a symlink; it was not followed (evidence must not be read through a symlink)",
                artifact=_ref_id(PACKAGE_DIR))
        b.failed = rec is None
        b.integrity.state = "unverifiable"
        b.integrity.checked_now = False
        return
    if not pkg.is_dir():
        b.error("package_missing", f"{PACKAGE_DIR}/ is missing or not a directory in the incident", artifact=_ref_id(PACKAGE_DIR))
        b.failed = rec is None
        b.integrity.state = "unverifiable"
        b.integrity.checked_now = False
        return
    v = verify_incident_detailed(d, respect_active=False) if rec is not None else None
    if v is None:  # record unusable: fall back to the package-only check
        _inspect_package_dir(b, pkg, PACKAGE_DIR)
        b.warn("incident_record_unusable", "incident.json could not be used; the package was inspected on its own")
        return
    _inspect_package_dir(b, pkg, PACKAGE_DIR, incident_state=v)
    # entries in the incident dir that the incident format does not allow (verify reports them as problems)
    known = {a.relative_path.split("/", 1)[0] for a in b.artifacts}
    try:
        entries = sorted(os.listdir(d))
    except OSError as err:
        b.error("directory_unreadable", f"the incident directory could not be listed: {err.strerror or err}")
        entries = []
    for e in entries:
        if e not in known:
            sha, size = b.hash_file(d / e, e)
            b.add_artifact(e, "unlisted", False, role=None, size=size, sha=sha, basis="computed_now" if sha else "not_available",
                           integrity="unlisted", problems=["unexpected entry in the incident directory"])
            b.gap("unlisted_artifact", "warning", "unexpected_incident_entry",
                  f"'{e}' is an unexpected entry in the incident directory; it is covered by no recorded digest.", "inventory",
                  artifact=_ref_id(e))
    # incident.json binds the manifest by digest: surface the result per artifact
    for a in b.artifacts:
        if a.relative_path == _join(PACKAGE_DIR, MANIFEST_NAME) and rec is not None:
            ok = a.sha256 == rec.package.manifest_sha256
            a.integrity, a.sha256_basis = ("verified" if ok and v.state == "verified_now" else "mismatch" if not ok else "not_checked"), "computed_now"
            if not ok:
                a.problems.append("sha256 differs from the digest recorded in incident.json")


# --------------------------------------------------------------------------
# Raw recordings
# --------------------------------------------------------------------------

def _inspect_raw_bag(b: _Builder, path: Path) -> None:
    if not os.path.lexists(path / "metadata.yaml"):  # session-style input: its bag/ and sidecar must be real, not symlinks
        for name in (CAPTURE_BAG_DIR, CAPTURE_HEALTH_NAME):
            if (path / name).is_symlink():
                b.error("unsafe_path", f"'{name}' inside the input is a symlink; it was not followed (evidence must not be read through a symlink)",
                        artifact=_ref_id(name))
                b.failed = True
                b.state_reasons.append("the recording could not be read without following a symlink")
                return
    rec_path, sidecar = resolve_recording_input(path)
    prefix = CAPTURE_BAG_DIR if rec_path != path else ""
    scan, bag = scan_bag(rec_path)
    b.integrity = Integrity(state="not_applicable", checked_now=False, manifest_present=False, not_established=[
        "No manifest exists for a raw recording, so there is no recorded digest to verify against. SHA-256 values are computed now "
        "and describe these bytes only."])
    insp = scan.inspection
    b.window, b.topics = insp.recording_window, sorted(insp.topics, key=lambda t: (t.topic, t.channel_id))
    b.take_bag_info(bag.info, bag_rel=prefix)
    b.recording.readability = scan.readability
    b.recording.read_errors = list(scan.errors)
    b.recording.kind = "rosbag2_directory"
    if not b.recording.segments:
        b.recording.total_message_count = insp.total_message_count
    b.not_established.extend(insp.not_verified)
    listed: set[str] = set()
    for f in sorted(bag.files, key=lambda f: f.relative_path):
        rel = _join(prefix, f.relative_path)
        listed.add(rel)
        kind, ok = _kind_for(f.role, f.relative_path)
        b.add_artifact(rel, kind, ok, role=f.role.value, size=f.size_bytes, sha=f.sha256, basis="computed_now", integrity="not_applicable")
    for u in bag.info.unaccounted:
        rel = _join(prefix, u.name)
        sha, size = b.hash_file(rec_path / u.name, rel)
        b.add_artifact(rel, "unlisted", False, role=None, size=size, sha=sha, basis="computed_now" if sha else "not_available",
                       integrity="unlisted", problems=["not accounted for by metadata.yaml"])
    for s in bag.info.segments:  # segments that could not be hashed/read still appear, with their state
        rel = _join(prefix, s.relative_path)
        if s.state.value == "unsafe_path" or relative_path_problem(rel) is not None:
            b.error("unsafe_path", f"segment path {s.relative_path!r} named by metadata.yaml is unsafe; it was neither read nor referenced")
            continue
        if rel not in listed and not any(a.relative_path == rel for a in b.artifacts):
            b.add_artifact(rel, "mcap_recording" if s.relative_path.endswith(".mcap") else "recording_unsupported",
                           s.relative_path.endswith(".mcap") and s.state.value != "unsupported", role="recording", size=s.size_bytes,
                           sha=s.sha256, basis="not_available" if s.sha256 is None else "computed_now", integrity="not_checked",
                           problems=list(s.errors))
    extra = list(bag.checks)
    if sidecar is not None:
        try:
            lh = load_health(sidecar)
            b.take_health(lh, "supplied_sidecar")
            extra.extend(build_capture_checks(lh, bag))
            sha, size = lh.sha256, len(lh.raw)
            b.add_artifact(CAPTURE_HEALTH_NAME, "capture_health", True, role="capture_health", size=size, sha=sha,
                           basis="computed_now", integrity="not_applicable")
        except PhysiCoreError as e:
            b.error("capture_health_unreadable", e.message, hint=e.hint, artifact=_ref_id(CAPTURE_HEALTH_NAME))
    b.take_checks(extra)
    b.warn("not_validated", "A raw recording has no robot profile here: it was inspected structurally, not validated against expectations.")
    if sidecar is None:
        b.gap("known_gap", "warning", "capture_health_unavailable",
              "No capture-health record accompanies this recording: how it was started and stopped, and whether it was interrupted, is unknown.",
              "inventory")
    if scan.readability is not ArtifactReadability.READABLE:
        b.gap("segment_not_readable", "error" if scan.readability is ArtifactReadability.UNREADABLE else "warning", "recording_not_fully_readable",
              f"the recording is {scan.readability.value.replace('_', ' ')}; content after the first read error is unknown. "
              + (scan.errors[0] if scan.errors else ""), "inventory")


def _inspect_raw_mcap(b: _Builder, path: Path) -> None:
    digest = sha256_file(path)
    scan = scan_recording(path)
    insp = scan.inspection
    b.integrity = Integrity(state="not_applicable", checked_now=False, manifest_present=False, not_established=[
        "No manifest exists for a raw recording, so there is no recorded digest to verify against."])
    b.window, b.topics = insp.recording_window, sorted(insp.topics, key=lambda t: (t.topic, t.channel_id))
    b.recording = RecordingInfo(
        kind="mcap_file", readability=scan.readability, read_errors=list(scan.errors), total_message_count=insp.total_message_count,
        segments=[SegmentRef(order=0, relative_path=path.name, artifact_ref=_ref_id(path.name), state=scan.readability.value,
                             size_bytes=insp.size_bytes, sha256=digest, observed_message_count=insp.total_message_count,
                             first_log_time_ns=insp.recording_window.min_log_time_ns if insp.recording_window else None,
                             last_log_time_ns=insp.recording_window.max_log_time_ns if insp.recording_window else None,
                             errors=list(scan.errors))])
    b.add_artifact(path.name, "mcap_recording", True, role="recording", size=insp.size_bytes, sha=digest, basis="computed_now",
                   integrity="not_applicable")
    b.not_established.extend(insp.not_verified)
    b.warn("not_validated", "A raw recording has no robot profile here: it was inspected structurally, not validated against expectations.")
    b.gap("known_gap", "warning", "capture_health_unavailable",
          "No capture-health record accompanies a single .mcap file: how it was recorded, and whether it was interrupted, is unknown.", "inventory")
    try:
        sib = sorted(x.name for x in path.parent.iterdir() if x.suffix == ".mcap" and x != path and x.is_file())
    except OSError as e:
        sib = []
        b.warn("siblings_not_listed", f"the directory holding this file could not be listed ({e.strerror or e}); neighbouring bag files, if any, are unknown")
    if (path.parent / "metadata.yaml").is_file() or sib:
        b.warn("sibling_files_not_included", "Other bag files sit next to this .mcap; only this one file was inspected. "
               "Point at the bag directory to inventory a split recording.")
    if scan.readability is not ArtifactReadability.READABLE:
        b.gap("segment_not_readable", "error" if scan.readability is ArtifactReadability.UNREADABLE else "warning", "recording_not_fully_readable",
              f"the recording is {scan.readability.value.replace('_', ' ')}; content after the first read error is unknown. "
              + (scan.errors[0] if scan.errors else ""), "inventory")


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def _lexists(p: Path) -> bool:
    """True if the entry exists as a link or file (a dangling or looping symlink counts). Other errors (permission) propagate."""
    try:
        os.lstat(p)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def _classify(p: Path) -> InputKind:
    """Like `classify_input` but lets OSError propagate so `inspect_evidence` can report it precisely."""
    if "\x00" in str(p):
        raise ValueError("embedded NUL byte in path")
    try:
        st = os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        return "unsupported"
    if stat.S_ISLNK(st.st_mode):
        return "unsupported"
    if stat.S_ISREG(st.st_mode):
        if p.name == INCIDENT_FILE:
            return "incident"
        return "raw_mcap" if p.suffix == ".mcap" else "unsupported"
    if stat.S_ISDIR(st.st_mode):
        if _lexists(p / INCIDENT_FILE) or _lexists(p / DEFAULT_INCIDENT_DIR / INCIDENT_FILE):
            return "incident"
        if _lexists(p / MANIFEST_NAME):
            return "package"
        if _lexists(p / "metadata.yaml") or _lexists(p / CAPTURE_HEALTH_NAME) or any(x.name.endswith(".mcap") for x in os.scandir(p)):
            return "raw_bag"
        if _lexists(p / CAPTURE_BAG_DIR):
            return "raw_bag"
    return "unsupported"


def classify_input(path: str | Path) -> InputKind:
    """Decide what an input is without reading evidence content. Never raises: an input that cannot be examined is `unsupported`.
    Symlinks as the input itself are not followed."""
    try:
        return _classify(Path(path))
    except (OSError, ValueError):
        return "unsupported"


def _os_error(b: _Builder, e: OSError, what: str) -> None:
    if isinstance(e, PermissionError):
        b.error("permission_denied", f"{what}: permission denied",
                hint="Run as a user that can read the evidence directory, or copy the evidence somewhere readable.")
    elif e.errno == errno.ELOOP:
        b.error("symlink_loop", f"{what}: too many levels of symbolic links")
    elif e.errno == errno.ENAMETOOLONG:
        b.error("path_too_long", f"{what}: file name or path too long")
    else:
        b.error("io_error", f"{what}: {e.strerror or e}")


def inspect_evidence(path: str | Path) -> EvidenceInventory:
    """Inventory a local incident directory, export package, rosbag2 directory or .mcap file. Read-only, deterministic, and
    never raises for expected problems: filesystem errors (permission, loops, vanished files, NUL bytes) become structured
    `errors` and a partial/failed `inspection.state`."""
    try:
        p = Path(path)
    except (TypeError, ValueError) as e:
        p = Path("(invalid path)")
        b = _Builder(p, "unsupported")
        b.error("input_invalid_path", f"not a usable path: {e}")
        b.failed = True
        return b.build()
    classify_error: tuple[str, BaseException] | None = None
    try:
        kind = _classify(p)
    except ValueError as e:
        kind, classify_error = "unsupported", ("input_invalid_path", e)
    except OSError as e:
        kind, classify_error = "unsupported", ("os", e)
    b = _Builder(p, kind)
    try:
        if classify_error is not None:
            code, exc = classify_error
            if code == "os":
                _os_error(b, exc, f"'{p.name}' could not be examined")  # type: ignore[arg-type]
            else:
                b.error(code, f"'{p.name}' is not a usable path: {exc}")
            b.failed = True
            b.state_reasons.append("nothing was inspected")
        elif kind == "unsupported":
            if not _lexists(p):
                b.error("input_not_found", f"'{p}' does not exist", hint="Pass an incident directory, a package directory, a rosbag2 directory or an .mcap file.")
            elif p.is_symlink():
                b.error("input_is_symlink", f"'{p}' is a symlink; the input itself is not followed", hint="Pass the real path.")
            else:
                b.error("unsupported_input", f"'{p}' is not a supported input (incident, export package, rosbag2 directory or .mcap file)")
            b.failed = True
            b.state_reasons.append("nothing was inspected")
            b.gap("unsupported_artifact", "error", "unsupported_input", f"'{p.name}' is not an input type the Collector supports.", "inventory")
        elif kind == "incident":
            _inspect_incident(b, p)
        elif kind == "package":
            _inspect_package_dir(b, p, "")
        elif kind == "raw_bag":
            _inspect_raw_bag(b, p)
        else:
            _inspect_raw_mcap(b, p)
    except PhysiCoreError as e:
        b.error("inspection_error", e.message, hint=e.hint)
        b.failed = b.failed or not b.artifacts
    except OSError as e:
        _os_error(b, e, "part of the input could not be read")
        b.failed = b.failed or not b.artifacts
    return b.build()


def exit_code(inv: EvidenceInventory) -> int:
    """0 = inspected, and nothing known to be wrong with the evidence; 1 = inspected, but integrity failed or could not be
    checked, the evidence is partial/failed, or the inventory is partial; 2 = the input could not be inspected."""
    if inv.inspection.state == "failed":
        return 2
    if inv.inspection.state == "partial" or inv.integrity.state in ("tampered", "unverifiable"):
        return 1
    if inv.session_status in (SessionStatus.PARTIAL, SessionStatus.FAILED):
        return 1
    if inv.session_status is None:  # raw input: judged on readability and the structural checks that ran
        if inv.recording.readability not in (None, ArtifactReadability.READABLE):
            return 1
        if any(c.required and c.outcome in (CheckOutcome.FAIL, CheckOutcome.INCONCLUSIVE) for c in inv.checks):
            return 1
    return 0


def inventory_json(inv: EvidenceInventory) -> str:
    """Deterministic JSON text (stable field order, no wall-clock time)."""
    return inv.model_dump_json(indent=2) + "\n"


def render_inventory(inv: EvidenceInventory) -> str:
    """Concise human-readable report. Everything shown is in the JSON; this adds no analysis."""
    L: list[str] = []
    a = L.append
    a(f"EVIDENCE INVENTORY (schema {inv.inventory_schema_version})  input: {inv.provenance.input.kind}  '{inv.provenance.input.name}'")
    a(f"inspection: {inv.inspection.state.upper()}" + (f" ({'; '.join(inv.inspection.reasons[:3])})" if inv.inspection.reasons else ""))
    a("source: offline inspection of files on disk; live ROS 2 capture is NOT verified by this inventory")
    a("content: " + ("stored manifest results as written at preparation (not re-scanned); only integrity is re-hashed now"
                     if inv.provenance.content_basis == "stored_manifest" else "recording files parsed during this inspection"))
    a("")
    ig = inv.integrity
    a(f"integrity:      {ig.state.upper().replace('_', ' ')}" + (" (re-hashed now)" if ig.checked_now else " (no recorded digests to compare)" if ig.state == "not_applicable" else ""))
    for pr in ig.problems[:8]:
        a(f"                - {pr}")
    if len(ig.problems) > 8:
        a(f"                (+{len(ig.problems) - 8} more in the JSON)")
    a(f"validation:     {inv.session_status.value.upper() if inv.session_status else 'NOT VALIDATED (raw input, no profile)'}"
      + (f"  ({'; '.join(inv.session_status_reasons[:2])})" if inv.session_status_reasons else ""))
    ch = inv.capture_health
    a("capture health: " + ("NOT SUPPLIED (unknown, not 'healthy')" if not ch.present else
      f"{ch.state}  finalized={'unknown' if ch.appears_finalized is None else ch.appears_finalized}  recorder loss warnings seen: {ch.recorder_loss_line_count if ch.recorder_loss_line_count is not None else 'n/a'}  [{ch.source}]"))
    if ch.present:
        a("                no loss claim is permitted: rosbag2 exposes no drop counters")
    rec = inv.recording
    a(f"recording:      {rec.kind or 'unknown'}" + (f", {len(rec.segments)} segment(s), order basis: {rec.segment_order_basis}" if rec.kind == "rosbag2_directory" else "")
      + f", readability: {rec.readability.value if rec.readability else 'unknown'}, messages: {rec.total_message_count if rec.total_message_count is not None else 'unknown'}")
    for s in rec.segments:
        a(f"  segment {s.order}: {s.relative_path}  {s.state}  msgs={s.observed_message_count if s.observed_message_count is not None else '?'}"
          f"  log_time {s.first_log_time_ns if s.first_log_time_ns is not None else '?'}..{s.last_log_time_ns if s.last_log_time_ns is not None else '?'}")
    a("")
    a("time:")
    for d in inv.timebase.clock_domains:
        span = f"{d.start_ns}..{d.end_ns}" if d.end_ns is not None else (f"{d.start_ns} (+{d.duration_ns})" if d.duration_ns is not None else str(d.start_ns))
        a(f"  {d.id}: basis={d.basis}  {span}")
    for al in inv.timebase.alignments:
        a(f"  alignment {al.a} <-> {al.b}: {al.state.upper()}")
    a("  clock domains are not merged or converted")
    a("")
    a(f"topics ({len(inv.topics)}):")
    for t in inv.topics:
        a(f"  {t.topic}  {t.message_count} msgs  {t.schema_name or '?'}")
    a("")
    cnt = {k: sum(1 for x in inv.artifacts if x.integrity == k) for k in ("verified", "mismatch", "missing", "unsafe", "unlisted")}
    unsupported = [x.relative_path for x in inv.artifacts if not x.supported]
    a(f"artifacts ({len(inv.artifacts)}): verified={cnt['verified']} mismatch={cnt['mismatch']} missing={cnt['missing']} unlisted={cnt['unlisted']} unsupported={len(unsupported)}")
    for x in inv.artifacts:
        if x.integrity in ("mismatch", "missing", "unsafe", "unlisted") or not x.supported:
            a(f"  ! {x.relative_path}: {x.integrity}" + ("" if x.supported else " (unsupported)") + (f" - {'; '.join(x.problems)}" if x.problems else ""))
    a("")
    shown = [g for g in inv.gaps if g.severity != "limitation"]
    lim = len(inv.gaps) - len(shown)
    a(f"gaps and warnings ({len(shown)}; {lim} standing limitation(s) in the JSON):")
    for g in shown[:15]:
        a(f"  [{g.severity}] {g.code}: {g.message}")
    if len(shown) > 15:
        a(f"  (+{len(shown) - 15} more in the JSON)")
    for w in inv.warnings:
        a(f"  [note] {w.code}: {w.message}")
    for e in inv.errors:
        a(f"  [ERROR] {e.code}: {e.message}" + (f" (hint: {e.hint})" if e.hint else ""))
    a("")
    a("Not established: that no message was lost, that timestamps from different clocks are comparable, or that any data is true.")
    return "\n".join(L) + "\n"
