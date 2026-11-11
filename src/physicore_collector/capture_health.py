"""Capture-health sidecar: a versioned, machine-readable record of what happened while a
recording was made, plus the validation checks derived from it.

RULES (enforced by schema and tests):
  * Only observable facts are recorded. rosbag2 exposes no packet-loss counters, sensor-health
    measurements or delivery guarantees, so none exist here: `recorder_statistics` is explicitly
    unavailable and every unknown stays null/"unknown" - never False, never 0.
  * `bag_finalization.appears_finalized` is a tri-state: True only after a clean stop AND an
    inspected, structurally consistent bag; False when something known contradicts it; None
    (unknown) otherwise.
  * Timestamps are the supervisor host's system clock. They are explicitly NOT asserted to be
    comparable with MCAP log_time (`clock_compatible_with_log_time: "not_established"`).
  * Nothing sensitive is written: the environment is an allow-list of two names, the command is
    only the argument vector this tool built.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .bag import BagAnalysis
from .checksums import FileTooLargeError, read_and_hash
from .errors import HealthError
from .issues import format_issue, json_no_duplicates
from .schemas import (
    SHA256_PATTERN, ArtifactReadability, CaptureHealthSummary, CaptureIdentity, CheckCategory,
    CheckOutcome, CheckResult, _Strict,
)
from .versions import CAPTURE_HEALTH_SCHEMA_VERSION, SUPPORTED_CAPTURE_HEALTH_SCHEMA_VERSIONS

MAX_HEALTH_BYTES = 4 * 1024 * 1024
ENV_ALLOWLIST = ("ROS_DISTRO", "RMW_IMPLEMENTATION")
STATS_UNAVAILABLE_REASON = (
    "the rosbag2 command-line recorder exposes no machine-readable statistics, and writes no drop counters "
    "to metadata.yaml. It prints a human-readable warning when its cache drops messages; any such lines "
    "seen are kept verbatim in recorder.reported_loss_lines. Their absence does NOT show that nothing was lost"
)


class CaptureState(str, Enum):
    STARTING = "starting"  # session created by `capture start`; the detached supervisor has not taken over yet
    RUNNING = "running"  # written while recording; if it is the FINAL state the supervisor died
    STOPPED_CLEAN = "stopped_clean"  # graceful stop requested, recorder exited with status 0
    STOPPED_WITH_ERROR = "stopped_with_error"  # graceful stop requested, recorder exited non-zero
    EXITED_UNEXPECTEDLY = "exited_unexpectedly"  # recorder ended before any stop was requested
    STOP_TIMEOUT_KILLED = "stop_timeout_killed"  # did not stop in time and was force-killed
    FAILED_TO_START = "failed_to_start"  # recorder never started
    SUPERVISOR_ERROR = "supervisor_error"  # a bug/unexpected exception in the supervisor itself
    # Day 3
    STOPPED_DISK_CRITICAL = "stopped_disk_critical"  # supervisor stopped the recorder gracefully: free space < critical
    INTERRUPTED = "interrupted"  # set only by `capture recover`: the supervisor vanished without recording an outcome


CLEAN_STATES = frozenset({CaptureState.STOPPED_CLEAN})
# States after which the BAG may legitimately be judged finalized (the recorder was stopped
# gracefully). Only STOPPED_CLEAN lets the *capture* be judged complete: a disk-critical stop
# ends the recording earlier than intended.
FINALIZABLE_STATES = frozenset({CaptureState.STOPPED_CLEAN, CaptureState.STOPPED_DISK_CRITICAL})
ACTIVE_STATES = frozenset({CaptureState.STARTING, CaptureState.RUNNING})
EventKind = Literal[
    "capture_started", "recorder_started", "start_failed", "stop_requested", "recorder_exited",
    "unexpected_exit", "stop_timeout", "recorder_killed", "output_inspected", "warning", "error",
    # Day 3
    "supervisor_adopted", "disk_warning", "disk_critical", "reconciled", "orphan_recorder_stopped",
]


class CaptureEvent(_Strict):
    seq: int = Field(ge=0)
    kind: EventKind
    wall_clock_unix_ns: int = Field(ge=0)
    monotonic_ns: int = Field(ge=0)
    detail: str | None = None


class ProcessIdentityRecord(_Strict):
    """(pid, start time in clock ticks since boot, boot id) from /proc. See procident.py: a bare
    pid is NOT an identity. Start-time ticks are only meaningful together with the boot id."""

    pid: int
    starttime_ticks: int
    boot_id: str


class SupervisorInfo(_Strict):
    pid: int
    tool_name: str
    tool_version: str
    platform: str
    identity: ProcessIdentityRecord | None = None  # None: not recorded (1.0 sidecar, or no /proc)
    launch_mode: Literal["foreground", "detached", "service"] | None = None  # None: unknown (1.0)
    service_name: str | None = None  # the systemd unit name when launch_mode == "service"


class RequestedCapture(_Strict):
    profile_id: str
    profile_sha256: str = Field(pattern=SHA256_PATTERN)
    storage_id: str
    topics: list[str]
    max_bag_size_bytes: int | None = None
    max_bag_duration_seconds: int | None = None
    stop_timeout_seconds: float
    min_free_disk_bytes: int | None = None
    planned_duration_seconds: float | None = None
    identity: CaptureIdentity = Field(default_factory=CaptureIdentity)
    heartbeat_seconds: float | None = None  # 1.1; None in a 1.0 sidecar
    disk_warn_free_bytes: int | None = None
    disk_critical_free_bytes: int | None = None


class RecorderRecord(_Strict):
    adapter: str
    command: list[str] = Field(default_factory=list)  # argument vector built by this tool only
    pid: int | None = None
    exit_code: int | None = None  # None = unknown / not exited
    terminated_by_signal: int | None = None
    identity: ProcessIdentityRecord | None = None  # of the recorder process, taken right after start
    stderr_tail: list[str] = Field(default_factory=list)  # last lines of recorder output, bounded
    # Verbatim recorder output lines matching rosbag2's own loss/unflushed warnings. Not parsed.
    reported_loss_lines: list[str] = Field(default_factory=list)


class CaptureTiming(_Strict):
    wall_clock_basis: Literal["supervisor_host_system_clock"] = "supervisor_host_system_clock"
    clock_compatible_with_log_time: Literal["not_established"] = "not_established"
    start_unix_ns: int | None = None
    stop_requested_unix_ns: int | None = None
    end_unix_ns: int | None = None
    monotonic_duration_seconds: float | None = None
    # 1.1. All *_unix_ns are the supervisor host's UTC system clock (wall clock), never ROS time,
    # simulation time, bag log_time or message header stamps. Durations are monotonic.
    supervisor_started_unix_ns: int | None = None
    recorder_started_unix_ns: int | None = None
    last_heartbeat_unix_ns: int | None = None  # refreshed every `heartbeat_seconds` while running
    last_state_update_unix_ns: int | None = None  # every write of this file
    stop_reason: str | None = None  # why the stop was requested (operator, signal, duration, disk critical, ...)


class OutputFile(_Strict):
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)


class OutputInspection(_Strict):
    """What was seen in the bag directory right after the recorder ended. `files` is the
    hash inventory at that moment, so export can later prove the bytes did not change."""

    bag_directory_present: bool | None = None  # None = not checked
    inspected: bool = False
    readability: ArtifactReadability | None = None
    metadata_present: bool | None = None
    segment_count: int | None = None
    files: list[OutputFile] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


class DiskStatus(_Strict):
    """Free space on the filesystem holding the session, as last observed. `level` is a pure
    function of the numbers and the thresholds; it says nothing about write throughput."""

    level: Literal["ok", "warning", "critical", "unknown"] = "unknown"
    free_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    warn_free_bytes: int | None = None
    critical_free_bytes: int | None = None
    checked_unix_ns: int | None = None
    lowest_free_bytes: int | None = Field(default=None, ge=0)  # minimum seen during this capture


class Reconciliation(_Strict):
    """Written by `capture recover` when it settles a session whose supervisor vanished."""

    reconciled_unix_ns: int
    previous_state: CaptureState
    reason: str
    rebooted_since_capture_start: bool | None = None  # None: boot ids unavailable
    supervisor_identity_result: Literal["same", "different", "gone", "unknown"]
    recorder_identity_result: Literal["same", "different", "gone", "unknown", "not_recorded"]
    orphan_recorder_action: Literal["none", "stopped_gracefully", "killed_after_timeout", "left_running"] = "none"
    evidence_modified: Literal[False] = False  # recovery only reads the bag; it never edits or deletes


class RecorderStatistics(_Strict):
    available: Literal[False] = False
    reason: str = STATS_UNAVAILABLE_REASON


class BagFinalization(_Strict):
    appears_finalized: bool | None = None  # tri-state; None = unknown
    basis: str = "not evaluated"


class CaptureHealth(_Strict):
    capture_health_schema_version: str = CAPTURE_HEALTH_SCHEMA_VERSION
    session_id: str = Field(min_length=1)
    state: CaptureState
    supervisor: SupervisorInfo
    requested: RequestedCapture
    recorder: RecorderRecord
    timing: CaptureTiming = Field(default_factory=CaptureTiming)
    events: list[CaptureEvent] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    interruptions: list[str] = Field(default_factory=list)  # known interruptions of recording
    recorder_statistics: RecorderStatistics = Field(default_factory=RecorderStatistics)
    bag_finalization: BagFinalization = Field(default_factory=BagFinalization)
    output: OutputInspection = Field(default_factory=OutputInspection)
    disk: DiskStatus = Field(default_factory=DiskStatus)  # 1.1
    reconciliation: Reconciliation | None = None  # 1.1; set only by `capture recover`
    health_write_failures: int = Field(default=0, ge=0)  # 1.1: times writing this file failed
    environment: dict[str, str] = Field(default_factory=dict)  # allow-list only
    unknown_fields: list[str] = Field(default_factory=lambda: [
        "machine-readable recorder statistics (rosbag2 exposes none through the CLI)",
        "messages dropped between publisher and recorder (DDS level)",
        "whether the recorder's loss warnings (if none were seen) were simply not emitted",
        "sensor health",
        "relation between supervisor wall-clock time and MCAP log_time",
    ])

    @field_validator("capture_health_schema_version")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in SUPPORTED_CAPTURE_HEALTH_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported capture_health_schema_version {v!r}; supported: {list(SUPPORTED_CAPTURE_HEALTH_SCHEMA_VERSIONS)}"
            )
        return v

    @field_validator("environment")
    @classmethod
    def _env_allowlist(cls, v: dict[str, str]) -> dict[str, str]:
        extra = sorted(set(v) - set(ENV_ALLOWLIST))
        if extra:
            raise ValueError(f"environment may only contain {list(ENV_ALLOWLIST)}; found {extra}")
        return v

    @model_validator(mode="after")
    def _finalized_is_earned(self) -> "CaptureHealth":
        """'appears finalized' can never be represented after anything that contradicts it."""
        if self.bag_finalization.appears_finalized is True:
            if self.state not in FINALIZABLE_STATES:
                raise ValueError(f"appears_finalized=true is invalid for state '{self.state.value}'")
            if self.output.readability is not ArtifactReadability.READABLE:
                raise ValueError("appears_finalized=true requires the output to have been inspected as readable")
        return self

    def summary(self, sidecar_sha256: str) -> CaptureHealthSummary:
        return CaptureHealthSummary(
            session_id=self.session_id, health_schema_version=self.capture_health_schema_version,
            state=self.state.value, appears_finalized=self.bag_finalization.appears_finalized,
            sidecar_sha256=sidecar_sha256,
        )


@dataclass(frozen=True)
class LoadedHealth:
    health: CaptureHealth
    raw: bytes
    sha256: str
    path: Path


def load_health(path: str | Path) -> LoadedHealth:
    p = Path(path)
    if not p.exists():
        raise HealthError(f"Capture-health file not found: '{p}'", hint="Check the path.")
    if not p.is_file():
        raise HealthError(f"Capture-health path is not a regular file: '{p}'")
    try:
        raw, digest = read_and_hash(p, max_bytes=MAX_HEALTH_BYTES)
    except FileTooLargeError as e:
        raise HealthError(f"Capture-health file '{p}' is larger than {e.max_bytes} bytes.") from e
    except OSError as e:
        raise HealthError(f"Cannot read capture-health file '{p}': {e.strerror or e}") from e
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=json_no_duplicates)
    except UnicodeDecodeError as e:
        raise HealthError(f"Capture-health file '{p}' is not valid UTF-8 (byte offset {e.start}).") from e
    except json.JSONDecodeError as e:
        raise HealthError(f"Capture-health file '{p}' is not valid JSON at line {e.lineno}, column {e.colno}: {e.msg}") from e
    except ValueError as e:
        raise HealthError(f"Capture-health file '{p}' is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise HealthError(f"Capture-health file '{p}' must contain a JSON object at the top level.")
    try:
        health = CaptureHealth.model_validate(data)
    except ValidationError as e:
        issues = [format_issue(err) for err in e.errors(include_url=False)]
        lines = "\n".join(f"  - {i}" for i in issues[:20])
        raise HealthError(
            f"Capture-health file '{p}' failed validation ({len(issues)} error(s)):\n{lines}",
            hint="Use the capture_health.json written by `physicore-collector capture run`, unmodified.",
        ) from e
    return LoadedHealth(health=health, raw=raw, sha256=digest, path=p)


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def _check(cid: str, outcome: CheckOutcome, message: str, *, observed=None, unknowns=None) -> CheckResult:
    return CheckResult(
        check_id=cid, category=CheckCategory.CAPTURE, outcome=outcome, required=True,
        message=message, observed=observed or {}, unknowns=unknowns or [],
    )


def build_capture_checks(loaded: LoadedHealth, bag: BagAnalysis | None) -> list[CheckResult]:
    h = loaded.health
    checks: list[CheckResult] = []
    obs = {"state": h.state.value, "recorder_exit_code": h.recorder.exit_code,
           "session_id": h.session_id}
    base_unknown = ["recorder statistics (none exposed by rosbag2 CLI)", "messages dropped before recording"]
    if h.state in CLEAN_STATES:
        checks.append(_check(
            "capture:lifecycle", CheckOutcome.PASS,
            f"capture '{h.session_id}' ended with a graceful stop and recorder exit status 0",
            observed=obs, unknowns=base_unknown,
        ))
    elif h.state in (CaptureState.RUNNING, CaptureState.STARTING):
        checks.append(_check(
            "capture:lifecycle", CheckOutcome.FAIL,
            "the capture-health record says the recording was still running when it was last written; "
            "the supervisor ended without recording an outcome (killed, crashed or power loss). "
            "The recording is known to be interrupted or unfinished.",
            observed=obs, unknowns=["when and why the capture ended"] + base_unknown,
        ))
    elif h.state is CaptureState.INTERRUPTED:
        why = h.reconciliation.reason if h.reconciliation else "the supervisor vanished without recording an outcome"
        checks.append(_check(
            "capture:lifecycle", CheckOutcome.FAIL,
            f"the capture was interrupted: {why}. The recording is known to be unfinished; how far it got is not established.",
            observed=obs, unknowns=["when and why the capture ended"] + base_unknown,
        ))
    else:
        checks.append(_check(
            "capture:lifecycle", CheckOutcome.FAIL,
            f"capture '{h.session_id}' did not end cleanly: state '{h.state.value}'"
            + (f"; errors: {'; '.join(h.errors)}" if h.errors else "")
            + (f"; interruptions: {'; '.join(h.interruptions)}" if h.interruptions else ""),
            observed=obs, unknowns=base_unknown,
        ))

    if h.state is CaptureState.STOPPED_DISK_CRITICAL:
        pass  # explained by the lifecycle check above (state is not clean)
    if h.disk.level in ("warning", "critical"):
        checks.append(CheckResult(
            check_id="capture:disk", category=CheckCategory.CAPTURE, outcome=CheckOutcome.FAIL, required=False,
            message=f"free disk space reached the '{h.disk.level}' level during capture "
                    f"(lowest observed {h.disk.lowest_free_bytes} bytes free; thresholds warn "
                    f"{h.disk.warn_free_bytes}, critical {h.disk.critical_free_bytes}). Free space does not show that write "
                    "throughput was sufficient.",
            observed={"level": h.disk.level, "lowest_free_bytes": h.disk.lowest_free_bytes},
            unknowns=["whether disk pressure caused any message loss"],
        ))
    if h.recorder.reported_loss_lines:
        n = len(h.recorder.reported_loss_lines)
        checks.append(_check(
            "capture:recorder_reported_loss", CheckOutcome.FAIL,
            f"the recorder's own console output reported dropped or unflushed messages ({n} matching line(s), kept "
            "verbatim in the capture-health record), so this recording is known to be missing data. Which messages, "
            "and how much, is in those lines; no other count is available.",
            observed={"lines": list(h.recorder.reported_loss_lines)},
            unknowns=["exact extent of the loss", "any loss the recorder did not report"],
        ))
    if bag is None:
        return checks
    if not h.output.inspected:
        checks.append(_check(
            "capture:output_unchanged", CheckOutcome.INCONCLUSIVE,
            "the capture-health record holds no output file hashes, so it cannot be shown that the bag is "
            "unchanged since capture ended",
            unknowns=["whether the bag changed after the capture ended"],
        ))
        return checks
    recorded = {f.relative_path: f for f in h.output.files}
    current = {f.relative_path: f for f in bag.files}
    diffs: list[str] = []
    for rel in sorted(set(recorded) | set(current)):
        r, c = recorded.get(rel), current.get(rel)
        if r is None:
            diffs.append(f"'{rel}' is present now but was not present when capture ended")
        elif c is None:
            diffs.append(f"'{rel}' was present when capture ended but is missing or unusable now")
        elif (r.sha256, r.size_bytes) != (c.sha256, c.size_bytes):
            diffs.append(f"'{rel}' differs from its digest recorded at capture end")
    if diffs:
        checks.append(_check(
            "capture:output_unchanged", CheckOutcome.FAIL,
            "the bag differs from the state recorded when capture ended: " + "; ".join(diffs),
            observed={"differences": diffs}, unknowns=["who or what changed the files, and when"],
        ))
    else:
        checks.append(_check(
            "capture:output_unchanged", CheckOutcome.PASS,
            f"all {len(recorded)} bag file(s) match the SHA-256 digests recorded when capture ended",
            unknowns=["a matching digest proves the bytes are unchanged since capture end, not that they are complete or true"],
        ))
    return checks
