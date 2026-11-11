"""Pydantic schemas: robot profile, trigger, validation report, manifest.

Design principles encoded here (not just documented):

* Observed evidence and unknowns are kept separate.
* `LossDetectionCoverage.no_loss_claim_permitted` is always False. The tool
  can never assert "no data was lost" from an MCAP file alone.
* `Manifest.known_gaps` must be non-empty for the same reason.
* A cadence violation is *timing evidence*; it is never labelled as loss.
* A `complete` session cannot be constructed if the report contains a
  required check that did not pass, or if the artifact was not fully readable.
* All timestamps are integer nanoseconds. No floats for time values stored
  in outputs.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from .versions import (
    MANIFEST_SCHEMA_VERSION,
    PROFILE_VERSION_WITH_CAPTURE,
    PROFILE_VERSION_WITH_MONITORING,
    SUPPORTED_MANIFEST_SCHEMA_VERSIONS,
    SUPPORTED_PROFILE_SCHEMA_VERSIONS,
    SUPPORTED_REPORT_SCHEMA_VERSIONS,
    TOOL_NAME,
    TOOL_VERSION,
    VALIDATION_REPORT_SCHEMA_VERSION,
)

SHA256_PATTERN = r"^[0-9a-f]{64}$"

CADENCE_INTERPRETATION = (
    "Observed gap between consecutive log_time values. This is timing/cadence "
    "evidence only. It is not proof of DDS message loss; causes may include "
    "publisher stalls, recorder back-pressure, or a genuinely slow source."
)


class _Strict(BaseModel):
    """Reject unknown fields so typos in profiles/triggers are caught."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class TimeBase(str, Enum):
    """Label for what a timestamp's clock represents.

    A label alone NEVER makes two timestamps comparable (two `sim_time`
    clocks from different simulators are not the same clock). See
    `BasisCompatibility` for the explicit assertion that is also required.
    """

    UNKNOWN = "unknown"
    ROS_TIME = "ros_time"
    SYSTEM_TIME = "system_time"
    SIM_TIME = "sim_time"
    UTC_WALL_CLOCK = "utc_wall_clock"
    MONOTONIC = "monotonic"
    OTHER = "other"


class SessionStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ArtifactReadability(str, Enum):
    READABLE = "readable"
    PARTIALLY_READABLE = "partially_readable"  # e.g. truncated; some data recovered
    UNREADABLE = "unreadable"


class CheckOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"  # could not be determined
    NOT_APPLICABLE = "not_applicable"


class CheckCategory(str, Enum):
    READABILITY = "readability"
    REQUIRED_TOPIC = "required_topic"
    METADATA = "metadata"
    CADENCE = "cadence"
    TRIGGER_TIMEBASE = "trigger_timebase"
    BAG_STRUCTURE = "bag_structure"  # Day 2: rosbag2 directory vs. files on disk
    CAPTURE = "capture"  # Day 2: capture-health sidecar
    OTHER = "other"


class TriggerComparisonResult(str, Enum):
    UNAVAILABLE = "unavailable"  # no trigger, or trigger basis unknown
    INCONCLUSIVE = "inconclusive"  # basis known but compatibility not established
    WITHIN_RECORDING_WINDOW = "within_recording_window"
    BEFORE_RECORDING_WINDOW = "before_recording_window"
    AFTER_RECORDING_WINDOW = "after_recording_window"


class ArtifactRole(str, Enum):
    RECORDING = "recording"
    PROFILE = "profile"
    METADATA = "metadata"
    TRIGGER = "trigger"
    VALIDATION_REPORT = "validation_report"
    BAG_METADATA = "bag_metadata"  # Day 2: the rosbag2 metadata.yaml, byte-identical
    CAPTURE_HEALTH = "capture_health"  # Day 2: capture-health sidecar, byte-identical


# --------------------------------------------------------------------------
# Robot profile
# --------------------------------------------------------------------------


class TopicRequirement(_Strict):
    """Per-topic expectations. Cadence rules are optional.

    Effective cadence threshold (see `effective_max_gap_seconds`):
      1. `max_gap_seconds` if given (explicit wins), else
      2. `gap_factor / expected_hz` if `expected_hz` given, else
      3. None (no cadence rule for this topic).
    """

    name: str = Field(min_length=1)
    required: bool = True
    expected_hz: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_gap_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    gap_factor: float = Field(default=2.0, gt=1.0, allow_inf_nan=False)

    @field_validator("name")
    @classmethod
    def _no_whitespace(cls, v: str) -> str:
        if any(ch.isspace() for ch in v):
            raise ValueError("topic name must not contain whitespace")
        return v

    @property
    def effective_max_gap_seconds(self) -> float | None:
        if self.max_gap_seconds is not None:
            return self.max_gap_seconds
        if self.expected_hz is not None:
            return self.gap_factor / self.expected_hz
        return None


class CaptureIdentity(_Strict):
    """Optional labels for the run. Copied into the capture-health sidecar as supplied;
    never interpreted or verified by this tool."""

    robot_id: str | None = None
    run_id: str | None = None
    test_id: str | None = None
    incident_id: str | None = None


# Capture settings introduced by profile_schema_version "1.2", with their defaults.
CAPTURE_V12_DEFAULTS: dict = {"heartbeat_seconds": 5.0, "disk_warn_free_bytes": None, "disk_critical_free_bytes": None}


class CaptureConfig(_Strict):
    """How `capture run` asks rosbag2 to record. Deliberately small.

    Everything here maps one-to-one onto a documented `ros2 bag record` option; PhysiCore does
    not record messages itself. Requires profile_schema_version "1.1".
    """

    storage_id: Literal["mcap"] = "mcap"  # the only storage format the Collector can read
    topics: list[str] | None = None  # None -> every topic named in the profile
    max_bag_size_bytes: int | None = Field(default=None, gt=0)  # ros2 bag record --max-bag-size
    max_bag_duration_seconds: int | None = Field(default=None, gt=0)  # --max-bag-duration
    stop_timeout_seconds: float = Field(default=15.0, gt=0, le=3600, allow_inf_nan=False)
    min_free_disk_bytes: int | None = Field(default=None, gt=0)  # refuse to start below this
    identity: CaptureIdentity = Field(default_factory=CaptureIdentity)
    # ---- profile_schema_version "1.2" (Day 3) ----
    heartbeat_seconds: float = Field(default=5.0, ge=0.5, le=300, allow_inf_nan=False)
    # Free space on the filesystem holding the session. Below `warn`: the capture is reported
    # degraded. Below `critical`: the supervisor stops the recorder gracefully (never deletes
    # anything) and the session is marked as ended early. Free space does NOT guarantee write
    # throughput: a slow disk can still make rosbag2 drop messages before any threshold is hit.
    disk_warn_free_bytes: int | None = Field(default=None, gt=0)
    disk_critical_free_bytes: int | None = Field(default=None, gt=0)

    # The 1.2-only fields are omitted from the serialized form while they hold their defaults, so a
    # profile that does not use them serializes exactly as it did under 1.1 (Day 2 readers keep
    # working on manifests that embed such profiles) and round-trips without being "newly set".
    @model_serializer(mode="wrap")
    def _omit_default_v12_fields(self, handler):
        data = handler(self)
        for key, default in CAPTURE_V12_DEFAULTS.items():
            if key in data and data[key] == default:
                del data[key]
        return data

    def uses_v12_fields(self) -> bool:
        return any(getattr(self, k) != d for k, d in CAPTURE_V12_DEFAULTS.items())

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "CaptureConfig":
        w, c = self.disk_warn_free_bytes, self.disk_critical_free_bytes
        if w is not None and c is not None and w <= c:
            raise ValueError("capture.disk_warn_free_bytes must be greater than capture.disk_critical_free_bytes")
        return self

    @field_validator("topics")
    @classmethod
    def _topics_ok(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("capture.topics must not be empty (omit it to use the profile topics)")
        for name in v:
            if not name or any(ch.isspace() for ch in name):
                raise ValueError(f"invalid topic name {name!r}: must be non-empty without whitespace")
            if not name.startswith("/"):
                raise ValueError(f"topic {name!r} must be a fully qualified name starting with '/'")
        dupes = sorted({n for n in v if v.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate capture topics: {dupes}")
        return v


class RobotProfile(_Strict):
    profile_schema_version: str
    profile_id: str = Field(min_length=1)
    robot_name: str | None = None
    # What the profile asserts MCAP `log_time` represents for recordings from
    # this robot. MCAP itself does not say; the recorder decides.
    log_time_basis: TimeBase = TimeBase.UNKNOWN
    required_metadata_keys: list[str] = Field(default_factory=list)
    topics: list[TopicRequirement] = Field(min_length=1)
    capture: CaptureConfig | None = None  # Day 2; only valid with profile_schema_version "1.1"

    @field_validator("profile_schema_version")
    @classmethod
    def _supported_version(cls, v: str) -> str:
        if v not in SUPPORTED_PROFILE_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported profile_schema_version {v!r}; "
                f"supported: {list(SUPPORTED_PROFILE_SCHEMA_VERSIONS)}"
            )
        return v

    @model_validator(mode="after")
    def _unique_topics(self) -> "RobotProfile":
        names = [t.name for t in self.topics]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate topic entries in profile: {dupes}")
        if self.capture is not None and self.profile_schema_version not in (
            PROFILE_VERSION_WITH_CAPTURE, PROFILE_VERSION_WITH_MONITORING
        ):
            raise ValueError(
                f"the 'capture' section requires profile_schema_version "
                f"{PROFILE_VERSION_WITH_CAPTURE!r} (this profile declares "
                f"{self.profile_schema_version!r})"
            )
        if self.capture is not None and self.profile_schema_version != PROFILE_VERSION_WITH_MONITORING:
            new = sorted(k for k, d in CAPTURE_V12_DEFAULTS.items() if getattr(self.capture, k) != d)
            if new:
                raise ValueError(
                    f"capture.{', capture.'.join(new)} require profile_schema_version "
                    f"{PROFILE_VERSION_WITH_MONITORING!r} (this profile declares {self.profile_schema_version!r})"
                )
        return self


# --------------------------------------------------------------------------
# Trigger
# --------------------------------------------------------------------------


class BasisCompatibility(_Strict):
    """Explicit, human-attested statement that the trigger clock and the
    recording's log_time clock are the same clock domain.

    Matching enum labels are necessary but NOT sufficient: this assertion
    must also be present, with a stated source.
    """

    compatible_with_log_time: bool
    established_by: str = Field(
        min_length=1,
        description="How compatibility was established, e.g. 'both stamped by recorder host clock'.",
    )


class Trigger(_Strict):
    trigger_id: str = Field(min_length=1)
    timestamp_ns: int = Field(ge=0)
    timestamp_basis: TimeBase
    basis_compatibility: BasisCompatibility | None = None
    note: str | None = None


class TriggerComparison(_Strict):
    result: TriggerComparisonResult
    reason: str = Field(min_length=1)


# --------------------------------------------------------------------------
# Inspection / validation
# --------------------------------------------------------------------------


class TopicInfo(_Strict):
    channel_id: int
    topic: str
    schema_name: str | None = None
    message_encoding: str | None = None
    message_count: int = Field(ge=0)
    first_log_time_ns: int | None = None
    last_log_time_ns: int | None = None


class MetadataRecord(_Strict):
    """An MCAP Metadata record found inside the recording."""

    name: str
    values: dict[str, str]


class IntegrityInfo(_Strict):
    """What file-framing evidence was observed. All fields are observations."""

    start_magic_valid: bool
    header_present: bool
    data_end_present: bool
    footer_present: bool
    # True only if the footer was read, the closing magic matched, and no bytes follow it.
    end_magic_valid: bool
    trailing_bytes: int = Field(ge=0)
    statistics_present: bool
    statistics_message_count: int | None = None


class RecordingWindow(_Strict):
    min_log_time_ns: int
    max_log_time_ns: int

    @model_validator(mode="after")
    def _ordered(self) -> "RecordingWindow":
        if self.max_log_time_ns < self.min_log_time_ns:
            raise ValueError("max_log_time_ns must be >= min_log_time_ns")
        return self


class InspectionResult(_Strict):
    """Result of scanning one MCAP file. Contains no absolute paths."""

    file_name: str
    size_bytes: int = Field(ge=0)
    readability: ArtifactReadability
    errors: list[str] = Field(default_factory=list)
    mcap_profile: str | None = None
    mcap_library: str | None = None
    total_message_count: int = Field(ge=0)
    recording_window: RecordingWindow | None = None
    topics: list[TopicInfo] = Field(default_factory=list)
    metadata_records: list[MetadataRecord] = Field(default_factory=list)
    integrity: IntegrityInfo
    not_verified: list[str] = Field(default_factory=list)


class CheckResult(_Strict):
    check_id: str = Field(min_length=1)
    category: CheckCategory
    outcome: CheckOutcome
    required: bool
    message: str = Field(min_length=1)
    observed: dict[str, Any] = Field(default_factory=dict)  # what was seen
    unknowns: list[str] = Field(default_factory=list)  # what could not be determined


class CadenceFinding(_Strict):
    topic: str
    threshold_seconds: float = Field(gt=0)
    observed_gap_seconds: float = Field(ge=0)
    gap_start_log_time_ns: int
    gap_end_log_time_ns: int
    kind: Literal["observed_log_time_gap"] = "observed_log_time_gap"
    interpretation: str = CADENCE_INTERPRETATION


class ValidationReport(_Strict):
    report_schema_version: str = VALIDATION_REPORT_SCHEMA_VERSION
    status: SessionStatus
    status_reasons: list[str] = Field(min_length=1)
    artifact_readability: ArtifactReadability
    checks: list[CheckResult] = Field(default_factory=list)
    cadence_findings: list[CadenceFinding] = Field(default_factory=list)
    trigger_comparison: TriggerComparison | None = None
    read_errors: list[str] = Field(default_factory=list)

    @field_validator("report_schema_version")
    @classmethod
    def _known_report_version(cls, v: str) -> str:
        if v not in SUPPORTED_REPORT_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported report_schema_version {v!r}; supported: {list(SUPPORTED_REPORT_SCHEMA_VERSIONS)}")
        return v

    @model_validator(mode="after")
    def _complete_is_earned(self) -> "ValidationReport":
        """Defensive guard: 'complete' can never be represented if evidence
        contradicts it, even if a caller computes status incorrectly."""
        if self.status is SessionStatus.COMPLETE:
            if self.artifact_readability is not ArtifactReadability.READABLE:
                raise ValueError("status 'complete' requires a fully readable artifact")
            if not self.checks:
                raise ValueError("status 'complete' requires at least one executed check")
            bad = [
                c.check_id
                for c in self.checks
                if c.required
                and c.outcome not in (CheckOutcome.PASS, CheckOutcome.NOT_APPLICABLE)
            ]
            if bad:
                raise ValueError(
                    f"status 'complete' is invalid: required checks did not pass: {bad}"
                )
        return self



# --------------------------------------------------------------------------
# rosbag2 bag directory (Day 2)
# --------------------------------------------------------------------------


class SegmentState(str, Enum):
    """What was established about one storage file of a bag. Never 'complete'-sounding:
    READABLE only means the MCAP framing/CRC checks of that single file passed."""

    READABLE = "readable"
    PARTIALLY_READABLE = "partially_readable"
    UNREADABLE = "unreadable"
    MISSING = "missing"  # referenced by metadata.yaml, not on disk
    NOT_A_FILE = "not_a_file"  # referenced path is a directory or other non-regular file
    UNSAFE_PATH = "unsafe_path"  # absolute path, '..', or a symlink leaving the bag directory
    UNSUPPORTED = "unsupported"  # storage/compression this tool cannot read


class SegmentInfo(_Strict):
    relative_path: str
    # "metadata": listed in metadata.yaml. "discovered": found on disk because metadata.yaml was
    # missing or invalid. "metadata_files_only": listed in metadata `files[]` but not in
    # `relative_file_paths` (a conflict, still inspected so nothing is silently dropped).
    source: Literal["metadata", "discovered", "metadata_files_only"]
    state: SegmentState
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    observed_message_count: int | None = Field(default=None, ge=0)
    metadata_message_count: int | None = Field(default=None, ge=0)
    first_log_time_ns: int | None = None
    last_log_time_ns: int | None = None
    errors: list[str] = Field(default_factory=list)


class BagMetadataSummary(_Strict):
    """What metadata.yaml *claims*. It is written by the recorder when it closes; agreement with
    the files shows consistency, not completeness."""

    metadata_version: int
    storage_identifier: str
    compression_format: str | None = None
    compression_mode: str | None = None
    message_count: int = Field(ge=0)
    ros_distro: str | None = None
    # rosbag2's own start time / duration. The clock is not asserted to equal MCAP log_time and
    # is deliberately never compared with it.
    starting_time_ns: int | None = Field(default=None, ge=0)
    duration_ns: int | None = Field(default=None, ge=0)
    relative_file_paths: list[str] = Field(default_factory=list)
    topic_message_counts: dict[str, int] = Field(default_factory=dict)
    custom_data: dict[str, str] = Field(default_factory=dict)


class UnaccountedEntry(_Strict):
    name: str
    kind: Literal["file", "directory", "other"]
    size_bytes: int | None = Field(default=None, ge=0)
    looks_like_storage: bool = False  # e.g. a *.mcap / *.db3 file that metadata.yaml does not list


class BagInfo(_Strict):
    """Everything the Collector established about a rosbag2 directory's structure."""

    bag_directory_name: str
    metadata_present: bool
    metadata_valid: bool
    metadata_errors: list[str] = Field(default_factory=list)
    metadata: BagMetadataSummary | None = None
    # "metadata": order taken from metadata.yaml. "inferred_from_observed_log_time": there was no
    # usable metadata, so segments are ordered by first observed log_time then file name.
    segment_order_basis: Literal["metadata", "inferred_from_observed_log_time"]
    segments: list[SegmentInfo] = Field(default_factory=list)
    unaccounted: list[UnaccountedEntry] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class CaptureHealthSummary(_Strict):
    """Summary of the capture-health sidecar embedded in the manifest (the full sidecar is also
    shipped, byte-identical, as an artifact)."""

    session_id: str
    health_schema_version: str
    state: str
    appears_finalized: bool | None  # None = unknown; never collapsed into False
    sidecar_sha256: str = Field(pattern=SHA256_PATTERN)

# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


class ToolInfo(_Strict):
    name: str = TOOL_NAME
    version: str = TOOL_VERSION


def relative_path_problem(v: str) -> str | None:
    """Why `v` is not a safe package-relative path, or None if it is. One rule for POSIX AND Windows, so a manifest written
    on one platform cannot escape its package root when read on the other.

    A safe path is `/`-separated, non-empty, relative, and made of components that are each non-empty, are not made only
    of dots/spaces (`.`, `..`, `...`, `.. ` all mean "current/parent directory" on Windows) and do not start with a drive
    letter (`C:`). Backslashes and control characters (including NUL) are refused outright: `\\` is a separator on Windows,
    so `a\\..\\b` is a traversal there even though POSIX sees one odd file name. Not checked: Windows reserved device
    names (CON, NUL, ...) and NTFS alternate data streams (`name:stream`); neither can leave the package root."""
    if not v:
        return "is empty"
    if any(ord(c) < 32 or c == "\x7f" for c in v):
        return "contains a control character (including NUL)"
    if "\\" in v:
        return "contains a backslash (a path separator on Windows)"
    if v.startswith("/"):
        return "is absolute"
    for part in v.split("/"):
        if part == "":
            return "has an empty component (leading, trailing or doubled '/')"
        if re.fullmatch(r"[ .]+", part):
            return f"has component {part!r}, which is '.', '..' or only dots/spaces (current/parent directory on Windows)"
        if re.match(r"[A-Za-z]:", part):
            return f"has drive-qualified component {part!r}"
    return None


class ArtifactEntry(_Strict):
    relative_path: str = Field(min_length=1)
    role: ArtifactRole
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("relative_path")
    @classmethod
    def _safe_relative(cls, v: str) -> str:
        why = relative_path_problem(v)
        if why is not None:
            raise ValueError(f"artifact path must be relative and must not contain '..' or other unsafe components: path {why}")
        return v


class KnownGap(_Strict):
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)


class LossDetectionCoverage(_Strict):
    """What this tool did and did not examine regarding data loss.

    `no_loss_claim_permitted` is pinned to False: absence of detected gaps is
    never evidence that nothing was lost.
    """

    no_loss_claim_permitted: Literal[False] = False
    sequence_counter_analysis: Literal["not_performed"] = "not_performed"
    dds_level_loss_detection: Literal["not_performed"] = "not_performed"
    cadence_checked_topics: list[str] = Field(default_factory=list)
    topics_without_cadence_rules: list[str] = Field(default_factory=list)
    statement: str = Field(min_length=1)


class Manifest(_Strict):
    manifest_schema_version: str = MANIFEST_SCHEMA_VERSION
    tool: ToolInfo = Field(default_factory=ToolInfo)
    # Optional so tests/exports can be fully deterministic; set by export.
    created_at_utc: str | None = None
    session_status: SessionStatus
    profile: RobotProfile
    profile_sha256: str = Field(pattern=SHA256_PATTERN)
    # The manifest cannot list its own checksum; it lists every other artifact.
    artifacts: list[ArtifactEntry] = Field(min_length=1)
    recording_window: RecordingWindow | None = None
    topics: list[TopicInfo] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trigger: Trigger | None = None
    validation: ValidationReport
    known_gaps: list[KnownGap] = Field(min_length=1)
    loss_detection_coverage: LossDetectionCoverage
    # Day 2 (manifest_schema_version "0.2"); absent from single-MCAP packages.
    recording_kind: Literal["mcap_file", "rosbag2_directory"] | None = None
    bag: BagInfo | None = None
    capture_health: CaptureHealthSummary | None = None

    @field_validator("manifest_schema_version")
    @classmethod
    def _known_manifest_version(cls, v: str) -> str:
        if v not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported manifest_schema_version {v!r}; supported: {list(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)}"
            )
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "Manifest":
        if self.manifest_schema_version == "0.1" and (
            self.recording_kind is not None or self.bag is not None or self.capture_health is not None
        ):
            raise ValueError("manifest_schema_version '0.1' cannot carry Day 2 sections (recording_kind/bag/capture_health)")
        if self.session_status is not self.validation.status:
            raise ValueError(
                "manifest session_status must equal validation report status "
                f"({self.session_status.value} != {self.validation.status.value})"
            )
        paths = [a.relative_path for a in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate artifact paths in manifest")
        return self
