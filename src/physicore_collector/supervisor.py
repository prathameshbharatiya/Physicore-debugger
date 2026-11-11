"""Capture supervisor: orchestrates ONE rosbag2 recording and records what happened.

Responsibilities (and only these):
  profile -> prepared session (STARTING record) -> adopt: take locks, start the recorder, watch
  (heartbeat, disk, stop requests) -> graceful stop (bounded) -> inspect the output bag -> write
  `capture_health.json`.

It does not record messages, does not validate evidence against profiles, and does not package.
The output bag directory is handed to the existing pipeline (`session validate` / `export`).

Guarantees (each covered by tests):
  * Never overwrites: the session directory is created exclusively; the bag directory is left
    for rosbag2 to create and is never touched beforehand.
  * One supervisor per session and one ACTIVE capture per output directory: both are kernel
    flocks that vanish with the process however it dies, so a killed supervisor can never leave
    a lock that makes it look alive, and two starts cannot both record.
  * Bounded shutdown: a graceful stop is requested, then the recorder is killed after
    `stop_timeout_seconds`. The recorder's whole process group is killed, so no orphans.
  * Every observable outcome is recorded. An unexpected recorder exit, a stop timeout, a start
    failure, a disk-critical stop and a supervisor bug each have their own state.
  * The sidecar is rewritten atomically after every event and every heartbeat, so a supervisor
    that is itself killed leaves a record whose last state is `running`: `capture recover` (or
    any status read) treats a `running` record without a live supervisor as interrupted.
  * `appears_finalized` is only ever True after a graceful stop AND a fully consistent, readable bag.
  * Free-space monitoring only ever STOPS the recorder gracefully. Nothing is ever deleted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .bag import scan_bag
from .capture_health import (
    ENV_ALLOWLIST, FINALIZABLE_STATES, BagFinalization, CaptureEvent, CaptureHealth,
    CaptureState, CaptureTiming, DiskStatus, EventKind, OutputFile, OutputInspection,
    ProcessIdentityRecord, RecorderRecord, RequestedCapture, SupervisorInfo, load_health,
)
from .errors import (
    CaptureConfigError, CaptureError, CaptureSessionExistsError, HealthError, InputError, RecorderStartError,
)
from .procident import ProcessIdentity, identity_of
from .profile import LoadedProfile, load_profile
from .recorder import RecorderAdapter, RecorderSpec, sanitize_command
from .schemas import ArtifactReadability, CaptureConfig
from .sessionlock import FileLock, LockHeldError, read_lock_text
from .versions import TOOL_NAME, TOOL_VERSION

HEALTH_NAME = "capture_health.json"
BAG_DIR_NAME = "bag"
SESSION_LOCK_NAME = "supervisor.lock"
ACTIVE_LOCK_NAME = ".active-capture.lock"  # in the OUTPUT directory: one active capture per directory
STOP_REQUEST_NAME = "stop_request.json"
PROFILE_COPY_STEM = "profile"
SUPERVISOR_LOG_NAME = "supervisor.log"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_PROBLEMS_RECORDED = 20
MAX_STOP_REQUEST_BYTES = 4096


class StopRequest(threading.Event):
    """A stop event that remembers WHY it was set (e.g. 'signal SIGTERM'). A plain
    threading.Event also works; the reason is then recorded generically."""

    reason: str | None = None

    def request(self, reason: str) -> None:
        if not self.is_set():
            self.reason = reason
        self.set()


class Clock(Protocol):
    def wall_ns(self) -> int: ...
    def mono_ns(self) -> int: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def wall_ns(self) -> int:
        return time.time_ns()

    def mono_ns(self) -> int:
        return time.monotonic_ns()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


FINAL_WRITE_ATTEMPTS = 4
FINAL_WRITE_RETRY_SECONDS = 1.0


@dataclass(frozen=True)
class CaptureResult:
    session_dir: Path
    bag_dir: Path
    health_path: Path
    health: CaptureHealth
    # False if the FINAL record could not be written (e.g. disk full) even after retries. The
    # on-disk record is then stale (still `running`): `health` is the truth, the file is not.
    record_persisted: bool = True


@dataclass(frozen=True)
class PreparedSession:
    """A session that exists on disk with a STARTING record but is not yet being supervised."""

    session_dir: Path
    bag_dir: Path
    session_id: str
    out_root: Path
    loaded: LoadedProfile  # loaded from the session's own byte-identical profile copy
    cfg: CaptureConfig
    spec: RecorderSpec
    duration_seconds: float | None
    launch_mode: str
    service_name: str | None


def new_session_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def write_health_atomic(path: Path, health: CaptureHealth) -> None:
    """Replace `path` (our own sidecar) atomically: readers see the old or the new file, never half."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    data = (health.model_dump_json(indent=2) + "\n").encode()
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def resolve_topics(loaded: LoadedProfile) -> tuple[tuple[str, ...], CaptureConfig]:
    """Topics to record and the capture config, validated BEFORE anything is created or started."""
    cfg = loaded.profile.capture or CaptureConfig()
    topics = tuple(cfg.topics) if cfg.topics else tuple(t.name for t in loaded.profile.topics)
    for name in topics:
        # A name like '-a' would be parsed by `ros2 bag record` as an option (record EVERYTHING).
        if not name.startswith("/") or any(ch.isspace() for ch in name):
            raise CaptureConfigError(
                f"Cannot record topic {name!r}: topic names must be fully qualified (start with '/') and contain "
                "no whitespace.",
                hint="Fix the topic name in the profile (topics[].name or capture.topics).",
            )
    return topics, cfg


def _to_record(ident: ProcessIdentity | None) -> ProcessIdentityRecord | None:
    return None if ident is None else ProcessIdentityRecord(
        pid=ident.pid, starttime_ticks=ident.starttime_ticks, boot_id=ident.boot_id)


def disk_level(free: int, warn: int | None, critical: int | None) -> str:
    """Pure function of the numbers; 'unknown' when no thresholds are configured."""
    if critical is not None and free < critical:
        return "critical"
    if warn is not None and free < warn:
        return "warning"
    return "ok" if (warn is not None or critical is not None) else "unknown"


def read_stop_request(session_dir: Path) -> str | None:
    """The reason in <session>/stop_request.json if a stop was requested through the file, else None.
    A present-but-unreadable file still counts as a request (with a generic reason)."""
    p = session_dir / STOP_REQUEST_NAME
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            data = json.loads(f.read(MAX_STOP_REQUEST_BYTES).decode("utf-8", errors="replace"))
        reason = data.get("reason") if isinstance(data, dict) else None
        return f"operator request: {reason}" if isinstance(reason, str) and reason else "operator request"
    except (OSError, ValueError):
        return "operator request (stop_request.json unreadable)"


@dataclass(frozen=True)
class OutputAssessment:
    output: OutputInspection
    finalization: BagFinalization
    errors: tuple[str, ...] = ()
    event_detail: str | None = None  # None: no event to record (nothing was inspected)


def inspect_output(bag_dir: Path, state: CaptureState) -> OutputAssessment:
    """Read-only inspection of the recorder's output, with the finalization judgement for `state`.
    Shared by the supervisor (at capture end) and by `capture recover` (after the fact), so a bag
    is judged by the same rules either way. Never writes anything."""
    if state is CaptureState.FAILED_TO_START and not bag_dir.exists():
        return OutputAssessment(
            OutputInspection(bag_directory_present=False, inspected=False, problems=["recorder never started; no output expected"]),
            BagFinalization(appears_finalized=None, basis="no output: the recorder never started"))
    if not bag_dir.exists():
        return OutputAssessment(
            OutputInspection(bag_directory_present=False, inspected=True, problems=["expected bag directory was not created by the recorder"]),
            BagFinalization(appears_finalized=False, basis="no bag directory exists"),
            ("expected output missing: the recorder produced no bag directory",), "bag directory missing")
    try:
        scan, analysis = scan_bag(bag_dir)
    except InputError as e:
        return OutputAssessment(
            OutputInspection(bag_directory_present=True, inspected=False, problems=[e.message]),
            BagFinalization(appears_finalized=None, basis=f"output could not be inspected: {e.message}"),
            (), "inspection failed")
    insp = scan.inspection
    output = OutputInspection(
        bag_directory_present=True, inspected=True, readability=insp.readability,
        metadata_present=analysis.info.metadata_present, segment_count=len(analysis.info.segments),
        files=[OutputFile(relative_path=f.relative_path, size_bytes=f.size_bytes, sha256=f.sha256) for f in analysis.files],
        problems=insp.errors[:MAX_PROBLEMS_RECORDED],
    )
    readable = insp.readability is ArtifactReadability.READABLE
    if state in FINALIZABLE_STATES and readable:
        fin = BagFinalization(appears_finalized=True, basis="graceful stop, recorder exit status 0, metadata.yaml present and consistent with the files, every segment fully readable")
    elif not analysis.info.metadata_present:
        fin = BagFinalization(appears_finalized=False, basis="metadata.yaml is missing (rosbag2 writes it when the recording is closed)")
    elif not readable:
        fin = BagFinalization(appears_finalized=False, basis=f"the output was inspected as {insp.readability.value.replace('_', ' ')}")
    else:
        fin = BagFinalization(appears_finalized=None, basis=f"the output looks consistent but the capture ended in state '{state.value}', so finalization cannot be affirmed")
    return OutputAssessment(output, fin, (), f"{insp.readability.value}, {len(analysis.info.segments)} segment(s)")


class CaptureSupervisor:
    def __init__(
        self, adapter: RecorderAdapter, *, clock: Clock | None = None, poll_interval: float = 0.1,
        disk_usage: Callable[[Path], "shutil._ntuple_diskusage"] | None = None,
    ) -> None:
        self._adapter = adapter
        self._clock = clock or SystemClock()
        self._poll = poll_interval
        self._disk_usage = disk_usage or (lambda p: shutil.disk_usage(p))

    # ------------------------------------------------------------------ foreground

    def run(
        self,
        loaded: LoadedProfile,
        out_root: str | Path,
        *,
        duration_seconds: float | None = None,
        stop_event: threading.Event | None = None,
        session_id: str | None = None,
        launch_mode: str = "foreground",
        service_name: str | None = None,
    ) -> CaptureResult:
        """prepare + adopt in this process (what `capture run` does)."""
        prepared = self.prepare(
            loaded, out_root, duration_seconds=duration_seconds, session_id=session_id,
            launch_mode=launch_mode, service_name=service_name,
        )
        return self.execute(prepared, stop_event=stop_event)

    # ------------------------------------------------------------------ prepare / adopt

    def prepare(
        self,
        loaded: LoadedProfile,
        out_root: str | Path,
        *,
        duration_seconds: float | None = None,
        session_id: str | None = None,
        launch_mode: str = "detached",
        service_name: str | None = None,
    ) -> PreparedSession:
        """Validate everything, create the session directory EXCLUSIVELY, keep a byte-identical copy
        of the profile in it, and write the STARTING record. Starts nothing."""
        topics, cfg = resolve_topics(loaded)
        if duration_seconds is not None and duration_seconds <= 0:
            raise CaptureConfigError("--duration must be greater than 0 seconds.")
        root = Path(out_root)
        if not root.is_dir():
            raise CaptureConfigError(
                f"Capture output directory does not exist: '{root}'",
                hint="Create it first; capture never creates parent directories.",
            )
        free = self._disk_usage(root).free
        if cfg.min_free_disk_bytes is not None and free < cfg.min_free_disk_bytes:
            raise CaptureConfigError(
                f"Refusing to start: {free} bytes free under '{root}' is below capture.min_free_disk_bytes "
                f"({cfg.min_free_disk_bytes}).",
            )
        if cfg.disk_critical_free_bytes is not None and free < cfg.disk_critical_free_bytes:
            raise CaptureConfigError(
                f"Refusing to start: {free} bytes free under '{root}' is already below capture.disk_critical_free_bytes "
                f"({cfg.disk_critical_free_bytes}).",
            )
        sid = session_id or new_session_id()
        if not SESSION_ID_PATTERN.match(sid):
            raise CaptureConfigError(f"Invalid session id {sid!r}: use letters, digits, '.', '_' or '-' (max 64, not starting with a symbol).")
        try:
            raw_profile = loaded.path.read_bytes()
        except OSError as e:
            raise CaptureConfigError(f"Cannot read the profile '{loaded.path}' to keep a copy: {e.strerror or e}") from e
        import hashlib
        if hashlib.sha256(raw_profile).hexdigest() != loaded.sha256:
            raise CaptureConfigError(f"The profile '{loaded.path}' changed after it was loaded; not starting.")

        session_dir = root / sid
        try:
            os.mkdir(session_dir)  # exclusive: never reuse or overwrite a session
        except FileExistsError as e:
            raise CaptureSessionExistsError(f"Capture session directory already exists: '{session_dir}'. Capture never overwrites.",
                                            hint="Choose another --out directory or --session-id.") from e
        except OSError as e:
            raise CaptureConfigError(f"Cannot create capture session directory '{session_dir}': {e.strerror or e}") from e
        copy_name = f"{PROFILE_COPY_STEM}.{'json' if loaded.source_format == 'json' else 'yaml'}"
        with open(session_dir / copy_name, "xb") as f:
            f.write(raw_profile)

        bag_dir = session_dir / BAG_DIR_NAME
        spec = RecorderSpec(
            bag_dir=bag_dir, topics=topics, storage_id=cfg.storage_id,
            max_bag_size_bytes=cfg.max_bag_size_bytes, max_bag_duration_seconds=cfg.max_bag_duration_seconds,
        )
        prepared = PreparedSession(
            session_dir=session_dir, bag_dir=bag_dir, session_id=sid, out_root=root, loaded=loaded, cfg=cfg,
            spec=spec, duration_seconds=duration_seconds, launch_mode=launch_mode, service_name=service_name,
        )
        run = _Run(self, prepared, stop_event=None)
        run.state = CaptureState.STARTING
        run.timing.start_unix_ns = self._clock.wall_ns()
        run._flush()
        return prepared

    def execute(self, prepared: PreparedSession, *, stop_event: threading.Event | None = None) -> CaptureResult:
        run = _Run(self, prepared, stop_event=stop_event)
        return run.execute()

    def adopt(self, session_dir: str | Path, *, stop_event: threading.Event | None = None,
              launch_mode: str | None = None) -> CaptureResult:
        """Take over a session that `capture start` prepared (the detached supervisor's entry point).
        Refuses unless the record says STARTING. If adoption cannot proceed, the failure is written
        into the record so the session is never left 'starting' forever."""
        sdir = Path(session_dir)
        try:
            health = load_health(sdir / HEALTH_NAME).health
        except HealthError as e:
            raise CaptureError(f"Cannot adopt '{sdir}': {e.message}") from e
        if health.state is not CaptureState.STARTING:
            raise CaptureError(f"Refusing to adopt '{sdir}': its state is '{health.state.value}', not 'starting'.")
        try:
            copy = next(p for p in (sdir / f"{PROFILE_COPY_STEM}.yaml", sdir / f"{PROFILE_COPY_STEM}.json") if p.is_file())
            loaded = load_profile(copy)
            if loaded.sha256 != health.requested.profile_sha256:
                raise CaptureConfigError("the session's profile copy does not match the recorded profile digest")
            topics, cfg = resolve_topics(loaded)
            prepared = PreparedSession(
                session_dir=sdir, bag_dir=sdir / BAG_DIR_NAME, session_id=health.session_id, out_root=sdir.parent,
                loaded=loaded, cfg=cfg,
                spec=RecorderSpec(bag_dir=sdir / BAG_DIR_NAME, topics=topics, storage_id=cfg.storage_id,
                                  max_bag_size_bytes=cfg.max_bag_size_bytes,
                                  max_bag_duration_seconds=cfg.max_bag_duration_seconds),
                duration_seconds=health.requested.planned_duration_seconds,
                launch_mode=launch_mode or health.supervisor.launch_mode or "detached",
                service_name=health.supervisor.service_name,
            )
        except (StopIteration, CaptureError, InputError, OSError) as e:
            msg = "no profile copy in the session" if isinstance(e, StopIteration) else getattr(e, "message", str(e))
            self._fail_adoption(sdir, health, msg)
            raise CaptureError(f"Cannot adopt '{sdir}': {msg}") from e
        return self.execute(prepared, stop_event=stop_event)

    def _fail_adoption(self, sdir: Path, health: CaptureHealth, msg: str) -> None:
        data = health.model_dump(mode="json")
        data["state"] = CaptureState.FAILED_TO_START.value
        data["errors"] = [*data["errors"], f"supervisor could not adopt the session: {msg}"]
        data["output"] = OutputInspection(bag_directory_present=False, problems=["recorder never started; no output expected"]).model_dump(mode="json")
        data["bag_finalization"] = BagFinalization(appears_finalized=None, basis="no output: the recorder never started").model_dump(mode="json")
        try:
            write_health_atomic(sdir / HEALTH_NAME, CaptureHealth.model_validate(data))
        except (OSError, ValueError):
            pass  # nothing more can be recorded; the caller still gets the exception


class _Run:
    """State of one supervised capture. Kept apart so the supervisor object holds no per-run state."""

    def __init__(self, sup: CaptureSupervisor, prep: PreparedSession, stop_event: threading.Event | None) -> None:
        self.sup, self.adapter, self.clock = sup, sup._adapter, sup._clock
        self.prep, self.loaded, self.cfg, self.spec = prep, prep.loaded, prep.cfg, prep.spec
        self.sid, self.session_dir, self.bag_dir = prep.session_id, prep.session_dir, prep.bag_dir
        self.health_path = prep.session_dir / HEALTH_NAME
        self.duration = prep.duration_seconds
        self.stop_event = stop_event if stop_event is not None else StopRequest()
        self.state = CaptureState.RUNNING
        self.events: list[CaptureEvent] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.interruptions: list[str] = []
        self.timing = CaptureTiming()
        self.exit_code: int | None = None
        self.signal_no: int | None = None
        self.pid: int | None = None
        self.sup_identity: ProcessIdentity | None = None
        self.rec_identity: ProcessIdentity | None = None
        self.output = OutputInspection()
        self.finalization = BagFinalization()
        self.disk = DiskStatus(warn_free_bytes=self.cfg.disk_warn_free_bytes,
                               critical_free_bytes=self.cfg.disk_critical_free_bytes)
        self.disk_critical = False
        self.write_failures = 0
        self.started_mono: int | None = None
        self.last_hb_mono: int | None = None
        self.stop_reason: str | None = None
        self.locks: list[FileLock] = []
        self.command = sanitize_command(self.adapter.command(self.spec), self.spec.bag_dir)

    # ----- sidecar -----------------------------------------------------------------------

    def _snapshot(self) -> CaptureHealth:
        c = self.cfg
        return CaptureHealth(
            session_id=self.sid, state=self.state,
            supervisor=SupervisorInfo(pid=os.getpid(), tool_name=TOOL_NAME, tool_version=TOOL_VERSION, platform=sys.platform,
                                      identity=_to_record(self.sup_identity), launch_mode=self.prep.launch_mode,
                                      service_name=self.prep.service_name),
            requested=RequestedCapture(
                profile_id=self.loaded.profile.profile_id, profile_sha256=self.loaded.sha256,
                storage_id=c.storage_id, topics=list(self.spec.topics),
                max_bag_size_bytes=c.max_bag_size_bytes, max_bag_duration_seconds=c.max_bag_duration_seconds,
                stop_timeout_seconds=c.stop_timeout_seconds, min_free_disk_bytes=c.min_free_disk_bytes,
                planned_duration_seconds=self.duration, identity=c.identity,
                heartbeat_seconds=c.heartbeat_seconds, disk_warn_free_bytes=c.disk_warn_free_bytes,
                disk_critical_free_bytes=c.disk_critical_free_bytes,
            ),
            recorder=RecorderRecord(
                adapter=self.adapter.name, command=self.command, pid=self.pid, exit_code=self.exit_code,
                terminated_by_signal=self.signal_no, identity=_to_record(self.rec_identity),
                stderr_tail=self.adapter.output_tail(), reported_loss_lines=self.adapter.output_signals(),
            ),
            timing=self.timing, events=list(self.events), errors=list(self.errors),
            warnings=list(self.warnings), interruptions=list(self.interruptions),
            bag_finalization=self.finalization, output=self.output, disk=self.disk,
            health_write_failures=self.write_failures,
            environment={k: src_env[k] for k in ENV_ALLOWLIST if k in (src_env := (getattr(self.adapter, "env", None) or os.environ))},
        )

    def _flush(self, *, final: bool = False) -> bool:
        """Write the record. Mid-run failures are counted and survived (a full disk must not kill a
        recording). The FINAL write is retried, and its failure is reported, never swallowed."""
        attempts = FINAL_WRITE_ATTEMPTS if final else 1
        for attempt in range(attempts):
            self.timing.last_state_update_unix_ns = self.clock.wall_ns()
            try:
                write_health_atomic(self.health_path, self._snapshot())
                return True
            except OSError as e:
                self.write_failures += 1
                print(f"physicore-collector: could not write {self.health_path.name}: {e.strerror or e}", file=sys.stderr, flush=True)
                if attempt + 1 < attempts:
                    self.clock.sleep(FINAL_WRITE_RETRY_SECONDS)
        if final:
            print(f"physicore-collector: ERROR: the final capture outcome (state '{self.state.value}') could NOT be written to "
                  f"'{self.health_path}'; the record on disk is stale. Check free disk space, then run `capture recover`.",
                  file=sys.stderr, flush=True)
        return False

    def _event(self, kind: EventKind, detail: str | None = None) -> None:
        self.events.append(CaptureEvent(seq=len(self.events), kind=kind, wall_clock_unix_ns=self.clock.wall_ns(),
                                        monotonic_ns=self.clock.mono_ns(), detail=detail))

    def _record_exit(self, rc: int) -> None:
        if rc < 0:
            self.signal_no, self.exit_code = -rc, None
        else:
            self.exit_code = rc

    # ----- disk / heartbeat ----------------------------------------------------------------

    def _check_disk(self) -> None:
        try:
            u = self.sup._disk_usage(self.session_dir)
        except OSError as e:
            self.disk = self.disk.model_copy(update={"level": "unknown", "checked_unix_ns": self.clock.wall_ns()})
            if not any("disk space could not be read" in w for w in self.warnings):
                self.warnings.append(f"disk space could not be read: {e.strerror or e}")
            return
        level = disk_level(u.free, self.cfg.disk_warn_free_bytes, self.cfg.disk_critical_free_bytes)
        lowest = u.free if self.disk.lowest_free_bytes is None else min(self.disk.lowest_free_bytes, u.free)
        prev = self.disk.level
        self.disk = self.disk.model_copy(update={"level": level, "free_bytes": u.free, "total_bytes": u.total,
                                                  "checked_unix_ns": self.clock.wall_ns(), "lowest_free_bytes": lowest})
        if level == "warning" and prev not in ("warning", "critical"):
            self._event("disk_warning", f"{u.free} bytes free")
            self.warnings.append(f"free disk space fell below the warning threshold ({self.cfg.disk_warn_free_bytes} bytes)")
        if level == "critical" and not self.disk_critical:
            self.disk_critical = True
            self._event("disk_critical", f"{u.free} bytes free")
            self.warnings.append(f"free disk space fell below the critical threshold ({self.cfg.disk_critical_free_bytes} bytes)")

    def _heartbeat(self) -> None:
        self.timing.last_heartbeat_unix_ns = self.clock.wall_ns()
        self.last_hb_mono = self.clock.mono_ns()
        self._check_disk()
        self._flush()

    # ----- lifecycle ---------------------------------------------------------------------

    def _acquire_locks(self) -> str | None:
        """Take the session lock and the output-directory 'active capture' lock. Returns an error
        message if another capture is active in this directory (the session lock failing raises:
        a second supervisor for the SAME session must never proceed)."""
        sess = FileLock(self.session_dir / SESSION_LOCK_NAME)
        try:
            sess.acquire()
        except LockHeldError as e:
            raise CaptureError(f"Another supervisor is already attached to session '{self.sid}'.") from e
        self.locks.append(sess)
        active = FileLock(self.prep.out_root / ACTIVE_LOCK_NAME)
        try:
            active.acquire()
        except LockHeldError:
            other = read_lock_text(self.prep.out_root / ACTIVE_LOCK_NAME)
            return ("another capture is already active in this output directory"
                    + (f" (session '{other}')" if other else "") + "; only one capture may record per output directory at a time")
        active.write_text(self.sid)
        self.locks.append(active)
        return None

    def _release_locks(self) -> None:
        for lk in reversed(self.locks):
            lk.release()
        self.locks.clear()

    def execute(self) -> CaptureResult:
        self.timing.supervisor_started_unix_ns = self.clock.wall_ns()
        self.timing.start_unix_ns = self.timing.supervisor_started_unix_ns
        self.started_mono = self.clock.mono_ns()
        try:
            busy = self._acquire_locks()
            self.sup_identity = identity_of(os.getpid())
            self._event("capture_started", f"session {self.sid}")
            if busy is not None:
                self.state = CaptureState.FAILED_TO_START
                self.errors.append(f"capture did not start: {busy}")
                self._event("start_failed", busy)
                self._finish()
                return self._result()
            try:
                try:
                    self.adapter.start(self.spec)
                except RecorderStartError as e:
                    self.state = CaptureState.FAILED_TO_START
                    self.errors.append(f"recorder failed to start: {e.message}" + (f" ({e.hint})" if e.hint else ""))
                    self._event("start_failed", e.message)
                    self._finish()
                    return self._result()
                self.pid = self.adapter.pid
                self.rec_identity = identity_of(self.pid) if self.pid is not None else None
                self.timing.recorder_started_unix_ns = self.clock.wall_ns()
                self._event("recorder_started", f"pid {self.pid}")
                self.state = CaptureState.RUNNING
                self._heartbeat()  # state is RUNNING from here: if we die now the record says so
                self._watch_and_stop()
            except BaseException as e:  # noqa: BLE001 - recorded, recorder killed, then re-raised (never swallowed)
                self.state = CaptureState.SUPERVISOR_ERROR
                self.errors.append(f"supervisor error: {type(e).__name__}: {e}")
                self._event("error", f"{type(e).__name__}: {e}")
                self._ensure_recorder_gone()
                try:
                    self._finish()
                finally:
                    raise
            finally:
                self._ensure_recorder_gone()
            self._finish()
            return self._result()
        finally:
            self._release_locks()

    def _stop_reason_from_event(self) -> str:
        return getattr(self.stop_event, "reason", None) or "stop requested"

    def _watch_and_stop(self) -> None:
        reason: str | None = None
        t0 = self.clock.mono_ns()
        hb = self.cfg.heartbeat_seconds
        try:
            while True:
                rc = self.adapter.poll()
                if rc is not None:
                    self._unexpected_exit(rc)
                    return
                marker = read_stop_request(self.session_dir)
                if marker is not None:
                    reason = marker
                    break
                if self.stop_event.is_set():
                    reason = self._stop_reason_from_event()
                    break
                if self.duration is not None and (self.clock.mono_ns() - t0) / 1e9 >= self.duration:
                    reason = f"planned duration of {self.duration:g} s elapsed"
                    break
                if self.disk_critical:
                    reason = "free disk space fell below the critical threshold"
                    break
                if self.last_hb_mono is None or (self.clock.mono_ns() - self.last_hb_mono) / 1e9 >= hb:
                    self._heartbeat()
                    if self.disk_critical:
                        continue  # act on it on the next iteration, after the state is persisted
                self.clock.sleep(self.sup._poll)
        except KeyboardInterrupt:
            reason = "keyboard interrupt"

        self.timing.stop_requested_unix_ns = self.clock.wall_ns()
        self.timing.stop_reason = reason
        self._event("stop_requested", reason)
        self.adapter.request_stop()
        forced = False
        try:
            rc = self.adapter.wait(self.cfg.stop_timeout_seconds)
        except KeyboardInterrupt:
            rc, forced = None, True  # second interrupt while stopping: do not keep waiting
        if rc is None:
            self._event("stop_timeout", "second interrupt while stopping" if forced else f"no exit within {self.cfg.stop_timeout_seconds:g} s")
            self.adapter.kill()
            self._event("recorder_killed")
            self.state = CaptureState.STOP_TIMEOUT_KILLED
            msg = ("the recorder was killed after a second interrupt" if forced
                   else f"the recorder did not stop within {self.cfg.stop_timeout_seconds:g} s and was killed")
            self.interruptions.append(msg + "; rosbag2 may not have written metadata.yaml or flushed its last data")
            self.errors.append(msg)
            rc2 = self.adapter.poll()
            if rc2 is not None:
                self._record_exit(rc2)
            return
        self._record_exit(rc)
        self._event("recorder_exited", f"status {rc}")
        if rc == 0 and self.disk_critical:
            self.state = CaptureState.STOPPED_DISK_CRITICAL
            self.interruptions.append("the capture ended earlier than intended: the supervisor stopped the recorder because free "
                                      "disk space fell below the critical threshold")
        elif rc == 0:
            self.state = CaptureState.STOPPED_CLEAN
        else:
            self.state = CaptureState.STOPPED_WITH_ERROR
            self.errors.append(f"recorder exited with status {rc} after a graceful stop request")

    def _unexpected_exit(self, rc: int) -> None:
        self._record_exit(rc)
        self.state = CaptureState.EXITED_UNEXPECTEDLY
        what = f"killed by signal {-rc}" if rc < 0 else f"status {rc}"
        self._event("unexpected_exit", what)
        self.errors.append(f"the recorder ended on its own ({what}) before any stop was requested")
        self.interruptions.append("recording ended before the capture was stopped; everything after that moment was not recorded")

    def _ensure_recorder_gone(self) -> None:
        try:
            if self.adapter.pid is not None and self.adapter.poll() is None:
                self.adapter.kill()
                self.warnings.append("recorder was still running during supervisor cleanup and was killed")
        except Exception as e:  # noqa: BLE001 - cleanup must not mask the original problem
            self.warnings.append(f"recorder cleanup failed: {type(e).__name__}: {e}")

    # ----- end of capture ----------------------------------------------------------------

    def _finish(self) -> None:
        if self.adapter.output_signals():
            self.warnings.append("the recorder reported dropped or unflushed messages in its console output (see recorder.reported_loss_lines)")
        end_mono = self.clock.mono_ns()
        self.timing.end_unix_ns = self.clock.wall_ns()
        if self.started_mono is not None:
            self.timing.monotonic_duration_seconds = (end_mono - self.started_mono) / 1e9
        self._inspect_output()
        self.persisted = self._flush(final=True)

    persisted = True

    def _inspect_output(self) -> None:
        res = inspect_output(self.bag_dir, self.state)
        self.output, self.finalization = res.output, res.finalization
        self.errors.extend(res.errors)
        if res.event_detail is not None:
            self._event("output_inspected", res.event_detail)

    def _result(self) -> CaptureResult:
        return CaptureResult(self.session_dir, self.bag_dir, self.health_path, self._snapshot(), self.persisted)
