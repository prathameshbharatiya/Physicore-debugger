"""Incident preparation: turn a finished (or already existing) recording into an investigation-ready,
self-contained, locally verifiable package, and say precisely what is and is not established.

This module ORCHESTRATES existing components; it implements no parallel logic:

  discovery / ordering   bag.scan_bag, session.resolve_recording_input      (unchanged)
  validation / gaps      session.analyze_session, validation, manifest      (unchanged)
  capture health         capture_health sidecar + `capture:*` checks, liveness.assess (unchanged)
  copy / hash / verify   export.export_package (staging, atomic publish, never overwrites) (unchanged)
  re-verification        export.verify_package                               (unchanged)

Layout written for one incident (all new files are OUTSIDE the unchanged export package, so
`package verify <incident>/package` keeps working and no existing schema changed):

    <incident>/package/              the export package (manifest.json 0.1/0.2, verified byte copies)
    <incident>/incident.json         machine-readable preparation + status result (schema 1.0)
    <incident>/INCIDENT_SUMMARY.md   concise human-readable summary of incident.json

FOUR separate statements are made and never merged (see `IncidentRecord`):

  artifact_integrity  do the packaged bytes match their recorded digests?
  capture_health      what is known about the recorder's lifecycle (finalized/interrupted/unknown ...)?
  evidence_coverage   which EXPECTED signals were observed / absent / unknown?
  preparation         was the package assembled and re-verified?

A matching digest does not show that no message was lost; a clean recorder exit does not show every
sensor delivered everything; a missing capture-health record is reported as UNKNOWN, never healthy.

Safety properties (each has a test):
  * The source is only READ. Nothing is written inside the recording or its bag directory.
  * Everything is built in a hidden staging directory next to the target and published with one
    rename; a failure (including Ctrl-C/SIGTERM) removes the staging directory.
  * An existing target is never overwritten. Re-running on the same unchanged source is idempotent
    ("already prepared", after re-verifying the existing package); anything else is a conflict.
  * A per-target flock stops two preparations racing; because only its holder creates staging
    directories, leftovers of a hard-killed run are provably stale and are removed by the next run.
  * The copy is self-contained, so free space is checked BEFORE copying (copy size + reserve). The
    reserve is the profile's capture.disk_critical_free_bytes when set, so preparing can never push
    the disk below the level at which capture itself would have stopped.
  * Files are streamed (export's copy_and_hash); recording payloads are never loaded into memory.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import posixpath
import shutil
import signal
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Sequence

from pydantic import Field, ValidationError

from . import checksums
from .bag_metadata import MAX_METADATA_BYTES, parse_bag_metadata
from .capture_health import ACTIVE_STATES, CaptureState, HealthError, LoadedHealth, load_health
from .checksums import FileTooLargeError, read_and_hash
from .errors import (
    ChecksumMismatchError, ExportError, ExportTargetExistsError, IncidentConflictError, IncidentDeferredError,
    IncidentError, IncidentRefusedError, InputError, InsufficientSpaceError, NoRecordingError, PhysiCoreError,
)
from .export import MANIFEST_NAME, export_package, verify_package
from .liveness import assess
from .manifest import build_loss_coverage
from .recovery import list_sessions, recover_session
from .schemas import (
    SHA256_PATTERN, ArtifactReadability, ArtifactRole, CheckOutcome, KnownGap, Manifest, RecordingWindow,
    SessionStatus, ToolInfo, _Strict,
)
from .session import CAPTURE_BAG_DIR, CAPTURE_HEALTH_NAME, SessionContext, analyze_session, resolve_recording_input
from .sessionlock import FileLock, LockHeldError, lock_is_held_elsewhere, read_lock_text
from .supervisor import ACTIVE_LOCK_NAME, PROFILE_COPY_STEM
from .versions import (
    ATTEMPT_SCHEMA_VERSION, INCIDENT_SCHEMA_VERSION, SUPPORTED_ATTEMPT_SCHEMA_VERSIONS, SUPPORTED_INCIDENT_SCHEMA_VERSIONS,
)

INCIDENT_FILE = "incident.json"
SUMMARY_FILE = "INCIDENT_SUMMARY.md"
PACKAGE_DIR = "package"
ATTEMPT_FILE = "incident_attempt.json"
DEFAULT_INCIDENT_DIR = "incident"  # inside a capture session directory
FIXED_OVERHEAD_BYTES = 1 << 20  # manifest, report, incident.json, summary: far less, but never zero
MAX_INCIDENT_BYTES = 8 * 1024 * 1024
_WRAPPER_ENTRIES = frozenset({INCIDENT_FILE, SUMMARY_FILE, PACKAGE_DIR})

Progress = Callable[[str], None]
FreeSpace = Callable[[Path], int]


# --------------------------------------------------------------------------
# Result model (incident.json)
# --------------------------------------------------------------------------

Discovery = Literal[
    "direct", "capture_session_directory", "bag_directory_of_capture_session", "segment_file_promoted_to_bag"
]


class SourceInfo(_Strict):
    """Where the evidence came from. Host paths here are PROVENANCE ONLY: nothing is verified
    through them, and the package itself contains no host paths."""

    input_path: str
    resolved_path: str
    recording_kind: Literal["mcap_file", "rosbag2_directory"]
    discovery: Discovery
    session_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class SegmentRecord(_Strict):
    order: int = Field(ge=0)  # position in the deterministic order used (0 = first)
    relative_path: str
    state: str  # SegmentState value: what was established about THIS file
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    observed_message_count: int | None = Field(default=None, ge=0)
    first_log_time_ns: int | None = None
    last_log_time_ns: int | None = None


class IntegrityStatus(_Strict):
    """Artifact integrity ONLY: do the packaged bytes match their recorded integrity information?
    It says nothing about truth, completeness, or absence of message loss."""

    state: Literal["verified", "mismatch"]
    package_verification: Literal["passed"]  # an incident is only published after verify_package passed
    verified_artifact_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    capture_end_digests: Literal["match", "mismatch", "not_recorded", "not_available"]
    compared: list[str]
    not_established: list[str]


class CaptureHealthStatus(_Strict):
    """What is KNOWN about the recorder's lifecycle. `unknown` means no capture-health record existed:
    it is never read as healthy. Even `finalized` does not show every sensor delivered every message."""

    state: Literal["finalized", "healthy", "starting", "degraded", "interrupted", "failed", "unknown"]
    basis: Literal["capture_health_record", "not_supplied"]
    sidecar_state: str | None = None
    appears_finalized: bool | None = None
    recorder_reported_loss: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    not_established: list[str]


class TopicCoverage(_Strict):
    topic: str
    required: bool
    # present = at least one message was observed; absent = none in a fully readable recording;
    # unknown = the recording is only partly readable, so it may sit in the unread part.
    observed: Literal["present", "absent", "unknown"]
    message_count: int | None = Field(default=None, ge=0)
    cadence: Literal["pass", "fail", "inconclusive", "not_applicable", "no_rule"]


class MetadataKeyCoverage(_Strict):
    key: str
    state: Literal["present", "missing", "unknown"]


class EvidenceCoverage(_Strict):
    """Which EXPECTED (profile) signals were observed. This is coverage of what the profile expects,
    not a statement that nothing else was lost; time intervals are only assessed through cadence rules."""

    state: Literal["expected_signals_observed", "expected_signals_missing", "undetermined"]
    profile_id: str
    expected_topics: list[TopicCoverage]
    required_metadata: list[MetadataKeyCoverage]
    recording_window: RecordingWindow | None = None
    observed_cadence_gap_count: int = Field(ge=0)
    cadence_checked_topics: list[str]
    topics_without_cadence_rules: list[str]
    topics_recorded_but_not_in_profile: list[str]
    trigger_comparison: str | None = None
    not_established: list[str]


class PreparationStatus(_Strict):
    """Was the package assembled and re-verified? Says nothing about the evidence itself."""

    state: Literal["prepared"]  # a failed preparation publishes no incident (see incident_attempt.json)
    package_mode: Literal["self_contained_copy"]
    package_bytes: int = Field(ge=0)
    artifact_count: int = Field(ge=1)
    verified_before_publish: Literal[True] = True
    statement: str


class PackageRef(_Strict):
    directory: str
    manifest: str
    manifest_schema_version: str
    manifest_size_bytes: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)  # binds incident.json to the exact manifest


class IncidentRecord(_Strict):
    incident_schema_version: str = INCIDENT_SCHEMA_VERSION
    tool: ToolInfo = Field(default_factory=ToolInfo)
    incident_id: str = Field(min_length=1)
    created_at_utc: str
    # success: package verified, existing validation says `complete`, capture health not adverse.
    # degraded: a package exists and is verified, but the evidence is partial/failed/interrupted/changed.
    # (A preparation that could not produce a verified package is a FAILURE: no incident is published.)
    outcome: Literal["success", "degraded"]
    outcome_reasons: list[str]
    session_status: SessionStatus  # the EXISTING validation taxonomy (complete | partial | failed)
    session_status_reasons: list[str]
    source: SourceInfo
    source_fingerprint: str = Field(pattern=SHA256_PATTERN)
    segment_order_basis: Literal["metadata", "inferred_from_observed_log_time", "single_file"]
    segments: list[SegmentRecord]
    artifact_integrity: IntegrityStatus
    capture_health: CaptureHealthStatus
    evidence_coverage: EvidenceCoverage
    preparation: PreparationStatus
    package: PackageRef
    known_gaps: list[KnownGap]
    notes: list[str] = Field(default_factory=list)


class AttemptRecord(_Strict):
    """The state of preparation for ONE capture session, next to `capture_health.json` (never inside the bag or the
    package). It is also the pending-preparation queue entry: a session whose record says `deferred` or `failed` (or that
    has no incident at all) is pending work, discoverable after any restart by scanning, with no daemon and no database.

    Schema 1.1 (1.0 files stay readable). `state`:
      prepared  an incident was published (see `incident`);
      deferred  NOT prepared on purpose, to protect a recording or because the recorder may still be writing; `code`
                (`deferred_*`) says why and `retry_command` how. Not a failure;
      failed    a real attempt failed; `code` says why. Nothing was published; the source is untouched.
    """

    attempt_schema_version: str = ATTEMPT_SCHEMA_VERSION
    state: Literal["prepared", "failed", "deferred"]
    attempted_at_utc: str
    first_attempted_at_utc: str | None = None  # 1.1
    attempt_count: int = Field(default=1, ge=1)  # 1.1
    scheduled_by: Literal["auto", "manual", "pending_run", "service"] | None = None  # 1.1
    incident: str | None = None  # relative path of the incident directory when prepared
    code: str | None = None
    message: str | None = None
    hint: str | None = None
    guard_dirs: list[str] = Field(default_factory=list)  # 1.1: directories checked for active captures
    retry_command: str


# --------------------------------------------------------------------------
# Small file helpers
# --------------------------------------------------------------------------


def _utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)  # without this the rename itself can be lost on power failure


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _default_free_space(path: Path) -> int:
    return shutil.disk_usage(path).free


@contextlib.contextmanager
def _terminable():
    """Turn SIGTERM into KeyboardInterrupt for the duration of a preparation so that staging is cleaned
    up (default SIGTERM would kill the process mid-copy). SIGINT already raises KeyboardInterrupt."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGTERM, _raise)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, old)


terminable = _terminable  # public name for the CLI: graceful cancellation of manual preparation on SIGTERM


# --------------------------------------------------------------------------
# Source discovery
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSource:
    input_path: Path
    recording: Path  # an .mcap file, or a rosbag2 bag directory
    health_path: Path | None  # capture-health sidecar, supplied or discovered
    profile_path: Path | None  # the capture session's own byte-identical profile copy, if any
    session_dir: Path | None
    discovery: Discovery
    notes: tuple[str, ...] = ()


def _profile_copy_in(session_dir: Path) -> Path | None:
    for ext in ("yaml", "json"):
        p = session_dir / f"{PROFILE_COPY_STEM}.{ext}"
        if p.is_file() and not p.is_symlink():
            return p
    return None


def _listed_by_metadata(meta: Path, name: str) -> bool | None:
    """True/False: metadata.yaml next to a file does/does not list it. None: unusable metadata."""
    try:
        raw, _ = read_and_hash(meta, max_bytes=MAX_METADATA_BYTES)
    except (OSError, FileTooLargeError):
        return None
    parsed, _errors = parse_bag_metadata(raw)
    if parsed is None:
        return None
    listed = {PurePosixPath(posixpath.normpath(r)).as_posix() for r in parsed.summary.relative_file_paths}
    return name in listed


def resolve_source(path: str | Path, health: str | Path | None = None) -> ResolvedSource:
    """Turn whatever the engineer pointed at into (recording, sidecar, profile copy). Read-only.

    Accepted: a capture session directory; the `bag/` directory of one; any rosbag2 directory (also a
    directory of loose .mcap files); a single .mcap file. A segment file that metadata.yaml next to it
    lists is PROMOTED to its whole bag, so nobody has to enumerate segments by hand; the promotion is
    recorded in the result. A file that is not listed by its neighbour's metadata.yaml is prepared alone,
    with a note naming the neighbours that were NOT included."""
    p = Path(path)
    if not os.path.lexists(p):
        raise InputError(f"Recording not found: '{p}'", hint="Check the path to the .mcap file, rosbag2 directory or capture session directory.")
    explicit = Path(health) if health is not None else None
    if p.is_dir():
        rec, hp = resolve_recording_input(p, health)
        if rec != p:  # a capture session directory
            return ResolvedSource(p, rec, hp, _profile_copy_in(p), p, "capture_session_directory")
        if explicit is None and p.name == CAPTURE_BAG_DIR and (p.parent / CAPTURE_HEALTH_NAME).is_file():
            return ResolvedSource(
                p, p, p.parent / CAPTURE_HEALTH_NAME, _profile_copy_in(p.parent), p.parent, "bag_directory_of_capture_session",
                (f"capture-health record and profile copy were taken from the capture session '{p.parent.name}' around this bag directory",),
            )
        return ResolvedSource(p, p, explicit, None, None, "direct")
    if not p.is_file():
        raise InputError(f"Recording path is neither a regular file nor a directory: '{p}'")
    notes: list[str] = []
    if p.suffix == ".mcap":
        meta = p.parent / "metadata.yaml"
        if meta.is_file():
            listed = _listed_by_metadata(meta, p.name)
            if listed:
                res = resolve_source(p.parent, health)
                return replace(
                    res, input_path=p, discovery="segment_file_promoted_to_bag",
                    notes=(f"'{p.name}' is a segment listed by metadata.yaml; the whole bag directory '{p.parent.name}' was prepared",) + res.notes,
                )
            notes.append(
                "metadata.yaml next to this file could not be used to relate it to a bag; the file is prepared on its own"
                if listed is None else
                "metadata.yaml next to this file does not list it; the file is prepared on its own"
            )
        siblings = sorted(x.name for x in p.parent.iterdir() if x.suffix == ".mcap" and x != p and x.is_file())
        if siblings:
            shown = ", ".join(siblings[:5]) + (f" (+{len(siblings) - 5} more)" if len(siblings) > 5 else "")
            notes.append(f"{len(siblings)} other .mcap file(s) sit next to this file and are NOT included: {shown}. "
                         "To prepare a whole multi-segment recording, point at its directory.")
    return ResolvedSource(p, p, explicit, None, None, "direct", tuple(notes))


# --------------------------------------------------------------------------
# Status derivation (pure functions of the existing evidence model)
# --------------------------------------------------------------------------

_CAPTURE_NOT_ESTABLISHED = [
    "whether every sensor delivered every expected message (rosbag2 exposes no loss counters)",
    "messages dropped before the recorder received them",
]
_INTEGRITY_NOT_ESTABLISHED = [
    "that the recorded data is true",
    "that the recording is complete or that no message was lost",
]


def _check_map(ctx: SessionContext) -> dict[str, CheckOutcome]:
    return {c.check_id: c.outcome for c in ctx.report.checks}


def derive_integrity(ctx: SessionContext, manifest: Manifest, manifest_sha256: str) -> IntegrityStatus:
    outcomes = _check_map(ctx)
    compared = [
        "every package file against manifest.json (SHA-256 and size; no unlisted or symlinked files)",
        "each source file against its staged copy while copying (three independent digests must agree)",
    ]
    not_established = list(_INTEGRITY_NOT_ESTABLISHED)
    cap = outcomes.get("capture:output_unchanged")
    if ctx.health is None or cap is None:
        end = "not_available"
        not_established.append("whether the bag changed after capture ended (no capture-health record)")
    elif cap is CheckOutcome.PASS:
        end = "match"
        compared.append("bag files against the digests the supervisor recorded when capture ended")
    elif cap is CheckOutcome.FAIL:
        end = "mismatch"
        compared.append("bag files against the digests the supervisor recorded when capture ended")
    else:
        end = "not_recorded"
        not_established.append("whether the bag changed after capture ended (no digests were recorded)")
    return IntegrityStatus(
        state="mismatch" if end == "mismatch" else "verified", package_verification="passed",
        verified_artifact_count=len(manifest.artifacts), manifest_sha256=manifest_sha256,
        capture_end_digests=end, compared=compared, not_established=not_established,
    )


def derive_capture_health(ctx: SessionContext) -> CaptureHealthStatus:
    if ctx.health is None:
        return CaptureHealthStatus(
            state="unknown", basis="not_supplied",
            reasons=["no capture-health record was supplied or found: how the recording was started and stopped is unknown"],
            not_established=["how the recording was started and stopped", *_CAPTURE_NOT_ESTABLISHED],
        )
    h = ctx.health.health
    a = assess(h, ctx.health.path.parent)
    return CaptureHealthStatus(
        state=a.capture, basis="capture_health_record", sidecar_state=h.state.value,  # type: ignore[arg-type]
        appears_finalized=h.bag_finalization.appears_finalized,
        recorder_reported_loss=bool(h.recorder.reported_loss_lines),
        reasons=list(a.reasons), not_established=list(_CAPTURE_NOT_ESTABLISHED),
    )


def derive_coverage(ctx: SessionContext) -> EvidenceCoverage:
    prof = ctx.profile.profile
    outcomes = _check_map(ctx)
    topics: list[TopicCoverage] = []
    for t in prof.topics:
        pres = outcomes.get(f"topic_present:{t.name}")
        observed = {CheckOutcome.PASS: "present", CheckOutcome.FAIL: "absent"}.get(pres, "unknown")  # type: ignore[arg-type]
        cad = outcomes.get(f"cadence:{t.name}")
        cadence = "no_rule" if cad is None else cad.value
        n = ctx.scan.message_count_for_topic(t.name) if observed == "present" else (0 if observed == "absent" else None)
        topics.append(TopicCoverage(topic=t.name, required=t.required, observed=observed, message_count=n, cadence=cadence))  # type: ignore[arg-type]
    meta = []
    for key in prof.required_metadata_keys:
        o = outcomes.get(f"metadata:{key}")
        meta.append(MetadataKeyCoverage(key=key, state={CheckOutcome.PASS: "present", CheckOutcome.FAIL: "missing"}.get(o, "unknown")))  # type: ignore[arg-type]
    req = [t for t in topics if t.required]
    if any(t.observed == "absent" for t in req):
        state = "expected_signals_missing"
    elif any(t.observed == "unknown" for t in req) or ctx.report.artifact_readability is not ArtifactReadability.READABLE:
        state = "undetermined"
    else:
        state = "expected_signals_observed"
    loss = build_loss_coverage(ctx)
    profile_names = {t.name for t in prof.topics}
    unknown = [
        "whether any expected message is missing from a topic that has some messages (no sequence or DDS-level analysis)",
        "time intervals other than gaps in the cadence-checked topics; silence before the first and after the last message is not evaluated",
    ]
    if ctx.report.artifact_readability is not ArtifactReadability.READABLE:
        unknown.append("content after the first read error (the recording is not fully readable)")
    tc = ctx.report.trigger_comparison
    return EvidenceCoverage(
        state=state, profile_id=prof.profile_id, expected_topics=topics, required_metadata=meta,
        recording_window=ctx.scan.inspection.recording_window,
        observed_cadence_gap_count=_observed_gap_count(ctx),
        cadence_checked_topics=loss.cadence_checked_topics, topics_without_cadence_rules=loss.topics_without_cadence_rules,
        topics_recorded_but_not_in_profile=sorted(ctx.scan.topic_names() - profile_names),
        trigger_comparison=tc.result.value if tc is not None else None, not_established=unknown,
    )


def _observed_gap_count(ctx: SessionContext) -> int:
    """Total cadence gaps the existing validation observed (timing evidence only, not proof of loss)."""
    return sum(
        int(c.observed.get("gap_count", 0)) for c in ctx.report.checks
        if c.check_id.startswith("cadence:") and c.outcome is CheckOutcome.FAIL
    )


def derive_outcome(
    status: SessionStatus, status_reasons: list[str], integrity: IntegrityStatus, health: CaptureHealthStatus,
    coverage: EvidenceCoverage,
) -> tuple[Literal["success", "degraded"], list[str]]:
    """success only if nothing adverse is KNOWN. Unknowns never raise the outcome above what is established,
    but they are never hidden either: they stay in the record and in the summary."""
    reasons: list[str] = []
    if status is not SessionStatus.COMPLETE:
        reasons.append(f"validation status is {status.value.upper()}: " + "; ".join(status_reasons))
    if integrity.state == "mismatch":
        reasons.append("the bag differs from the digests recorded when the capture ended")
    if health.state in ("interrupted", "failed", "degraded"):
        reasons.append(f"capture health is {health.state.upper()}" + (": " + "; ".join(health.reasons) if health.reasons else ""))
    if health.recorder_reported_loss:
        reasons.append("the recorder itself reported dropped or unflushed messages")
    if coverage.state == "expected_signals_missing":
        missing = [t.topic for t in coverage.expected_topics if t.required and t.observed == "absent"]
        reasons.append(f"required topic(s) absent: {missing}")
    return ("degraded" if reasons else "success"), reasons


# --------------------------------------------------------------------------
# Record construction
# --------------------------------------------------------------------------


def _fingerprint(ctx: SessionContext) -> str:
    parts = [
        ("kind", ctx.recording_kind), ("recording", ctx.recording_sha256), ("profile", ctx.profile.sha256),
        ("health", ctx.health.sha256 if ctx.health else "none"), ("metadata", ctx.metadata.sha256 if ctx.metadata else "none"),
        ("trigger", ctx.trigger.sha256 if ctx.trigger else "none"),
    ]
    return hashlib.sha256("".join(f"{k} {v}\n" for k, v in parts).encode()).hexdigest()


def _segments(ctx: SessionContext) -> tuple[list[SegmentRecord], str]:
    if ctx.bag is not None:
        info = ctx.bag.info
        return [
            SegmentRecord(
                order=i, relative_path=s.relative_path, state=s.state.value, size_bytes=s.size_bytes, sha256=s.sha256,
                observed_message_count=s.observed_message_count, first_log_time_ns=s.first_log_time_ns,
                last_log_time_ns=s.last_log_time_ns,
            )
            for i, s in enumerate(info.segments)
        ], info.segment_order_basis
    insp = ctx.scan.inspection
    w = insp.recording_window
    return [SegmentRecord(
        order=0, relative_path=ctx.recording_path.name, state=insp.readability.value, size_bytes=insp.size_bytes,
        sha256=ctx.recording_sha256, observed_message_count=insp.total_message_count,
        first_log_time_ns=w.min_log_time_ns if w else None, last_log_time_ns=w.max_log_time_ns if w else None,
    )], "single_file"


def build_record(
    ctx: SessionContext, resolved: ResolvedSource, manifest: Manifest, manifest_bytes: bytes, fingerprint: str,
    now: datetime, extra_notes: list[str],
) -> IncidentRecord:
    msha = hashlib.sha256(manifest_bytes).hexdigest()
    integrity = derive_integrity(ctx, manifest, msha)
    health = derive_capture_health(ctx)
    coverage = derive_coverage(ctx)
    outcome, reasons = derive_outcome(ctx.report.status, ctx.report.status_reasons, integrity, health, coverage)
    segs, basis = _segments(ctx)
    sid = ctx.health.health.session_id if ctx.health else None
    if sid is None and resolved.session_dir is not None:
        sid = resolved.session_dir.name
    stem = ctx.recording_path.resolve().name.removesuffix(".mcap") or "recording"
    return IncidentRecord(
        incident_id=sid or f"{stem}-{fingerprint[:12]}", created_at_utc=_utc_stamp(now), outcome=outcome, outcome_reasons=reasons,
        session_status=ctx.report.status, session_status_reasons=list(ctx.report.status_reasons),
        source=SourceInfo(
            input_path=str(resolved.input_path), resolved_path=str(ctx.recording_path), recording_kind=ctx.recording_kind,  # type: ignore[arg-type]
            discovery=resolved.discovery, session_id=sid, notes=list(resolved.notes),
        ),
        source_fingerprint=fingerprint, segment_order_basis=basis, segments=segs,  # type: ignore[arg-type]
        artifact_integrity=integrity, capture_health=health, evidence_coverage=coverage,
        preparation=PreparationStatus(
            state="prepared", package_mode="self_contained_copy", package_bytes=sum(a.size_bytes for a in manifest.artifacts),
            artifact_count=len(manifest.artifacts),
            statement="The package is a self-contained byte copy that was re-verified against its manifest before it was published. "
                      "It says nothing about the evidence itself: see the other three statements and known_gaps.",
        ),
        package=PackageRef(
            directory=PACKAGE_DIR, manifest=f"{PACKAGE_DIR}/{MANIFEST_NAME}", manifest_schema_version=manifest.manifest_schema_version,
            manifest_size_bytes=len(manifest_bytes), manifest_sha256=msha,
        ),
        known_gaps=list(manifest.known_gaps), notes=list(extra_notes),
    )


def _record_bytes(record: IncidentRecord) -> bytes:
    return (record.model_dump_json(indent=2) + "\n").encode()


# --------------------------------------------------------------------------
# Rendering (one renderer: valid Markdown AND readable as plain text in a terminal)
# --------------------------------------------------------------------------

_INTEGRITY_TEXT = {"verified": "VERIFIED", "mismatch": "MISMATCH"}
DISCOVERY_TEXT = {
    "direct": "the path as given", "capture_session_directory": "a capture session directory",
    "bag_directory_of_capture_session": "the bag directory of a capture session",
    "segment_file_promoted_to_bag": "a segment file, promoted to its whole bag",
}


def render_incident(r: IncidentRecord, *, path: Path | None = None) -> str:
    ig, ch, ev, pr = r.artifact_integrity, r.capture_health, r.evidence_coverage, r.preparation
    L = [f"# Incident {r.incident_id}: {r.outcome.upper()}", ""]
    if path is not None:
        L += [f"- Location: {path}"]
    L += [
        f"- Prepared: {r.created_at_utc} by {r.tool.name} {r.tool.version}",
        f"- Source: {r.source.recording_kind.replace('_', ' ')} (found via {DISCOVERY_TEXT[r.source.discovery]})",
        f"- Segments: {len(r.segments)} in order ({r.segment_order_basis.replace('_', ' ')})",
        f"- Validation status (existing taxonomy): {r.session_status.value.upper()}",
        "",
    ]
    if r.outcome_reasons:
        L += ["## Why this is DEGRADED", *[f"- {x}" for x in r.outcome_reasons], ""]
    L += ["## Four separate statements (none implies another)"]
    L += [f"- Artifact integrity: {_INTEGRITY_TEXT[ig.state]}. Package files match the manifest ({ig.verified_artifact_count} artifact(s)); "
          f"bag vs. digests recorded at capture end: {ig.capture_end_digests.replace('_', ' ')}."]
    cause = f" ({'; '.join(ch.reasons)})" if ch.reasons and ch.state != "finalized" else ""
    L += [f"- Capture health: {ch.state.upper()}{cause}"
          + (f" [recorder state '{ch.sidecar_state}', recorder-reported loss: {'YES' if ch.recorder_reported_loss else 'none reported'}]"
             if ch.basis == "capture_health_record" else "")]
    absent = [t.topic for t in ev.expected_topics if t.observed == "absent"]
    unk = [t.topic for t in ev.expected_topics if t.observed == "unknown"]
    L += [f"- Evidence coverage: {ev.state.replace('_', ' ').upper()}; {sum(t.observed == 'present' for t in ev.expected_topics)}/"
          f"{len(ev.expected_topics)} expected topic(s) observed"
          + (f", absent: {absent}" if absent else "") + (f", unknown: {unk}" if unk else "")
          + f"; {ev.observed_cadence_gap_count} cadence gap(s) observed"]
    L += [f"- Preparation: {pr.state.upper()} (self-contained copy, {pr.artifact_count} files, {pr.package_bytes} bytes), "
          "re-verified before publishing", ""]
    L += ["## Not established"]
    seen: set[str] = set()
    for x in [*ig.not_established, *ch.not_established, *ev.not_established]:
        if x not in seen:
            seen.add(x)
            L.append(f"- {x}")
    L += ["", "## Known gaps (from manifest)", *[f"- {g.code}" for g in r.known_gaps], ""]
    if r.source.notes or r.notes:
        L += ["## Notes", *[f"- {x}" for x in [*r.source.notes, *r.notes]], ""]
    L += ["## Verify offline", "- `physicore-collector incident show <incident-dir> --verify`",
          f"- `physicore-collector package verify <incident-dir>/{PACKAGE_DIR}`"]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Loading / verifying / listing
# --------------------------------------------------------------------------


def _incident_dir_of(path: Path) -> Path:
    p = Path(path)
    if p.is_file() and p.name == INCIDENT_FILE:
        return p.parent
    if p.is_dir() and not (p / INCIDENT_FILE).is_file() and (p / DEFAULT_INCIDENT_DIR / INCIDENT_FILE).is_file():
        return p / DEFAULT_INCIDENT_DIR
    return p


def load_incident(path: str | Path) -> tuple[IncidentRecord, Path]:
    """Strictly load an incident from its directory, its incident.json, or a capture session directory.
    Raises IncidentError for anything unreadable or of an unsupported version."""
    d = _incident_dir_of(Path(path))
    f = d / INCIDENT_FILE
    if not f.is_file() or f.is_symlink():
        raise IncidentError(f"No incident found at '{path}' (no {INCIDENT_FILE}).",
                            hint="Pass an incident directory, a capture session directory, or run `incident prepare` first.", code="not_an_incident")
    try:
        raw, _ = read_and_hash(f, max_bytes=MAX_INCIDENT_BYTES)
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, FileTooLargeError, ValueError) as e:
        raise IncidentError(f"Cannot read '{f}': {e}", code="incident_unreadable") from e
    ver = data.get("incident_schema_version") if isinstance(data, dict) else None
    if ver not in SUPPORTED_INCIDENT_SCHEMA_VERSIONS:
        raise IncidentError(f"Unsupported incident_schema_version {ver!r} in '{f}'; supported: {list(SUPPORTED_INCIDENT_SCHEMA_VERSIONS)}",
                            code="incident_unsupported")
    try:
        return IncidentRecord.model_validate(data), d
    except ValidationError as e:
        raise IncidentError(f"'{f}' is not a valid incident record: {e.error_count()} error(s): {e.errors(include_url=False)[0]['msg']}",
                            code="incident_invalid") from e


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of re-checking an incident NOW. `tampered` means: does not match what was prepared (modified, truncated,
    replaced, damaged or extended; this check cannot tell which). `unverifiable` means the check could not be completed
    (unreadable record/file, or deferred because a capture is active); it is NOT a pass."""

    state: Literal["verified_now", "tampered", "unverifiable"]
    problems: tuple[str, ...] = ()
    deferred: bool = False


def _incident_problems(record: IncidentRecord, d: Path) -> list[str]:
    problems: list[str] = []
    for e in sorted(os.listdir(d)):
        if e not in _WRAPPER_ENTRIES:
            problems.append(f"{e}: unexpected entry in the incident directory")
    for need in (SUMMARY_FILE, PACKAGE_DIR):
        if not (d / need).exists():
            problems.append(f"{need}: missing")
    pkg = d / PACKAGE_DIR
    if pkg.is_dir() and not pkg.is_symlink():
        problems += [f"package: {x}" for x in verify_package(pkg)]
        mpath = pkg / MANIFEST_NAME
        if mpath.is_file() and not mpath.is_symlink():  # a symlinked manifest was already reported and must not be read
            raw = mpath.read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.package.manifest_sha256:
                problems.append("package/manifest.json differs from the digest recorded in incident.json")
            else:
                try:
                    if Manifest.model_validate_json(raw).session_status is not record.session_status:
                        problems.append("incident.json session_status differs from the manifest")
                except ValueError:
                    pass  # verify_package already reported an invalid manifest
    elif PACKAGE_DIR in os.listdir(d):
        problems.append("package: not a directory")
    return problems


def verify_incident_detailed(
    path: str | Path, *, respect_active: bool = False, guard_dirs: Sequence[str | Path] = (),
) -> VerificationResult:
    """Offline re-check of an incident: every package file against its manifest (full re-hash), the manifest against the
    digest recorded in incident.json, the record against the manifest, and no unexpected entries. Verified means the
    bytes are what was prepared; nothing more.

    `respect_active=True` applies the scheduling policy: re-hashing every byte competes with a recording, so with a
    capture active the result is `unverifiable` (deferred), and it stops the same way if a capture starts meanwhile."""
    try:
        record, d = load_incident(path)
    except IncidentError as e:
        return VerificationResult("unverifiable", (e.message,))
    # the capture output directory (where `.active-capture.lock` lives) is the parent of a session directory
    out_root = d.parent.parent if (d.parent / CAPTURE_HEALTH_NAME).is_file() else None
    dirs = collect_guard_dirs(d.parent, out_root, extra=guard_dirs) if respect_active else []
    if dirs:
        active = find_active_captures(dirs)
        if active:
            where = "; ".join(f"'{a.directory}'" for a in active)
            return VerificationResult("unverifiable", (
                f"not verified now: a capture is active in {where}, and re-hashing the package would compete with it. "
                "Retry when idle, or pass --allow-while-recording.",), deferred=True)
    gov = IoGovernor(dirs, out_hint="") if dirs else None
    try:
        with (checksums.io_hook(gov) if gov else contextlib.nullcontext()):
            problems = _incident_problems(record, d)
    except IncidentDeferredError:
        return VerificationResult("unverifiable", ("not verified: a capture started while verifying; stopped so as not to compete "
                                                   "with it. Retry when idle.",), deferred=True)
    except OSError as e:
        return VerificationResult("unverifiable", (f"could not read part of the incident: {e.strerror or e}",))
    return VerificationResult("tampered", tuple(problems)) if problems else VerificationResult("verified_now")


def verify_incident(path: str | Path) -> list[str]:
    """Problems found re-checking an incident (empty = verified now). Unguarded; see `verify_incident_detailed`."""
    r = verify_incident_detailed(path)
    return list(r.problems)


@dataclass(frozen=True)
class IncidentListing:
    """One row of `incident list`. `state` is about the PACKAGE; `outcome` is about the EVIDENCE: they are independent
    (a package can be `verified_now` and its outcome `degraded`).

    state: previously_prepared (stored record only: NOT re-checked, so no claim that the package is intact now) |
           verified_now | tampered | unverifiable | deferred | failed | not_prepared | unreadable (record unusable).
    `integrity_at_preparation` / `capture_health` / `evidence_coverage` are copied from the stored record."""

    name: str
    path: Path
    state: str
    outcome: str | None = None
    session_status: str | None = None
    integrity_at_preparation: str | None = None
    capture_health: str | None = None
    evidence_coverage: str | None = None
    created_at_utc: str | None = None
    detail: str | None = None


def _listing_for_incident(name: str, d: Path) -> IncidentListing:
    try:
        r, _ = load_incident(d)
    except IncidentError as e:
        return IncidentListing(name, d, "unreadable", detail=e.message)
    return IncidentListing(
        name, d, "previously_prepared", r.outcome, r.session_status.value, r.artifact_integrity.state, r.capture_health.state,
        r.evidence_coverage.state, r.created_at_utc,
    )


def _read_attempt(session_dir: Path) -> AttemptRecord | None:
    f = session_dir / ATTEMPT_FILE
    try:
        raw, _ = read_and_hash(f, max_bytes=MAX_INCIDENT_BYTES)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("attempt_schema_version") not in SUPPORTED_ATTEMPT_SCHEMA_VERSIONS:
            return None
        return AttemptRecord.model_validate(data)
    except (OSError, ValueError, FileTooLargeError):
        return None


def list_incidents(
    root: str | Path, *, verify: bool = False, respect_active: bool = True, guard_dirs: Sequence[str | Path] = (),
) -> list[IncidentListing]:
    """Incidents found under `root`: `root` itself if it is an incident; otherwise each child that is an incident
    or a capture session (a session without an incident is `deferred`, `failed` or `not_prepared`).

    By default a prepared incident is `previously_prepared`: that is the STORED result, not a check, and it is never
    labelled verified. `verify=True` re-hashes every package (cost: reading every package byte, so minutes for large
    ones; with `respect_active` it will not do so while a capture is active) and sets `verified_now`, `tampered`
    or `unverifiable`."""
    r = Path(root)
    if not r.is_dir():
        raise InputError(f"Not a directory: '{r}'", hint="Pass the directory that holds capture sessions or incident directories.")

    def incident_row(name: str, d: Path) -> IncidentListing:
        row = _listing_for_incident(name, d)
        if verify and row.state == "previously_prepared":
            v = verify_incident_detailed(d, respect_active=respect_active, guard_dirs=[r, *guard_dirs])
            return replace(row, state=v.state, detail="; ".join(v.problems[:3]) or None)
        return row

    if (r / INCIDENT_FILE).is_file():
        return [incident_row(r.name, r)]
    rows: list[IncidentListing] = []
    for d in sorted(r.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.is_symlink():
            continue
        if (d / INCIDENT_FILE).is_file():
            rows.append(incident_row(d.name, d))
        elif (d / DEFAULT_INCIDENT_DIR / INCIDENT_FILE).is_file():
            rows.append(incident_row(d.name, d / DEFAULT_INCIDENT_DIR))
        elif (d / CAPTURE_HEALTH_NAME).is_file():
            att = _read_attempt(d)
            if att is not None and att.state in ("failed", "deferred"):
                rows.append(IncidentListing(d.name, d, att.state, detail=f"{att.code}: {att.message}. Retry: {att.retry_command}"))
            else:
                rows.append(IncidentListing(d.name, d, "not_prepared", detail=f"no incident yet. Prepare: physicore-collector incident prepare {d}"))
    return rows


# --------------------------------------------------------------------------
# Scheduling: never knowingly compete with a recording
# --------------------------------------------------------------------------
#
# Policy (also in docs/OPERATIONS.md):
#   * The only evidence of an active recording PhysiCore has is the kernel lock `.active-capture.lock` that every
#     capture holds in its OUTPUT directory. Preparation therefore checks the directories it is told about: the
#     capture output directory, the incident target's directory, `--guard-dir` and $PHYSICORE_GUARD_DIRS.
#   * Different directories are NEVER assumed to be different disks, and captures in directories that are not named
#     are invisible. List every capture output directory that shares storage with the incident target.
#   * Before starting, an active capture means: do not start (deferred). While running, the same check repeats at
#     checkpoints that cover EVERY bulk read/write PhysiCore issues: the MCAP scan (every read), hashing, copying and
#     package re-verification all report to `checksums.io_hook`, and the governor probes at most every
#     CHECK_INTERVAL_SECONDS; explicit checkpoints also run between phases. A capture that appears makes preparation
#     stop cleanly (staging removed, source untouched, state `deferred`).
#   * THE RACE IS BOUNDED, NOT ELIMINATED. Capture locking is deliberately unchanged, so preparation cannot hold
#     anything a capture would have to wait for (that would block recording, which is the one thing this feature must
#     never do). A capture can therefore start right after a check; both then touch storage until the next probe.
#     The overlap is at most CHECK_INTERVAL_SECONDS plus ONE uninterruptible unit: one read/write/fsync call (reads
#     are record-sized; while governed, copies `fdatasync` every `checksums.SYNC_EVERY_BYTES`). A capture's recorder
#     starts writing only after the capture holds `.active-capture.lock`, so a probe made after that moment always
#     sees it. What is NOT closed: captures in directories nobody listed (invisible), and the kernel's own writeback.
#   * An optional explicit throttle caps the bytes PhysiCore moves through its own loops. It reduces contention;
#     it does not guarantee a recording is safe. The only guarantee is not running at all.
#   * Independently of any lock, a recording that a process holds open for WRITING is never packaged (see
#     `find_live_writers`): that is how an unsupervised `ros2 bag record` is noticed without any sidecar.

GUARD_ENV = "PHYSICORE_GUARD_DIRS"
THROTTLE_ENV = "PHYSICORE_PREPARE_MAX_MIB_PER_SEC"
CHECK_INTERVAL_SECONDS = 0.1  # a probe is one open+flock+close: microseconds against a MiB-sized read
# Failures that a plain retry cannot fix: an operator has to act first.
NEEDS_ATTENTION_CODES = frozenset({"input_error", "no_recording_files", "target_exists", "capture_health_unreadable",
                                   "incident_unreadable", "cleanup_failed"})


@dataclass(frozen=True)
class ActiveCapture:
    directory: Path
    holder: str | None


def env_guard_dirs() -> list[Path]:
    return [Path(x) for x in os.environ.get(GUARD_ENV, "").split(os.pathsep) if x.strip()]


def env_throttle_mib() -> float | None:
    raw = os.environ.get(THROTTLE_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        v = float(raw)
    except ValueError:
        v = 0.0
    if not v > 0:
        raise InputError(f"{THROTTLE_ENV} must be a positive number of MiB per second, got {raw!r}.")
    return v


def collect_guard_dirs(*paths: Path | None, extra: Sequence[str | Path] = ()) -> list[Path]:
    """The directories to check for active captures (deduplicated by real path; only existing directories)."""
    seen: dict[str, Path] = {}
    for p in [*paths, *map(Path, extra), *env_guard_dirs()]:
        if p is None:
            continue
        d = Path(p)
        if d.is_dir():
            seen.setdefault(os.path.realpath(d), d)
    return list(seen.values())


def find_active_captures(dirs: Sequence[Path]) -> list[ActiveCapture]:
    """Captures currently active in `dirs` (kernel lock probe: a crashed capture's lock is already released).
    An unreadable lock counts as active: when in doubt, do not compete."""
    out: list[ActiveCapture] = []
    for d in dirs:
        lock = Path(d) / ACTIVE_LOCK_NAME
        held = lock_is_held_elsewhere(lock)
        if held is True:
            out.append(ActiveCapture(Path(d), read_lock_text(lock)))
        elif held is None:
            out.append(ActiveCapture(Path(d), "lock could not be inspected"))
    return out


@dataclass(frozen=True)
class LiveWriter:
    pid: int
    command: str
    path: str


def find_live_writers(recording: Path, *, proc: Path = Path("/proc")) -> tuple[list[LiveWriter], int]:
    """Processes (other than this one) that hold `recording` (a file, or any file inside a directory) open for
    WRITING, and how many processes could not be inspected (-1: /proc is unavailable, so nothing could be checked).

    Linux /proc only. Best effort by nature: a process of another user is invisible unless we run as root, which
    is reported through the second value and never silently treated as "no writer". It makes no claim about a
    recorder that has not opened the files yet."""
    target = os.path.realpath(recording)
    is_dir = os.path.isdir(target)
    me = os.getpid()
    try:
        pids = [e for e in os.listdir(proc) if e.isdigit() and int(e) != me]
    except OSError:
        return [], -1
    found: list[LiveWriter] = []
    unreadable = 0
    for e in pids:
        fddir = proc / e / "fd"
        try:
            fds = os.listdir(fddir)
        except PermissionError:
            unreadable += 1
            continue
        except OSError:
            continue  # the process exited meanwhile
        for fd in fds:
            try:
                link = os.readlink(fddir / fd)
            except OSError:
                continue
            link = link[:-len(" (deleted)")] if link.endswith(" (deleted)") else link
            if not (link == target or (is_dir and link.startswith(target + os.sep))):
                continue
            try:
                with open(proc / e / "fdinfo" / fd) as fi:
                    flags = next((int(line.split()[1], 8) for line in fi if line.startswith("flags:")), None)
            except (OSError, ValueError):
                continue
            if flags is not None and (flags & os.O_ACCMODE) != os.O_RDONLY:
                try:
                    cmd = (proc / e / "cmdline").read_bytes()[:120].replace(b"\0", b" ").decode("utf-8", "replace").strip()
                except OSError:
                    cmd = "?"
                found.append(LiveWriter(int(e), cmd or "?", link))
                break
    return found, unreadable


def _live_writer_deferral(writers: list[LiveWriter], recording: Path) -> IncidentDeferredError:
    w = writers[0]
    return IncidentDeferredError(
        f"A recording process still has '{w.path}' open for writing (pid {w.pid}: {w.command}); '{recording}' is not final and was "
        "not packaged. Nothing was published; the source is untouched.",
        hint="Stop that process (PhysiCore never stops it for you here), then retry. If it is a PhysiCore capture, use `capture stop`; "
             "for a leftover recorder, `capture recover <session> --stop-orphan`.", code="deferred_recorder_alive")


def _deferred_active(active: Sequence[ActiveCapture], *, started_during: bool, out_hint: str) -> IncidentDeferredError:
    where = "; ".join(f"'{a.directory}'" + (f" ({a.holder})" if a.holder else "") for a in active)
    when = "A capture started while this was running, so preparation stopped cleanly" if started_during else \
        "A capture is active, so preparation was not started"
    return IncidentDeferredError(
        f"{when}: it would compete with the recording for storage. Active in {where}. Nothing was published, "
        "the staging directory was removed and the source is untouched.",
        hint=f"This is a deferral, not a failure. Retry when idle: {out_hint}  (or pass --allow-while-recording to "
             "override on purpose).", code="deferred_capture_active")


def _recorder_alive_hint(h, session_dir: Path) -> str:
    if h.state in ACTIVE_STATES:  # stale `running` record: recovery owns this case and verifies identity before signalling
        return (f"Stop it safely first: physicore-collector capture recover {session_dir} --stop-orphan "
                "(only a process whose recorded identity still matches is signalled), then retry.")
    pid = h.recorder.identity.pid if h.recorder.identity else "?"
    return (f"The capture ended but its recorder (pid {pid}) is still running. PhysiCore does not signal it from here; "
            "stop it yourself (identity in capture_health.json), then retry.")


class IoGovernor:
    """The `checksums.io_hook` used during preparation: (1) yields by raising IncidentDeferredError as soon as a
    capture is active in the guarded directories; (2) optionally throttles to `max_bytes_per_sec` over the whole run.
    `clock`/`sleep`/`probe` are injectable for deterministic tests."""

    def __init__(self, dirs: Sequence[Path], *, max_bytes_per_sec: float | None = None, out_hint: str = "",
                 interval: float | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 probe: Callable[[Sequence[Path]], list[ActiveCapture]] | None = None) -> None:
        self.dirs, self.rate, self.out_hint = list(dirs), max_bytes_per_sec, out_hint
        self.interval = CHECK_INTERVAL_SECONDS if interval is None else interval
        self.clock, self.sleep = clock, sleep
        self.probe = probe or (lambda d: find_active_captures(d))  # resolved at call time, not at definition
        self.t0 = clock()
        self.last_check = self.t0
        self.total = 0

    def check_now(self) -> None:
        if self.dirs:
            active = self.probe(self.dirs)
            if active:
                raise _deferred_active(active, started_during=True, out_hint=self.out_hint)
        self.last_check = self.clock()

    def __call__(self, nbytes: int) -> None:
        self.total += nbytes
        if self.clock() - self.last_check >= self.interval:
            self.check_now()
        if self.rate:
            while True:
                lag = self.total / self.rate - (self.clock() - self.t0)
                if lag <= 0:
                    break
                self.sleep(min(lag, self.interval))
                if self.clock() - self.last_check >= self.interval:
                    self.check_now()


# --------------------------------------------------------------------------
# Preparation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparePlan:
    recording_kind: str
    discovery: str
    files: tuple[tuple[str, int], ...]  # (relative path in package, bytes) of what will be copied
    copy_bytes: int
    reserve_bytes: int
    required_bytes: int
    free_bytes: int | None  # None when the target's filesystem could not be queried
    target: Path


@dataclass(frozen=True)
class PrepareResult:
    state: Literal["prepared", "already_prepared", "dry_run"]
    incident_dir: Path | None
    record: IncidentRecord | None
    plan: PreparePlan
    notes: tuple[str, ...] = ()


def failure_info(exc: BaseException) -> tuple[str, str, str | None]:
    """(stable code, message, hint) for any exception a preparation can raise."""
    if isinstance(exc, IncidentError):
        return exc.code, exc.message, exc.hint
    cause_errno = getattr(exc, "errno", None) or getattr(getattr(exc, "__cause__", None), "errno", None)
    if isinstance(exc, ExportTargetExistsError):
        return "target_exists", exc.message, exc.hint
    if isinstance(exc, ChecksumMismatchError):
        return "checksum_mismatch", exc.message, "The source may have changed or be corrupt on disk; check it, then retry."
    if cause_errno == errno.ENOSPC:
        return "insufficient_space", str(exc), "Free space or choose --out on a larger disk, then retry."
    if isinstance(exc, ExportError):
        if isinstance(exc.__cause__, OSError):  # export wraps environmental failures; keep the honest classification
            return "io_error", exc.message, "Check permissions and disk health, then retry."
        return "export_failed", exc.message, exc.hint
    if isinstance(exc, PhysiCoreError):
        return "input_error", exc.message, exc.hint
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted", "preparation was interrupted; nothing was published and the source is untouched", "Retry the same command."
    if isinstance(exc, OSError):
        return "io_error", f"{exc.strerror or exc}", "Check permissions and disk health, then retry."
    return "internal_error", f"{type(exc).__name__}: {exc}", "This is a bug; the source is untouched and preparation can be retried."


def _ensure_outside(target: Path, recording: Path) -> None:
    if not recording.is_dir():
        return
    rec, tgt = os.path.realpath(recording), os.path.realpath(target)
    if tgt == rec or tgt.startswith(rec + os.sep):
        raise InputError(f"The incident target '{target}' is inside the recording directory '{recording}'.",
                         hint="Choose a --out outside the recording; preparation never writes into a recording.")


def _existing_incident(target: Path, fingerprint: str) -> IncidentRecord:
    """The target exists. Return its record iff it is the SAME source, and still verifies; else conflict."""
    try:
        rec, _ = load_incident(target)
    except IncidentError as e:
        raise IncidentConflictError(f"'{target}' already exists and is not a readable incident ({e.message}). It was not touched.",
                                    hint="Choose a new --out.") from e
    if rec.source_fingerprint != fingerprint:
        raise IncidentConflictError(
            f"A different incident already exists at '{target}' (its source, profile or sidecar differ from the current input). It was not touched.",
            hint="Choose a new --out; preparation never overwrites. (After `capture recover` rewrote the sidecar, prepare to a new directory.)")
    problems = verify_incident(target)
    if problems:
        raise IncidentConflictError(
            f"The incident at '{target}' is for this same input but fails verification: " + "; ".join(problems[:3]) + ". It was not touched.",
            hint="Move or delete that directory yourself, then retry.")
    return rec


def _remove_stale_staging(parent: Path, name: str, notes: list[str]) -> None:
    prefix = f".{name}.incident-staging-"
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return
    for e in entries:
        if e.name.startswith(prefix) and e.is_dir(follow_symlinks=False):
            try:
                shutil.rmtree(e.path)
                notes.append(f"removed stale staging directory '{e.name}' left by an interrupted earlier preparation")
            except OSError as err:
                notes.append(f"could not remove stale staging directory '{e.name}': {err.strerror or err}")


def _plan(ctx: SessionContext, resolved: ResolvedSource, target: Path, free: int | None, reserve: int) -> PreparePlan:
    if ctx.bag is not None:
        base = f"recording/{ctx.bag.info.bag_directory_name}"
        files = [(f"{base}/{f.relative_path}", f.size_bytes) for f in sorted(ctx.bag.files, key=lambda f: f.relative_path)]
    else:
        files = [(f"recording/{ctx.recording_path.name}", ctx.scan.inspection.size_bytes)]
    small = [ctx.profile.path, *(x.path for x in (ctx.metadata, ctx.trigger) if x is not None)]
    for p in small:
        try:
            files.append((p.name, p.stat().st_size))
        except OSError:
            pass
    if ctx.health is not None:
        files.append(("capture/capture_health.json", len(ctx.health.raw)))
    copy_bytes = sum(n for _, n in files)
    return PreparePlan(ctx.recording_kind, resolved.discovery, tuple(files), copy_bytes, reserve,
                       copy_bytes + FIXED_OVERHEAD_BYTES + reserve, free, target)


def _publish(staging: Path, target: Path) -> None:
    if os.path.lexists(target):
        raise IncidentConflictError(f"Target appeared while preparing: '{target}'. Nothing was overwritten.")
    try:
        os.rename(staging, target)
    except OSError as e:
        if e.errno in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR, errno.EISDIR):
            raise IncidentConflictError(f"Target appeared while preparing: '{target}'. Nothing was overwritten.") from e
        raise
    _fsync_dir(target.parent)


def _prepare_resolved(
    resolved: ResolvedSource, profile: Path, target: Path, *, metadata: Path | None, trigger: Path | None,
    dry_run: bool, now: datetime, free_space: FreeSpace, progress: Progress,
    guard_dirs: Sequence[str | Path] = (), respect_active: bool = True, max_bytes_per_sec: float | None = None,
) -> PrepareResult:
    notes: list[str] = []
    exists = os.path.lexists(target)
    if exists and not (target.is_dir() and not target.is_symlink() and (target / INCIDENT_FILE).is_file()):
        raise IncidentConflictError(f"'{target}' already exists and is not a prepared incident. It was not touched.",
                                    hint="Choose a new --out; preparation never overwrites.")
    if not target.parent.is_dir():
        raise InputError(f"Parent directory of the incident target does not exist: '{target.parent}'", hint="Create it first.")
    _ensure_outside(target, resolved.recording)

    # --- scheduling gate 1: do not even start while a capture is active in a guarded directory (writes nothing)
    dirs = collect_guard_dirs(target.parent, resolved.recording.parent,
                              resolved.session_dir.parent if resolved.session_dir else None, extra=guard_dirs)
    retry_hint = (f"physicore-collector incident pending run {resolved.session_dir.parent}" if resolved.session_dir
                  else f"physicore-collector incident prepare {resolved.input_path} ...")
    if respect_active:
        active = find_active_captures(dirs)
        if active:
            raise _deferred_active(active, started_during=False, out_hint=retry_hint)
    governor = IoGovernor(dirs if respect_active else [], max_bytes_per_sec=max_bytes_per_sec, out_hint=retry_hint) \
        if (respect_active or max_bytes_per_sec) else None

    lock: FileLock | None = None
    if not dry_run:
        lock = FileLock(target.parent / f".{target.name}.prepare.lock")
        try:
            lock.acquire()
        except LockHeldError as e:
            raise IncidentError(f"Another preparation of '{target}' is already running.", code="preparation_in_progress",
                                hint="Wait for it to finish; this one changed nothing.") from e
        except OSError as e:
            raise IncidentError(f"Cannot prepare in '{target.parent}': {e.strerror or e}", code="io_error",
                                hint="Check that the directory is writable.") from e
    stack = contextlib.ExitStack()
    try:
        loaded_health: LoadedHealth | None = load_health(resolved.health_path) if resolved.health_path is not None else None
        if loaded_health is not None:
            a = assess(loaded_health.health, loaded_health.path.parent)
            if a.supervisor == "alive" or a.capture in ("starting", "healthy"):
                raise IncidentRefusedError(
                    f"The capture '{loaded_health.health.session_id}' is still active ({a.capture}); its evidence is not final.",
                    hint="Stop it with `capture stop` first. Nothing was prepared.")
            # --- scheduling gate 2: a recorder that may still be writing means the bag is not final, whatever the
            # sidecar says (a crashed supervisor leaves its recorder behind). We never signal it from here.
            if respect_active and a.recorder in ("running", "orphaned", "unknown"):
                raise IncidentDeferredError(
                    f"The recorder of '{loaded_health.health.session_id}' may still be writing to the bag (recorder: {a.recorder}). "
                    "Nothing was prepared.",
                    hint=_recorder_alive_hint(loaded_health.health, loaded_health.path.parent), code="deferred_recorder_alive")
        writers, unreadable = find_live_writers(resolved.recording)
        if writers:
            raise _live_writer_deferral(writers, resolved.recording)
        if unreadable:
            notes.append(f"{unreadable} process(es) could not be inspected for open writers (insufficient permission); "
                         "a live recorder owned by another user would not have been noticed" if unreadable > 0 else
                         "/proc is unavailable: whether a process still writes to the recording could not be checked")
        if not respect_active and find_active_captures(dirs):
            notes.append("prepared while a capture was active in a guarded directory (operator override --allow-while-recording)")
        if governor is not None:
            stack.enter_context(checksums.io_hook(governor))
        progress(f"Discovering and checking '{resolved.recording}' ({resolved.discovery.replace('_', ' ')}) ...")
        ctx = analyze_session(resolved.recording, profile, trigger_path=trigger, metadata_path=metadata,
                              health_path=resolved.health_path)
        has_recording = (any(f.role is ArtifactRole.RECORDING for f in ctx.bag.files) if ctx.bag is not None
                         else ctx.recording_path.is_file())
        if not has_recording:
            raise NoRecordingError(f"No readable recording file was found in '{resolved.recording}'.",
                                   hint="A rosbag2 directory needs at least one readable .mcap segment. Nothing was prepared.")
        fp = _fingerprint(ctx)
        if exists:
            rec = _existing_incident(target, fp)
            return PrepareResult("already_prepared", target, rec, _plan(ctx, resolved, target, None, 0),
                                 ("an identical, verified incident already exists; nothing was written",))

        cfg = ctx.profile.profile.capture
        reserve = (cfg.disk_critical_free_bytes or 0) if cfg is not None else 0
        try:
            free: int | None = free_space(target.parent)
        except OSError:
            free = None
        plan = _plan(ctx, resolved, target, free, reserve)
        if dry_run:
            return PrepareResult("dry_run", None, None, plan)
        if free is not None and free < plan.required_bytes:
            raise InsufficientSpaceError(
                f"Not enough free space for a self-contained copy: {plan.copy_bytes} bytes to copy + {reserve} reserve + "
                f"{FIXED_OVERHEAD_BYTES} overhead = {plan.required_bytes} needed, {free} free under '{target.parent}'.",
                hint="Free space, or use --out on a larger disk. Nothing was copied.")
        assert lock is not None
        if governor is not None:
            governor.check_now()  # a capture may have started during the (uninterruptible) scan: do not begin copying
        _remove_stale_staging(target.parent, target.name, notes)
        staging = target.parent / f".{target.name}.incident-staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        os.mkdir(staging)
        try:
            progress(f"Copying and verifying {len(plan.files)} file(s), {plan.copy_bytes} bytes (self-contained copy) ...")
            export_package(ctx, staging / PACKAGE_DIR, now=now)
            if governor is not None:
                governor.check_now()  # last checkpoint before the (cheap) publish phase
            writers, _ = find_live_writers(resolved.recording)  # something may have opened the bag while we copied it
            if writers:
                raise _live_writer_deferral(writers, resolved.recording)
            stack.close()  # the heavy I/O is done: nothing below may be interrupted into a half-published state
            mbytes = (staging / PACKAGE_DIR / MANIFEST_NAME).read_bytes()
            manifest = Manifest.model_validate_json(mbytes)
            record = build_record(ctx, resolved, manifest, mbytes, fp, now, notes)
            progress("Writing incident record and summary ...")
            _write_new(staging / INCIDENT_FILE, _record_bytes(record))
            _write_new(staging / SUMMARY_FILE, render_incident(record).encode())
            _fsync_dir(staging)
            _publish(staging, target)
        except BaseException as original:
            stack.close()
            try:
                shutil.rmtree(staging)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                raise IncidentError(
                    f"Preparation failed ({original}) AND the staging directory '{staging}' could not be removed ({cleanup_error}). "
                    "It is incomplete and safe to delete.", code="cleanup_failed") from original
            if isinstance(original, OSError):
                code = "insufficient_space" if original.errno == errno.ENOSPC else "io_error"
                raise IncidentError(f"Preparation failed while writing: {original.strerror or original}. No incident was published "
                                    "and the staging directory was removed.", code=code, hint="Retry after fixing the cause.") from original
            raise
        return PrepareResult("prepared", target, record, plan, tuple(notes))
    finally:
        stack.close()
        if lock is not None:
            lock.release()


def _record_attempt(
    session_dir: Path, state: str, now: datetime, *, scheduled_by: str | None, code: str | None = None,
    message: str | None = None, hint: str | None = None, incident: str | None = None, guard_dirs: Sequence[str] = (),
) -> str | None:
    """Write `<session>/incident_attempt.json` atomically, carrying over the first-attempt time and the count.
    Best effort: returns an error text instead of raising (a bookkeeping failure must never mask the real result)."""
    prev = _read_attempt(session_dir)
    stamp = _utc_stamp(now)
    rec = AttemptRecord(
        state=state, attempted_at_utc=stamp,  # type: ignore[arg-type]
        first_attempted_at_utc=(prev.first_attempted_at_utc or prev.attempted_at_utc) if prev else stamp,
        attempt_count=(prev.attempt_count + 1) if prev else 1, scheduled_by=scheduled_by,  # type: ignore[arg-type]
        incident=incident, code=code, message=message, hint=hint, guard_dirs=[str(g) for g in guard_dirs],
        retry_command=f"physicore-collector incident prepare {session_dir}",
    )
    try:
        _write_atomic(session_dir / ATTEMPT_FILE, (rec.model_dump_json(indent=2) + "\n").encode())
    except OSError as e:
        return f"could not write {ATTEMPT_FILE}: {e.strerror or e}"
    return None


def prepare_incident(
    source: str | Path, profile: str | Path | None = None, out: str | Path | None = None, *,
    metadata: str | Path | None = None, trigger: str | Path | None = None, health: str | Path | None = None,
    dry_run: bool = False, now: datetime | None = None, free_space: FreeSpace | None = None,
    progress: Progress | None = None, guard_dirs: Sequence[str | Path] = (), respect_active_captures: bool = True,
    max_mib_per_sec: float | None = None, scheduled_by: str = "manual",
) -> PrepareResult:
    """Prepare an incident from `source`. Raises a PhysiCoreError (see `failure_info`) when no verified
    package could be produced; the source is never modified in any case.

    Scheduling: unless `respect_active_captures=False`, preparation raises `IncidentDeferredError` instead of
    competing with an active capture (before starting, and again while running; see the policy above).
    `max_mib_per_sec` (or $PHYSICORE_PREPARE_MAX_MIB_PER_SEC) throttles PhysiCore's own bulk I/O.

    `out=None` is only valid for a capture session directory (or its `bag/`): the incident is written to
    `<session>/incident` and the outcome (prepared / deferred / failed) is recorded in `<session>/incident_attempt.json`."""
    say = progress or (lambda _m: None)
    stamp = now or datetime.now(timezone.utc)
    resolved = resolve_source(source, health)
    prof = Path(profile) if profile is not None else resolved.profile_path
    if prof is None:
        raise InputError("No profile: an incident is judged against a robot profile.",
                         hint="Pass --profile. (A capture session directory carries its own copy, used automatically.)")
    default_out = out is None
    if default_out:
        if resolved.session_dir is None:
            raise InputError("--out is required unless the input is a capture session directory.",
                             hint="Example: --out ./incidents/run-12")
        target = resolved.session_dir / DEFAULT_INCIDENT_DIR
    else:
        target = Path(out)  # type: ignore[arg-type]
    track = default_out and resolved.session_dir is not None and not dry_run
    rate_mib = max_mib_per_sec if max_mib_per_sec is not None else env_throttle_mib()
    if rate_mib is not None and not rate_mib > 0:
        raise InputError("--max-mib-per-sec must be a positive number.")
    gdirs = [str(d) for d in collect_guard_dirs(
        target.parent, resolved.session_dir.parent if resolved.session_dir else None, extra=guard_dirs)]
    try:
        res = _prepare_resolved(resolved, prof, target, metadata=Path(metadata) if metadata else None,
                                trigger=Path(trigger) if trigger else None, dry_run=dry_run, now=stamp,
                                free_space=free_space or _default_free_space, progress=say, guard_dirs=guard_dirs,
                                respect_active=respect_active_captures,
                                max_bytes_per_sec=None if rate_mib is None else rate_mib * 1024 * 1024)
    except BaseException as e:
        if track:
            code, msg, hint = failure_info(e)
            if code not in ("preparation_in_progress", "capture_active"):  # not about THIS session's evidence
                _record_attempt(resolved.session_dir, "deferred" if code.startswith("deferred_") else "failed", stamp,  # type: ignore[arg-type]
                                scheduled_by=scheduled_by, code=code, message=msg, hint=hint, guard_dirs=gdirs)
        raise
    if track and res.state in ("prepared", "already_prepared"):
        err = _record_attempt(resolved.session_dir, "prepared", stamp, scheduled_by=scheduled_by,  # type: ignore[arg-type]
                              incident=DEFAULT_INCIDENT_DIR, guard_dirs=gdirs)
        if err:
            res = replace(res, notes=(*res.notes, err))
    return res


# --------------------------------------------------------------------------
# Automatic preparation after a capture
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AutoPrepareOutcome:
    state: Literal["prepared", "already_prepared", "failed", "deferred", "skipped"]
    message: str
    incident_dir: Path | None = None
    record: IncidentRecord | None = None
    code: str | None = None
    hint: str | None = None


def auto_prepare_after_capture(
    session_dir: str | Path, *, progress: Progress | None = None, free_space: FreeSpace | None = None,
    now: datetime | None = None, scheduled_by: str = "auto",
) -> AutoPrepareOutcome:
    """Prepare `<session>/incident` once a capture has stopped and its final record is written, unless that would
    compete with a recording (then it is DEFERRED and recorded as such).

    NEVER raises and never changes how the capture itself ended: any failure or deferral (including Ctrl-C/SIGTERM and
    unexpected bugs) is returned, recorded in `incident_attempt.json`, and leaves the recording and the capture-health
    record exactly as they were. Call it only after the supervisor returned (locks released)."""
    sdir = Path(session_dir)
    try:
        h = load_health(sdir / CAPTURE_HEALTH_NAME).health
        if h.state is CaptureState.FAILED_TO_START or not (sdir / CAPTURE_BAG_DIR).exists():
            return AutoPrepareOutcome("skipped", "no recording was produced, so no incident was prepared")
        with _terminable():
            res = prepare_incident(sdir, None, None, progress=progress, free_space=free_space, now=now, scheduled_by=scheduled_by)
        assert res.record is not None
        return AutoPrepareOutcome(res.state, f"{res.record.outcome} ({res.record.session_status.value})",  # type: ignore[arg-type]
                                  res.incident_dir, res.record)
    except BaseException as e:  # noqa: BLE001 - nothing may escape: the capture already ended and must stay as it ended
        if isinstance(e, (SystemExit, GeneratorExit)):
            raise
        code, msg, hint = failure_info(e)
        return AutoPrepareOutcome("deferred" if code.startswith("deferred_") else "failed", msg, None, None, code, hint)


def defer_session(
    session_dir: str | Path, *, code: str, message: str, hint: str | None = None, scheduled_by: str = "service",
    now: datetime | None = None,
) -> str | None:
    """Record that preparation of this session is deliberately NOT happening now (queue entry). Writes only
    `incident_attempt.json`. Never raises; returns an error text if the record could not be written."""
    sdir = Path(session_dir)
    try:
        return _record_attempt(sdir, "deferred", now or datetime.now(timezone.utc), scheduled_by=scheduled_by, code=code,
                               message=message, hint=hint)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Pending preparation: discovery and an explicit, idle-only drain
# --------------------------------------------------------------------------
#
# There is no daemon. The queue IS the set of capture sessions that have no incident: each carries its own durable
# `incident_attempt.json`, so pending work is discovered by scanning after any process restart or reboot. It is drained
# by `incident pending run`, which a person, cron, or a systemd timer invokes when the machine is idle.


@dataclass(frozen=True)
class PendingItem:
    session_dir: Path
    kind: Literal["deferred", "failed", "unprepared", "needs_reconcile", "unreadable"]
    capture_state: str | None  # sidecar state, e.g. stopped_clean | interrupted | running
    capture_assessment: str | None  # liveness: finalized | interrupted | failed ...
    recorder: str | None  # liveness: not_running | orphaned | ...
    attempt_count: int
    last_code: str | None
    detail: str
    blocked_by: str | None  # the session's own blocker: recorder_may_be_writing | capture_active | None
    capture_active: bool  # a capture is active in a guarded directory NOW: independent of blocked_by, and it gates everything
    retry_eligible: bool  # False: a retry cannot succeed until an operator acts (see `action`)
    action: str


def _pending_for_session(sdir: Path, active: list[ActiveCapture]) -> PendingItem | None:
    if (sdir / DEFAULT_INCIDENT_DIR / INCIDENT_FILE).is_file():
        return None
    try:
        h = load_health(sdir / CAPTURE_HEALTH_NAME).health
    except HealthError as e:
        return PendingItem(sdir, "unreadable", None, None, None, 0, "capture_health_unreadable", e.message, None, bool(active), False,
                           "Fix or replace the capture-health record, or run `incident prepare --health ...` by hand.")
    a = assess(h, sdir)
    if a.supervisor == "alive":
        return None  # an active capture, not pending work
    if h.state is CaptureState.FAILED_TO_START or not (sdir / CAPTURE_BAG_DIR).exists():
        return None  # nothing was recorded
    att = _read_attempt(sdir)
    stale = h.state in ACTIVE_STATES  # the record says "running" but no supervisor is attached (crash/power loss/reboot)
    kind = "needs_reconcile" if stale else (att.state if att and att.state in ("deferred", "failed") else "unprepared")
    blocked = None
    if a.recorder in ("running", "orphaned", "unknown") or find_live_writers(sdir / CAPTURE_BAG_DIR)[0]:
        blocked = "recorder_may_be_writing"
    elif active:
        blocked = "capture_active"
    eligible = not (att is not None and att.state == "failed" and att.code in NEEDS_ATTENTION_CODES)
    if blocked == "recorder_may_be_writing":
        action = _recorder_alive_hint(h, sdir) + " Then `incident pending run`."
    elif blocked == "capture_active":
        action = "wait until no capture is active, then `incident pending run`"
    elif not eligible:
        action = f"fix the cause ({att.code}: {att.message}), then `physicore-collector incident prepare {sdir}`"  # type: ignore[union-attr]
    elif stale:
        action = "`incident pending run` reconciles it with the existing recovery logic, then prepares it"
    else:
        action = "`incident pending run`"
    detail = (f"{att.state}: {att.code}: {att.message}" if att and att.message else
              (f"capture {h.state.value}; no incident prepared yet"))
    return PendingItem(sdir, kind, h.state.value, a.capture, a.recorder, att.attempt_count if att else 0,  # type: ignore[arg-type]
                       att.code if att else None, detail, blocked, bool(active), eligible, action)


def list_pending(out_dirs: Sequence[str | Path], *, guard_dirs: Sequence[str | Path] = ()) -> list[PendingItem]:
    """Capture sessions under `out_dirs` that still need an incident, with whether each can proceed NOW and, if not, why.
    Read-only: never writes, signals, recovers or hashes anything."""
    roots = [Path(d) for d in out_dirs]
    for r in roots:
        if not r.is_dir():
            raise InputError(f"Not a directory: '{r}'", hint="Pass the capture output directory (the one holding the session directories).")
    active = find_active_captures(collect_guard_dirs(*roots, extra=guard_dirs))
    items: list[PendingItem] = []
    for r in roots:
        for sdir in list_sessions(r):
            it = _pending_for_session(sdir, active)
            if it is not None:
                items.append(it)
    return items


@dataclass(frozen=True)
class PendingRunResult:
    session_dir: Path
    action: Literal["prepared", "already_prepared", "deferred", "failed", "skipped"]
    detail: str
    code: str | None = None
    outcome: str | None = None  # success | degraded, when an incident exists
    reconciled: str | None = None  # the recovery outcome, when one was needed


def run_pending(
    out_dirs: Sequence[str | Path], *, guard_dirs: Sequence[str | Path] = (), recover: bool = True, stop_orphan: bool = False,
    max_items: int | None = None, max_mib_per_sec: float | None = None, progress: Progress | None = None,
    now: datetime | None = None,
) -> list[PendingRunResult]:
    """Drain pending preparation, oldest session first, ONLY while no capture is active in the guarded directories.

    Per session: a stale `running` record is first reconciled with the existing `recover_session` (idempotent; only
    with `recover=True`); a recorder that may still be writing blocks the session (it is stopped only with
    `stop_orphan=True`, and only through recovery's identity-verified path); then the incident is prepared with the
    same scheduling rules as everything else, so a capture that starts meanwhile makes the current session yield
    (state `deferred`) and the run stops. Failures never stop the run; they are recorded and the next session continues."""
    say = progress or (lambda _m: None)
    items = list_pending(out_dirs, guard_dirs=guard_dirs)
    results: list[PendingRunResult] = []
    rate = max_mib_per_sec if max_mib_per_sec is not None else env_throttle_mib()
    done = 0
    stop_all: str | None = None
    stop_code: str | None = None
    for it in items:
        if stop_all is not None:
            results.append(PendingRunResult(it.session_dir, "skipped", stop_all, stop_code))
            continue
        if max_items is not None and done >= max_items:
            results.append(PendingRunResult(it.session_dir, "skipped", f"--max {max_items} reached", "max_items"))
            continue
        if it.capture_active:  # before ANY reconcile/hash/signal/copy: those all compete with the recording
            stop_all, stop_code = ("a capture is active; nothing is reconciled, stopped or prepared while it records",
                                   "deferred_capture_active")
            results.append(PendingRunResult(it.session_dir, "deferred", stop_all, stop_code))
            continue
        if not it.retry_eligible:
            results.append(PendingRunResult(it.session_dir, "skipped", f"needs attention: {it.action}", it.last_code))
            continue
        reconciled: str | None = None
        try:
            if it.kind == "needs_reconcile" or it.blocked_by == "recorder_may_be_writing":
                if not recover:
                    results.append(PendingRunResult(it.session_dir, "skipped",
                                                    "stale session; re-run without --no-recover to reconcile it", "deferred_stale_session"))
                    continue
                say(f"Reconciling '{it.session_dir.name}' (existing `capture recover` logic) ...")
                guard = collect_guard_dirs(it.session_dir.parent, extra=guard_dirs)
                gov = IoGovernor(guard, out_hint="physicore-collector incident pending run") if guard else None
                with (checksums.io_hook(gov) if gov else contextlib.nullcontext()), _terminable():
                    rr = recover_session(it.session_dir, stop_orphan=stop_orphan)
                reconciled = rr.outcome
                if rr.outcome in ("orphan_left_running", "supervisor_alive", "starting", "error"):
                    code = "deferred_recorder_alive" if rr.outcome == "orphan_left_running" else "deferred_stale_session"
                    defer_session(it.session_dir, code=code, message=rr.detail, hint=it.action, scheduled_by="pending_run", now=now)
                    results.append(PendingRunResult(it.session_dir, "deferred", rr.detail, code, None, reconciled))
                    continue
            say(f"Preparing '{it.session_dir.name}' ...")
            with _terminable():
                res = prepare_incident(it.session_dir, None, None, progress=progress, now=now, guard_dirs=guard_dirs,
                                       max_mib_per_sec=rate, scheduled_by="pending_run")
            assert res.record is not None
            done += 1
            results.append(PendingRunResult(it.session_dir, res.state, f"{res.record.outcome} ({res.record.session_status.value})",  # type: ignore[arg-type]
                                            None, res.record.outcome, reconciled))
        except BaseException as e:  # noqa: BLE001
            if isinstance(e, (SystemExit, GeneratorExit)):
                raise
            code, msg, _hint = failure_info(e)
            if code.startswith("deferred_"):
                # recover_session's own deferrals are recorded above; prepare_incident records its own. Stop: a capture is active.
                if code == "deferred_capture_active":
                    stop_all, stop_code = "a capture became active; stopped without competing with it", code
                results.append(PendingRunResult(it.session_dir, "deferred", msg, code, None, reconciled))
            else:
                if it.kind == "needs_reconcile" and reconciled is None:  # failed before/while reconciling: still record it
                    _record_attempt(it.session_dir, "failed", now or datetime.now(timezone.utc), scheduled_by="pending_run",
                                    code=code, message=msg)
                results.append(PendingRunResult(it.session_dir, "failed", msg, code, None, reconciled))
            if isinstance(e, KeyboardInterrupt):
                stop_all, stop_code = "interrupted", "interrupted"
    return results


def register_unprepared(out_dir: str | Path, *, scheduled_by: str = "service", now: datetime | None = None) -> list[Path]:
    """Give every finished session in `out_dir` that has neither an incident nor an attempt record a durable
    `deferred` record, so pending work is visible in `incident list`/`pending list`. Stat-only: never hashes, recovers,
    signals or prepares anything, so it is safe to call right before a recording starts."""
    marked: list[Path] = []
    for sdir in list_sessions(out_dir):
        if (sdir / DEFAULT_INCIDENT_DIR / INCIDENT_FILE).is_file() or _read_attempt(sdir) is not None:
            continue
        try:
            h = load_health(sdir / CAPTURE_HEALTH_NAME).health
        except HealthError:
            continue
        if h.state is CaptureState.FAILED_TO_START or not (sdir / CAPTURE_BAG_DIR).exists() or h.state in ACTIVE_STATES:
            continue
        if assess(h, sdir).supervisor == "alive":
            continue
        if defer_session(sdir, code="deferred_stale_session", scheduled_by=scheduled_by, now=now,
                         message="recorded at service start: this finished session has no incident yet; preparing it now would "
                                 "delay or compete with the next recording",
                         hint=f"run `physicore-collector incident pending run {Path(out_dir)}` when no capture is active") is None:
            marked.append(sdir)
    return marked
