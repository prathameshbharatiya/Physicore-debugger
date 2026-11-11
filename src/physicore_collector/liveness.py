"""Read-time assessment of a capture session: who is alive, and what that means.

Three DIFFERENT questions are answered separately and never merged into one flag:

  supervisor : is a supervisor process attached?  Authority = the session flock (kernel-released on
               any death). A PID is never trusted on its own.
  recorder   : is the recorded recorder process still running? Authority = process IDENTITY
               (pid + start time + boot id), so a reused pid is never mistaken for it.
  capture    : what does the evidence-producing activity amount to? A judgement derived from the
               persisted state plus the two facts above.

This module only READS. It never signals, writes, or repairs anything (see recovery.py for that).
It also never validates the recording: `evidence` is always the fixed hint to run validation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .capture_health import ACTIVE_STATES, CaptureHealth, CaptureState
from .procident import IdentityMatch, ProcessIdentity, check_identity
from .sessionlock import lock_is_held_elsewhere

SESSION_LOCK_NAME = "supervisor.lock"
STARTING_GRACE_SECONDS = 30.0
MIN_STALE_SECONDS = 15.0
STALE_FACTOR = 3.0
EVIDENCE_HINT = "not assessed: status never validates evidence; run `session validate` on the bag"


@dataclass(frozen=True)
class Assessment:
    supervisor: str  # alive | dead (should be attached but is not) | exited (capture over) | unknown
    recorder: str  # running | not_running | orphaned | unknown | not_recorded | not_started
    capture: str  # starting | healthy | degraded | interrupted | finalized | failed
    reasons: tuple[str, ...]
    heartbeat_age_seconds: float | None
    stale_heartbeat: bool
    evidence: str = EVIDENCE_HINT


def _ident(rec) -> ProcessIdentity | None:
    return None if rec is None else ProcessIdentity(rec.pid, rec.starttime_ticks, rec.boot_id)


def supervisor_state(session_dir: Path) -> str:
    """The session lock is the authority. It can lag a supervisor's death by milliseconds (the
    kernel keeps the shared fd table, hence the flock, until its LAST thread has exited, while
    /proc already shows the thread-group leader as a zombie): never use pid state to override it."""
    held = lock_is_held_elsewhere(Path(session_dir) / SESSION_LOCK_NAME)
    return {True: "alive", False: "dead", None: "unknown"}[held]


def is_supervisor_alive(session_dir: Path) -> bool:
    return supervisor_state(session_dir) == "alive"


def recorder_identity_match(h: CaptureHealth) -> IdentityMatch:
    return check_identity(_ident(h.recorder.identity))


def stale_after_seconds(h: CaptureHealth) -> float | None:
    hb = h.requested.heartbeat_seconds
    return None if hb is None else max(STALE_FACTOR * hb, MIN_STALE_SECONDS)


def assess(h: CaptureHealth, session_dir: Path, now_ns: int | None = None) -> Assessment:
    now = now_ns if now_ns is not None else time.time_ns()
    reasons: list[str] = []
    sup = supervisor_state(session_dir)
    ident = recorder_identity_match(h)
    age: float | None = None
    stale = False
    last = h.timing.last_heartbeat_unix_ns
    limit = stale_after_seconds(h)
    if last is not None:
        age = max(0.0, (now - last) / 1e9)
        stale = limit is not None and age > limit

    if h.state in ACTIVE_STATES:
        if h.recorder.identity is None:
            rec = "not_started" if h.state is CaptureState.STARTING else "not_recorded"
        elif ident is IdentityMatch.SAME:
            rec = "running"
        elif ident is IdentityMatch.UNKNOWN:
            rec = "unknown"
        else:
            rec = "not_running"

        if h.state is CaptureState.STARTING:
            started = h.timing.start_unix_ns
            waited = (now - started) / 1e9 if started else None
            if sup == "alive":
                return Assessment(sup, rec, "starting", ("the supervisor is attached and starting the recorder",), age, False)
            if waited is not None and waited < STARTING_GRACE_SECONDS:
                return Assessment(sup, rec, "starting", ("waiting for the detached supervisor to take over",), age, False)
            return Assessment(sup, rec, "interrupted",
                              ("the session was prepared but its supervisor never took over (or died while starting)",), age, False)

        if sup == "alive":
            if rec == "not_running":
                reasons.append("the recorded recorder process is not running although the supervisor is attached")
            if stale:
                reasons.append(f"heartbeat is stale ({age:.0f} s old; expected every {h.requested.heartbeat_seconds:g} s): "
                               "the supervisor is attached but not updating its record")
            if h.disk.level == "warning":
                reasons.append("free disk space is below the warning threshold")
            if h.disk.level == "critical":
                reasons.append("free disk space is below the critical threshold (a graceful stop is under way)")
            if h.recorder.reported_loss_lines:
                reasons.append("the recorder reported dropped or unflushed messages")
            if h.health_write_failures:
                reasons.append(f"the capture-health record could not be written {h.health_write_failures} time(s)")
            return Assessment(sup, rec, "degraded" if reasons else "healthy", tuple(reasons), age, stale)
        if sup == "dead":
            reasons.append("the record says the capture is running but no supervisor is attached: it ended without recording an outcome")
            if ident is IdentityMatch.SAME:
                rec = "orphaned"
                reasons.append(f"the recorder (pid {h.recorder.identity.pid}) is STILL RUNNING without a supervisor")
            return Assessment(sup, rec, "interrupted", tuple(reasons), age, stale)
        return Assessment(sup, rec, "degraded", ("supervisor liveness could not be determined",), age, stale)

    # ----- terminal states: the supervisor is expected to be gone ("exited", not "dead": that is normal)
    if sup == "dead":
        sup = "exited"
    rec = "not_running" if h.recorder.identity is not None and ident is not IdentityMatch.SAME else (
        "running" if ident is IdentityMatch.SAME else "not_started" if h.recorder.pid is None else "not_recorded")
    if ident is IdentityMatch.SAME:
        rec = "orphaned"
        reasons.append("the recorded recorder process is still running although the capture has ended")
    if h.state is CaptureState.STOPPED_CLEAN:
        if h.bag_finalization.appears_finalized is True:
            return Assessment(sup, rec, "finalized", tuple(reasons), age, False)
        return Assessment(sup, rec, "failed", tuple(reasons + [f"the recorder stopped cleanly but the bag is not confirmed finalized ({h.bag_finalization.basis})"]), age, False)
    if h.state in (CaptureState.STOPPED_DISK_CRITICAL, CaptureState.INTERRUPTED, CaptureState.EXITED_UNEXPECTEDLY,
                   CaptureState.STOP_TIMEOUT_KILLED):
        return Assessment(sup, rec, "interrupted", tuple(reasons + [f"the capture ended in state '{h.state.value}'"] + list(h.interruptions)), age, False)
    return Assessment(sup, rec, "failed", tuple(reasons + [f"the capture ended in state '{h.state.value}'"] + list(h.errors[:3])), age, False)
