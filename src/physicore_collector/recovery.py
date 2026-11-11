"""`capture recover`: settle a session whose supervisor vanished without recording an outcome.

When a supervisor is killed (SIGKILL, OOM, crash, power loss, reboot) its last record says
`running`. Recovery turns that into an explicit, persisted `interrupted` state, so nobody has to
re-derive it on every read, and finds recorders that survived their supervisor.

Rules (each tested):
  * It acts only on a session that is `starting`/`running` AND has no live supervisor: the
    session flock is acquired and HELD for the whole operation, which also keeps a late
    supervisor from adopting the session halfway through.
  * It is idempotent: a settled session is reported "already final" and no byte is written.
  * It is READ-ONLY with respect to evidence: the bag is only inspected, never edited, moved,
    repaired or deleted; `evidence_modified` is literally `false` in the record.
  * It never invents facts: the end time stays unknown (`end_unix_ns` is not set to "now"),
    finalization is judged by the same rules as at capture end, and reboot is reported as
    true/false/unknown from boot ids.
  * Processes are signalled only after an IDENTITY match (pid + start time + boot id) and only
    when explicitly asked (`stop_orphan=True`): a reused pid is never touched.
"""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .capture_health import (
    ACTIVE_STATES, CaptureEvent, CaptureHealth, CaptureState, Reconciliation, load_health,
)
from .errors import HealthError
from .liveness import STARTING_GRACE_SECONDS, _ident
from .procident import IdentityMatch, check_identity, read_boot_id, signal_if_same
from .sessionlock import FileLock, LockHeldError
from .supervisor import BAG_DIR_NAME, HEALTH_NAME, SESSION_LOCK_NAME, inspect_output, write_health_atomic

ORPHAN_KILL_WAIT_SECONDS = 5.0
POLL_SECONDS = 0.1


@dataclass(frozen=True)
class RecoveryResult:
    session_dir: Path
    # reconciled | already_final | supervisor_alive | starting | orphan_left_running | not_a_session | error
    outcome: str
    state_before: str | None
    state_after: str | None
    detail: str
    orphan_action: str = "none"

    @property
    def changed(self) -> bool:
        return self.outcome == "reconciled"


def _wait_gone(health: CaptureHealth, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check_identity(_ident(health.recorder.identity)) is not IdentityMatch.SAME:
            return True
        time.sleep(POLL_SECONDS)
    return check_identity(_ident(health.recorder.identity)) is not IdentityMatch.SAME


def recover_session(session_dir: str | Path, *, stop_orphan: bool = False, now_ns: int | None = None) -> RecoveryResult:
    sdir = Path(session_dir)
    sidecar = sdir / HEALTH_NAME
    if not sidecar.is_file():
        return RecoveryResult(sdir, "not_a_session", None, None, f"no {HEALTH_NAME} in '{sdir}': not a capture session")
    try:
        health = load_health(sidecar).health
    except HealthError as e:
        return RecoveryResult(sdir, "error", None, None, f"capture-health record unusable, left untouched: {e.message}")
    before = health.state.value
    if health.state not in ACTIVE_STATES:
        return RecoveryResult(sdir, "already_final", before, before, f"the session already ended in state '{before}'; nothing to reconcile")

    lock = FileLock(sdir / SESSION_LOCK_NAME)
    try:
        lock.acquire(grace=0.3)
    except LockHeldError:
        return RecoveryResult(sdir, "supervisor_alive", before, before, "a supervisor is attached and alive; the session is not stale")
    try:
        # Re-read under the lock: the supervisor may have finished between the first read and now.
        health = load_health(sidecar).health
        before = health.state.value
        if health.state not in ACTIVE_STATES:
            return RecoveryResult(sdir, "already_final", before, before, f"the session already ended in state '{before}'; nothing to reconcile")
        now = now_ns if now_ns is not None else time.time_ns()
        if health.state is CaptureState.STARTING:
            started = health.timing.start_unix_ns
            if started is not None and (now - started) / 1e9 < STARTING_GRACE_SECONDS:
                return RecoveryResult(sdir, "starting", before, before,
                                      "the session was prepared moments ago; its supervisor may still be taking over")

        sup_res = check_identity(_ident(health.supervisor.identity)).value if health.supervisor.identity else "unknown"
        rec_match = check_identity(_ident(health.recorder.identity)) if health.recorder.identity else None
        rec_res = rec_match.value if rec_match is not None else "not_recorded"
        orphan_action = "none"
        if rec_match is IdentityMatch.SAME:
            pid = health.recorder.identity.pid  # type: ignore[union-attr]
            if not stop_orphan:
                return RecoveryResult(
                    sdir, "orphan_left_running", before, before,
                    f"the recorder (pid {pid}) is still running without a supervisor. Nothing was changed. Re-run with --stop-orphan "
                    "to stop it gracefully (it is killed only if it does not stop in time).", "left_running")
            signalled = signal_if_same(_ident(health.recorder.identity), signal.SIGINT, group=True)
            if not signalled:
                return RecoveryResult(sdir, "orphan_left_running", before, before,
                                      f"the recorder (pid {pid}) could not be signalled safely (identity changed or it is not a "
                                      "process-group leader); left running and untouched", "left_running")
            if _wait_gone(health, health.requested.stop_timeout_seconds):
                orphan_action = "stopped_gracefully"
            else:
                signal_if_same(_ident(health.recorder.identity), signal.SIGKILL, group=True)
                _wait_gone(health, ORPHAN_KILL_WAIT_SECONDS)
                orphan_action = "killed_after_timeout"

        res = inspect_output(sdir / BAG_DIR_NAME, CaptureState.INTERRUPTED)
        boot_now, boot_then = read_boot_id(), (health.supervisor.identity.boot_id if health.supervisor.identity else None)
        rebooted = None if boot_now is None or boot_then is None else boot_now != boot_then
        reason = ("the supervisor ended without recording an outcome (killed, crashed, out of memory, power loss or reboot)"
                  if health.state is CaptureState.RUNNING else
                  "the detached supervisor never took over the prepared session")
        if rebooted:
            reason += "; the machine has rebooted since the capture started"
        data = health.model_dump(mode="json")
        data["state"] = CaptureState.INTERRUPTED.value
        data["interruptions"] = [*data["interruptions"], reason]
        if orphan_action != "none":
            data["interruptions"].append(f"the surviving recorder was {orphan_action.replace('_', ' ')} by `capture recover`")
        data["errors"] = [*data["errors"], *res.errors]
        data["output"] = res.output.model_dump(mode="json")
        data["bag_finalization"] = res.finalization.model_dump(mode="json")
        data["timing"]["last_state_update_unix_ns"] = now
        # The end time is NOT known; it is deliberately left as it was (None) rather than set to "now".
        events = list(data["events"])
        events.append(CaptureEvent(seq=len(events), kind="reconciled", wall_clock_unix_ns=now,
                                   monotonic_ns=0,  # a monotonic reading from this process would not be comparable
                                   detail=reason).model_dump(mode="json"))
        if orphan_action != "none":
            events.append(CaptureEvent(seq=len(events), kind="orphan_recorder_stopped", wall_clock_unix_ns=now, monotonic_ns=0,
                                       detail=orphan_action).model_dump(mode="json"))
        data["events"] = events
        data["reconciliation"] = Reconciliation(
            reconciled_unix_ns=now, previous_state=health.state, reason=reason, rebooted_since_capture_start=rebooted,
            supervisor_identity_result=sup_res, recorder_identity_result=rec_res,
            orphan_recorder_action=orphan_action).model_dump(mode="json")
        write_health_atomic(sidecar, CaptureHealth.model_validate(data))
        return RecoveryResult(sdir, "reconciled", before, CaptureState.INTERRUPTED.value, reason, orphan_action)
    finally:
        lock.release()


def list_sessions(out_root: str | Path) -> list[Path]:
    """Session directories (those holding a capture-health record) directly under `out_root`."""
    root = Path(out_root)
    if not root.is_dir():
        return []
    return [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith(".") and (d / HEALTH_NAME).is_file()]


def recover_all(out_root: str | Path, *, stop_orphan: bool = False) -> list[RecoveryResult]:
    return [recover_session(d, stop_orphan=stop_orphan) for d in list_sessions(out_root)]
