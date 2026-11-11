"""Typer CLI: parse arguments, call library code, print, set exit code. Nothing else.

Exit codes (documented contract):
  0  success. For validate/export/inspect this means: fully readable AND status `complete`.
  1  the command ran, but the recording/session did not pass: status `partial` or `failed`
     (`export` still writes the package in that case, labelled with its true status);
     `session inspect` returns 1 for a partially readable recording.
  2  usage, input, or runtime error (bad/missing file, invalid profile, unreadable recording
     for `inspect`, export target exists, ...).

`incident prepare` follows the same contract: 0 = package prepared and the evidence is not known to be adverse
  (`success`), 1 = package prepared and verified but the evidence is partial/failed/interrupted/changed
  (`degraded`), 2 = no verified package could be produced (`failure`; nothing is published, the source is untouched),
  3 = DEFERRED on purpose so as not to compete with a recording (nothing published, nothing wrong; retry when idle).

Expected failures (`PhysiCoreError`) go to stderr as `ERROR: ...`. Unexpected exceptions are
NOT caught: they surface as a traceback rather than being hidden.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .bag import scan_bag
from .capture_health import CLEAN_STATES, CaptureState, LoadedHealth, load_health
from .errors import CaptureConfigError, InputError, PhysiCoreError
from .export import MANIFEST_NAME, ensure_target_available, export_package, verify_package
from .inventory import exit_code as inventory_exit_code, inspect_evidence, inventory_json, render_inventory
from .incident import (
    DISCOVERY_TEXT, NEEDS_ATTENTION_CODES, AutoPrepareOutcome, auto_prepare_after_capture, defer_session, failure_info,
    list_incidents, list_pending, load_incident, prepare_incident, register_unprepared, render_incident, run_pending,
    terminable, verify_incident_detailed,
)
from .mcap_reader import scan_recording
from .profile import load_profile
from .recorder import Rosbag2CliAdapter
from .ros_env import load_ros_environment, parse_env_assignments
from .service import DEFAULT_DEST, EX_CONFIG, ServiceSpec, install_service, render_unit, uninstall_service, validate_spec
from .render import render_export, render_inspection, render_profile, render_validation
from .schemas import ArtifactReadability, Manifest, SessionStatus
from .session import CAPTURE_HEALTH_NAME, analyze_session, resolve_recording_input, session_json
from .liveness import Assessment, assess, supervisor_state
from .recovery import list_sessions, recover_all, recover_session
from .sessionlock import FileLock, LockHeldError
from .supervisor import (
    HEALTH_NAME, STOP_REQUEST_NAME, SUPERVISOR_LOG_NAME, CaptureSupervisor, StopRequest,
)


app = typer.Typer(
    name="physicore-collector",
    help="Local-first robotics evidence acquisition and packaging: offline MCAP / rosbag2 inspection, "
    "supervised rosbag2 capture, portable verified evidence packages.",
    no_args_is_help=True,
    add_completion=False,
)
profile_app = typer.Typer(help="Robot profile commands.", no_args_is_help=True)
session_app = typer.Typer(
    help="Inspect and validate existing recordings (an .mcap file or a rosbag2 bag directory).", no_args_is_help=True
)
capture_app = typer.Typer(help="Supervise a rosbag2 recording (requires ROS 2 at runtime).", no_args_is_help=True)
package_app = typer.Typer(help="Work with exported evidence packages (offline).", no_args_is_help=True)
incident_app = typer.Typer(
    help="Prepare, list and inspect investigation-ready incident packages from a capture or an existing recording (offline).",
    no_args_is_help=True,
)
app.add_typer(profile_app, name="profile")
app.add_typer(session_app, name="session")
app.add_typer(capture_app, name="capture")
app.add_typer(package_app, name="package")
app.add_typer(incident_app, name="incident")
evidence_app = typer.Typer(
    help="Read-only, machine-readable evidence inventory for a separate consumer (offline inspection; no diagnosis).",
    no_args_is_help=True,
)
app.add_typer(evidence_app, name="evidence")
pending_app = typer.Typer(
    help="Pending incident preparation: sessions that still need a package, and an idle-only command to drain them.",
    no_args_is_help=True,
)
incident_app.add_typer(pending_app, name="pending")


def _fail(error: PhysiCoreError) -> "typer.Exit":
    typer.echo(f"ERROR: {error}", err=True)
    return typer.Exit(code=error.exit_code)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"physicore-collector {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """PhysiCore Collector."""


@profile_app.command("validate")
def profile_validate(
    profile: Path = typer.Argument(..., help="Path to a robot profile (YAML or JSON)."),
) -> None:
    """Validate a versioned robot profile (YAML or JSON).

    Exit codes: 0 = valid, 2 = missing/unreadable/malformed/invalid profile.
    """
    try:
        loaded = load_profile(profile)
    except PhysiCoreError as e:
        raise _fail(e) from e
    typer.echo(render_profile(loaded))


@session_app.command("inspect")
def session_inspect(
    recording: Path = typer.Argument(..., help="Path to an .mcap file, a rosbag2 bag directory, or a capture session directory."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Show topics, message counts, and min/max log_time of a recording.

    For a rosbag2 bag directory every storage segment is inspected and cross-checked against
    metadata.yaml; missing, unreadable, unsupported or unaccounted files are reported, never
    skipped silently.

    Exit codes: 0 = fully readable, 1 = partially readable (truncated/malformed/incomplete bag;
    what could be read is shown), 2 = unreadable or inaccessible.
    """
    try:
        rec, _ = resolve_recording_input(recording)
        if rec.is_dir():
            scan, analysis = scan_bag(rec)
            insp, bag = scan.inspection, analysis.info
        else:
            insp, bag = scan_recording(rec).inspection, None
    except PhysiCoreError as e:
        raise _fail(e) from e
    if json_output:
        payload = insp.model_dump(mode="json")
        if bag is not None:
            payload = {**payload, "recording_kind": "rosbag2_directory", "bag": bag.model_dump(mode="json")}
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(render_inspection(insp, bag))
    if insp.readability is ArtifactReadability.UNREADABLE:
        raise typer.Exit(code=2)
    if insp.readability is ArtifactReadability.PARTIALLY_READABLE:
        raise typer.Exit(code=1)


@session_app.command("validate")
def session_validate(
    recording: Path = typer.Argument(..., help="Path to an .mcap file, a rosbag2 bag directory, or a capture session directory."),
    profile: Path = typer.Option(..., "--profile", help="Robot profile to validate against."),
    trigger: Optional[Path] = typer.Option(None, "--trigger", help="Optional trigger JSON file."),
    metadata: Optional[Path] = typer.Option(
        None, "--metadata", help="Optional metadata JSON file (also searched for required metadata keys)."
    ),
    health: Optional[Path] = typer.Option(
        None, "--health", help="Capture-health sidecar (capture_health.json) for a bag directory. "
        "Found automatically when a capture session directory is given."
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Validate a recording against a profile and report session status.

    Exit codes: 0 = complete, 1 = partial or failed, 2 = input/usage error.
    """
    try:
        rec, health_path = resolve_recording_input(recording, health)
        ctx = analyze_session(rec, profile, trigger_path=trigger, metadata_path=metadata, health_path=health_path)
    except PhysiCoreError as e:
        raise _fail(e) from e
    typer.echo(json.dumps(session_json(ctx), indent=2) if json_output else render_validation(ctx))
    if ctx.report.status is not SessionStatus.COMPLETE:
        raise typer.Exit(code=1)


@app.command("export")
def export(
    recording: Path = typer.Argument(..., help="Path to an .mcap file, a rosbag2 bag directory, or a capture session directory."),
    profile: Path = typer.Option(..., "--profile", help="Robot profile to validate against."),
    out: Path = typer.Option(..., "--out", help="Target package directory. Must not already exist."),
    metadata: Optional[Path] = typer.Option(None, "--metadata", help="Optional metadata JSON file."),
    trigger: Optional[Path] = typer.Option(None, "--trigger", help="Optional trigger JSON file."),
    health: Optional[Path] = typer.Option(
        None, "--health", help="Capture-health sidecar for a bag directory (automatic for a capture session directory)."
    ),
) -> None:
    """Export a portable evidence package. Never overwrites an existing target.

    The package is written with its true status even if that is partial or failed.
    Exit codes: 0 = package written, status complete; 1 = package written, status partial
    or failed; 2 = error (nothing is left behind; an existing target is never touched).
    """
    try:
        ensure_target_available(out)
        rec, health_path = resolve_recording_input(recording, health)
        ctx = analyze_session(rec, profile, trigger_path=trigger, metadata_path=metadata, health_path=health_path)
        result = export_package(ctx, out)
    except PhysiCoreError as e:
        raise _fail(e) from e
    typer.echo(render_export(result, ctx))
    if result.status is not SessionStatus.COMPLETE:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# package verify
# --------------------------------------------------------------------------


@package_app.command("verify")
def package_verify(
    package: Path = typer.Argument(..., help="An exported evidence package directory."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Re-check a package against its manifest, fully offline.

    Verifies that every listed file exists with the recorded size and SHA-256, that no unlisted
    or symlinked file is present, and that the manifest is internally consistent.
    A matching hash shows the bytes equal what was recorded at export time. It does NOT show the
    sensor data is true, nor that the recording is complete (see `known_gaps` in the manifest).

    Exit codes: 0 = verified, 1 = problems found, 2 = not a package directory.
    """
    if not package.is_dir():
        raise _fail(InputError(f"Package path is not a directory: '{package}'", hint="Pass the directory written by `export`."))
    problems = verify_package(package)
    status = None
    mpath = package / MANIFEST_NAME
    if mpath.is_file():
        try:
            m = Manifest.model_validate_json(mpath.read_bytes())
            status = (m.session_status.value, m.manifest_schema_version, len(m.artifacts))
        except ValueError:
            pass
    if json_output:
        typer.echo(json.dumps({"verified": not problems, "problems": problems,
                               "session_status": status[0] if status else None,
                               "manifest_schema_version": status[1] if status else None}, indent=2))
    elif problems:
        typer.echo(f"VERIFICATION FAILED: {len(problems)} problem(s) in '{package}'")
        for pr in problems:
            typer.echo(f"  - {pr}")
    else:
        typer.echo(f"VERIFIED: {status[2]} artifact(s) in '{package}' match manifest.json (SHA-256, sizes, no extra files).")
        typer.echo(f"Recorded session status: {status[0].upper()} (manifest schema {status[1]}).")
        typer.echo("A matching digest shows the bytes are unchanged since export; it does not show the data is true or the recording complete.")
    if problems:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# capture: run / start / status / stop / recover / list
# --------------------------------------------------------------------------


def _locate_sidecar(path: Path) -> Path:
    p = path / CAPTURE_HEALTH_NAME if path.is_dir() else path
    if not p.is_file():
        raise InputError(
            f"No capture-health record found at '{p}'.",
            hint="Pass a capture session directory (as written by `capture run`/`capture start`) or its capture_health.json.",
        )
    return p


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    return f"{seconds:.0f} s ago"


def _render_health(lh: LoadedHealth, a: Assessment | None) -> str:
    h = lh.health
    fin = {True: "yes", False: "NO", None: "unknown"}[h.bag_finalization.appears_finalized]
    lines = [f"Capture session: {h.session_id}", f"  state: {h.state.value.upper()}"]
    if a is not None:
        lines.append(f"  assessment: {a.capture.upper()}"
                     + (f" ({'; '.join(a.reasons)})" if a.reasons and a.capture not in ("interrupted",) else ""))
        lines.append(f"  supervisor: {a.supervisor}" + (f" (pid {h.supervisor.pid})" if a.supervisor == "alive" else "")
                     + f"; recorder: {a.recorder}; last heartbeat: {_age(a.heartbeat_age_seconds)}"
                     + (f"; launched as {h.supervisor.launch_mode}" if h.supervisor.launch_mode else ""))
    if h.state in (CaptureState.RUNNING, CaptureState.STARTING):
        if a is not None and a.supervisor == "alive":
            lines.append(f"  the supervisor (pid {h.supervisor.pid}) is running")
        else:
            lines.append(f"  INTERRUPTED: the record says '{h.state.value}' but the supervisor (pid {h.supervisor.pid}) is gone and never recorded "
                         "an outcome. The recording is known to be unfinished; how far it got is unknown.")
            if a is not None and a.recorder == "orphaned":
                lines.append(f"  ORPHANED RECORDER: process {h.recorder.identity.pid} is still recording without a supervisor. "
                             "Stop it safely with `capture recover --stop-orphan`.")
            lines.append("  Settle the record with `capture recover` (read-only for the recording).")
    lines.append(f"  recorder: {h.recorder.adapter}, exit code {h.recorder.exit_code if h.recorder.exit_code is not None else 'unknown'}"
                 + (f", killed by signal {h.recorder.terminated_by_signal}" if h.recorder.terminated_by_signal else ""))
    lines.append(f"  bag appears finalized: {fin} ({h.bag_finalization.basis})")
    o = h.output
    if o.inspected:
        lines.append(f"  output: {o.segment_count if o.segment_count is not None else 0} segment(s), "
                     f"readability {o.readability.value if o.readability else 'n/a'}, metadata.yaml "
                     f"{'present' if o.metadata_present else 'MISSING' if o.metadata_present is False else 'unknown'}")
    d = h.disk
    if d.free_bytes is not None:
        lines.append(f"  disk: {d.level}, {d.free_bytes} bytes free (lowest seen {d.lowest_free_bytes})")
    if h.timing.stop_reason:
        lines.append(f"  stop reason: {h.timing.stop_reason}")
    if h.reconciliation is not None:
        r = h.reconciliation
        lines.append(f"  reconciled by `capture recover`: {r.reason} (rebooted since start: "
                     f"{'unknown' if r.rebooted_since_capture_start is None else 'yes' if r.rebooted_since_capture_start else 'no'}; "
                     f"orphan recorder: {r.orphan_recorder_action})")
    for label, items in (("error", h.errors), ("warning", h.warnings), ("interruption", h.interruptions)):
        lines += [f"  {label}: {x}" for x in items]
    if a is not None:
        lines.append(f"  evidence: {a.evidence}")
    lines.append("  recorder statistics: not available (rosbag2 exposes none); timestamps are the supervisor host clock, "
                 "not established comparable with MCAP log_time")
    return "\n".join(lines)


def _install_stop_handlers(stop: StopRequest):
    def handler(signum, frame):  # noqa: ARG001
        if stop.is_set():
            raise KeyboardInterrupt  # second signal: stop waiting, force-kill the recorder
        stop.request(f"signal {signal.Signals(signum).name}")

    return {sg: signal.signal(sg, handler) for sg in (signal.SIGINT, signal.SIGTERM)}


def _restore_handlers(old) -> None:
    for sg, h in old.items():
        signal.signal(sg, h)


def _capture_exit_ok(h) -> bool:
    return h.state in CLEAN_STATES and h.bag_finalization.appears_finalized is True


AUTO_PREPARE_OFF_NOTE = ("Automatic incident preparation is disabled (--no-auto-prepare). "
                         "Prepare one yourself with: physicore-collector incident prepare <session-dir>")


def _run_auto_prepare(session_dir: Path) -> AutoPrepareOutcome:
    """Prepare `<session>/incident` after the capture ended. Never raises, never changes the capture's exit
    code (a preparation problem must not make a recording look failed, nor trigger a service restart)."""
    out = auto_prepare_after_capture(session_dir, progress=lambda m: typer.echo(f"  {m}", err=True))
    if out.state in ("prepared", "already_prepared"):
        assert out.record is not None
        typer.echo(f"Incident package {out.state.replace('_', ' ')}: {out.record.outcome.upper()} -> {out.incident_dir}")
        typer.echo(f"  inspect: physicore-collector incident show {out.incident_dir}")
    elif out.state == "skipped":
        typer.echo(out.message)
    elif out.state == "deferred":
        typer.echo(f"Incident preparation DEFERRED ({out.code}): {out.message}")
        typer.echo(f"  Not an error and no incident exists yet. See: physicore-collector incident pending list {session_dir.parent}\n"
                   f"  Prepare later, when no capture is active: physicore-collector incident pending run {session_dir.parent}")
    else:
        typer.echo(f"ERROR: automatic incident preparation FAILED ({out.code}): {out.message}", err=True)
        if out.hint:
            typer.echo(f"  Hint: {out.hint}", err=True)
        typer.echo(f"  The recording and the capture record are untouched. Retry safely with: "
                   f"physicore-collector incident prepare {session_dir}", err=True)
    return out


@capture_app.command("run")
def capture_run(
    profile: Path = typer.Argument(..., help="Profile (schema 1.0 to 1.2). A `capture:` section needs schema 1.1 or later."),
    out: Path = typer.Option(..., "--out", help="EXISTING directory in which the new capture session directory is created."),
    duration: Optional[float] = typer.Option(None, "--duration", help="Stop automatically after this many seconds. Default: until Ctrl+C / `capture stop`."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Session directory name (default: UTC timestamp + random suffix)."),
    no_auto_prepare: bool = typer.Option(False, "--no-auto-prepare", help="Do NOT prepare an incident package when the capture ends."),
) -> None:
    """Record with rosbag2 (`ros2 bag record`) under supervision, in the FOREGROUND.

    Writes <out>/<session>/bag/ (made by rosbag2) and <out>/<session>/capture_health.json. Ctrl+C
    (or SIGTERM, or `capture stop`) requests a graceful stop; the recorder is killed if it does
    not exit within capture.stop_timeout_seconds. Nothing existing is ever overwritten. The
    capture ends if this terminal closes; use `capture start` for a detached recording.

    When the capture has ended and its final record is written, an incident package is prepared
    automatically in <session>/incident (see `incident prepare`; disable with --no-auto-prepare). A
    preparation problem is reported but never changes this command's exit code.

    Exit codes: 0 = clean stop and a fully readable bag, 1 = recording exists but was not clean or
    is not fully readable, 2 = nothing was recorded (invalid profile, ros2 missing, ...).
    """
    try:
        loaded = load_profile(profile)
        stop = StopRequest()
        old = _install_stop_handlers(stop)
        try:
            result = CaptureSupervisor(Rosbag2CliAdapter()).run(
                loaded, out, duration_seconds=duration, stop_event=stop, session_id=session_id
            )
        finally:
            _restore_handlers(old)
    except PhysiCoreError as e:
        raise _fail(e) from e
    h = result.health
    typer.echo(_render_health(LoadedHealth(h, b"", "", result.health_path), None))
    typer.echo(f"  session directory: {result.session_dir}")
    if h.state is CaptureState.FAILED_TO_START:
        typer.echo(f"ERROR: {h.errors[0] if h.errors else 'recorder failed to start'}", err=True)
        raise typer.Exit(code=2)
    typer.echo("Next: physicore-collector session validate " + f"{result.session_dir} --profile {profile}")
    typer.echo("      physicore-collector export " + f"{result.session_dir} --profile {profile} --out <new-package-dir>")
    if not result.record_persisted:
        typer.echo("ERROR: the final capture record could not be written to disk (see above); the file on disk is stale.", err=True)
        typer.echo("No incident was prepared (the on-disk record is stale). Run `capture recover`, then `incident prepare`.", err=True)
        raise typer.Exit(code=1)
    if no_auto_prepare:
        typer.echo(AUTO_PREPARE_OFF_NOTE)
    else:
        _run_auto_prepare(result.session_dir)
    if not _capture_exit_ok(h):
        raise typer.Exit(code=1)


@capture_app.command("start")
def capture_start(
    profile: Path = typer.Argument(..., help="Profile (schema 1.0 to 1.2)."),
    out: Path = typer.Option(..., "--out", help="EXISTING directory in which the new capture session directory is created."),
    duration: Optional[float] = typer.Option(None, "--duration", help="Stop automatically after this many seconds."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Session directory name (default: UTC timestamp + random suffix)."),
    wait: float = typer.Option(15.0, "--wait", help="Seconds to wait for the detached supervisor to take over and start the recorder."),
    no_auto_prepare: bool = typer.Option(False, "--no-auto-prepare", help="Do NOT prepare an incident package when the capture ends."),
) -> None:
    """Start a capture DETACHED from this terminal and return once it is recording.

    The supervisor runs in its own session with no controlling terminal, so closing the terminal
    or SSH connection does not stop it (but a host that kills all of a user's processes at logout,
    e.g. systemd-logind with KillUserProcesses=yes, still can: use the systemd service for
    unattended recording). Stop it with `capture stop`; inspect it with `capture status`.

    When the capture ends, the detached supervisor prepares an incident package in <session>/incident
    after releasing its locks (`capture stop` returns before that finishes; see `incident list`).
    Disable with --no-auto-prepare.

    Exit codes: 0 = recording, 1 = prepared but the supervisor did not take over within --wait,
    2 = could not start (invalid profile/settings, ros2 missing, another capture active, ...).
    """
    try:
        loaded = load_profile(profile)
        sup = CaptureSupervisor(Rosbag2CliAdapter())
        prepared = sup.prepare(loaded, out, duration_seconds=duration, session_id=session_id, launch_mode="detached")
        with open(prepared.session_dir / SUPERVISOR_LOG_NAME, "ab") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "physicore_collector", "capture", "_supervise", str(prepared.session_dir),
                 *(["--no-auto-prepare"] if no_auto_prepare else [])],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True,
            )
        path = prepared.session_dir / HEALTH_NAME
        deadline = time.monotonic() + wait
        lh = load_health(path)
        while lh.health.state is CaptureState.STARTING and time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
            lh = load_health(path)
    except PhysiCoreError as e:
        raise _fail(e) from e
    h = lh.health
    if h.state is CaptureState.STARTING:
        late = proc.poll() is not None
        typer.echo(f"ERROR: the supervisor {'exited' if late else 'did not take over'} before the recorder started"
                   f" (see {prepared.session_dir / SUPERVISOR_LOG_NAME}); session '{h.session_id}' is left as evidence.", err=True)
        raise typer.Exit(code=1 if not late else 2)
    if h.state is not CaptureState.RUNNING:
        typer.echo(_render_health(lh, assess(h, prepared.session_dir)))
        typer.echo(f"ERROR: {h.errors[0] if h.errors else 'the capture did not start'}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"Capture started: session {h.session_id}")
    typer.echo(f"  session directory: {prepared.session_dir}")
    typer.echo(f"  supervisor pid {h.supervisor.pid}, recorder pid {h.recorder.pid}; log: {prepared.session_dir / SUPERVISOR_LOG_NAME}")
    typer.echo(f"Status: physicore-collector capture status {prepared.session_dir}")
    typer.echo(f"Stop:   physicore-collector capture stop {prepared.session_dir}")
    typer.echo("Incident package after stop: " + ("disabled (--no-auto-prepare)" if no_auto_prepare
               else f"{prepared.session_dir / 'incident'} (automatic; see `physicore-collector incident list {out}`)"))


@capture_app.command("_supervise", hidden=True)
def capture_supervise(
    session: Path = typer.Argument(...),
    no_auto_prepare: bool = typer.Option(False, "--no-auto-prepare", hidden=True),
) -> None:
    """INTERNAL: the detached supervisor process started by `capture start`."""
    stop = StopRequest()
    old = _install_stop_handlers(stop)
    try:
        result = CaptureSupervisor(Rosbag2CliAdapter()).adopt(session, stop_event=stop)
    except PhysiCoreError as e:
        print(f"ERROR: {e.message}", file=sys.stderr, flush=True)
        raise typer.Exit(code=2) from e
    finally:
        _restore_handlers(old)
    # The capture is over and its locks are released: only now is the incident prepared, so preparation can never
    # delay a stop or the next capture. Its outcome never changes this process's exit code.
    if result.health.state is not CaptureState.FAILED_TO_START:
        if no_auto_prepare:
            typer.echo(AUTO_PREPARE_OFF_NOTE)
        elif result.record_persisted:
            _run_auto_prepare(result.session_dir)
        else:
            typer.echo("No incident was prepared: the final capture record could not be written. Run `capture recover`, "
                       "then `incident prepare`.")
    raise typer.Exit(code=0 if (_capture_exit_ok(result.health) and result.record_persisted) else 1)


@capture_app.command("status")
def capture_status(
    session: Path = typer.Argument(..., help="Capture session directory (or its capture_health.json)."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Show the state of a capture. Read-only: it never changes the record or touches the recording.

    Three things are reported separately: the supervisor (is one attached?), the recorder (is the
    recorded process still running?) and the capture (healthy / degraded / interrupted /
    finalized / failed). A record that says 'running' without an attached supervisor is
    INTERRUPTED, never 'running' or 'complete'. Status does not validate the recording.

    Exit codes: 0 = healthy, or finalized; 1 = degraded, interrupted, failed or ended unclean;
    2 = no record.
    """
    try:
        path = _locate_sidecar(session)
        lh = load_health(path)
    except PhysiCoreError as e:
        raise _fail(e) from e
    h = lh.health
    a = assess(h, path.parent)
    if json_output:
        payload = h.model_dump(mode="json")
        payload["assessment"] = {"supervisor": a.supervisor, "recorder": a.recorder, "capture": a.capture,
                                 "reasons": list(a.reasons), "heartbeat_age_seconds": a.heartbeat_age_seconds,
                                 "stale_heartbeat": a.stale_heartbeat, "evidence": a.evidence}
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(_render_health(lh, a))
    if a.capture not in ("healthy", "finalized", "starting"):
        raise typer.Exit(code=1)


@capture_app.command("stop")
def capture_stop(
    session: Path = typer.Argument(..., help="Capture session directory."),
    wait: float = typer.Option(30.0, "--wait", help="Seconds to wait for the capture to finish after the request."),
    reason: str = typer.Option("capture stop", "--reason", help="Recorded as the stop reason."),
) -> None:
    """Ask a running capture to stop gracefully and wait for the outcome.

    The request is a file (<session>/stop_request.json) that the supervisor polls, so it works
    for foreground, detached and service-managed captures alike, needs no signal, and cannot hit
    an unrelated process. Repeating the command is safe. If the supervisor is gone, nothing is
    signalled: use `capture recover`.

    Exit codes: 0 = stopped cleanly, 1 = stopped unclean / still running after --wait / not running,
    2 = no capture record.
    """
    try:
        path = _locate_sidecar(session)
        h = load_health(path).health
    except PhysiCoreError as e:
        raise _fail(e) from e
    sdir = path.parent
    if h.state not in (CaptureState.RUNNING, CaptureState.STARTING):
        typer.echo(f"Nothing to stop: the capture already ended in state '{h.state.value}'.")
        raise typer.Exit(code=0 if h.state in CLEAN_STATES else 1)
    a = assess(h, sdir)
    if a.supervisor != "alive":
        typer.echo(f"ERROR: the supervisor (pid {h.supervisor.pid}) is not running; the capture was interrupted.", err=True)
        if a.recorder == "orphaned":
            typer.echo("The recorder is still running without a supervisor: use `capture recover --stop-orphan`.", err=True)
        else:
            typer.echo("Settle the record with `capture recover`.", err=True)
        raise typer.Exit(code=1)
    marker = sdir / STOP_REQUEST_NAME
    try:
        with open(marker, "x") as f:
            json.dump({"requested_unix_ns": time.time_ns(), "reason": reason[:200], "requested_by_pid": os.getpid()}, f)
    except FileExistsError:
        pass  # a stop was already requested: idempotent
    except OSError as e:
        raise _fail(InputError(f"Cannot write the stop request '{marker}': {e.strerror or e}")) from e
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            cur = load_health(path)
        except PhysiCoreError:
            time.sleep(0.1)
            continue
        if cur.health.state not in (CaptureState.RUNNING, CaptureState.STARTING):
            # "Stopped" means the supervisor has also let go of its locks, so that a script may
            # safely start another capture or clean up right after this command returns.
            while supervisor_state(sdir) == "alive" and time.monotonic() < deadline:
                time.sleep(0.05)
            typer.echo(_render_health(cur, assess(cur.health, sdir)))
            raise typer.Exit(code=0 if _capture_exit_ok(cur.health) else 1)
        time.sleep(0.1)
    typer.echo(f"ERROR: the capture did not finish within {wait:g} s of the stop request.", err=True)
    raise typer.Exit(code=1)


@capture_app.command("recover")
def capture_recover(
    path: Path = typer.Argument(..., help="A capture session directory, or (with --all) the output directory holding sessions."),
    all_sessions: bool = typer.Option(False, "--all", help="Settle every stale session directly under PATH."),
    stop_orphan: bool = typer.Option(False, "--stop-orphan", help="Also stop a recorder that outlived its supervisor "
                                     "(graceful first; killed only if it does not stop in time). Only a process whose "
                                     "recorded identity still matches is ever signalled."),
) -> None:
    """Settle sessions whose supervisor vanished (killed, crashed, power loss, reboot).

    Writes an explicit `interrupted` state into the capture-health record. It never edits, moves
    or deletes the recording, never invents an end time, and is idempotent: running it again changes
    nothing. A live supervisor is never disturbed.

    Exit codes: 0 = everything settled or nothing to do, 1 = a surviving recorder needs attention
    (re-run with --stop-orphan), 2 = not a capture session / unusable record.
    """
    results = recover_all(path, stop_orphan=stop_orphan) if all_sessions else [recover_session(path, stop_orphan=stop_orphan)]
    if all_sessions and not results:
        typer.echo(f"No capture sessions found under '{path}'.")
    worst = 0
    for r in results:
        typer.echo(f"{r.session_dir.name}: {r.outcome.upper().replace('_', ' ')}"
                   + (f" ({r.state_before} -> {r.state_after})" if r.outcome == "reconciled" else "") + f": {r.detail}")
        if r.orphan_action not in ("none", "left_running"):
            typer.echo(f"  orphan recorder: {r.orphan_action.replace('_', ' ')}")
        worst = max(worst, 2 if r.outcome in ("not_a_session", "error") else 1 if r.outcome == "orphan_left_running" else 0)
    if worst:
        raise typer.Exit(code=worst)


@capture_app.command("list")
def capture_list(
    out: Path = typer.Argument(..., help="Output directory holding capture sessions."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """List capture sessions under an output directory with their assessed state. Read-only."""
    rows = []
    for d in list_sessions(out):
        try:
            h = load_health(d / HEALTH_NAME).health
        except PhysiCoreError as e:
            rows.append({"session": d.name, "state": "unreadable", "capture": "unknown", "supervisor": "unknown",
                         "appears_finalized": None, "detail": e.message})
            continue
        a = assess(h, d)
        rows.append({"session": d.name, "state": h.state.value, "capture": a.capture, "supervisor": a.supervisor,
                     "recorder": a.recorder, "appears_finalized": h.bag_finalization.appears_finalized, "detail": "; ".join(a.reasons)})
    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo(f"No capture sessions found under '{out}'.")
    for r in rows:
        fin = {True: "finalized", False: "NOT finalized", None: "finalization unknown"}[r["appears_finalized"]]
        typer.echo(f"{r['session']}  state={r['state']}  capture={r['capture']}  supervisor={r['supervisor']}  {fin}")


# --------------------------------------------------------------------------
# unattended capture: service-run / install-service / uninstall-service
# --------------------------------------------------------------------------


@capture_app.command("service-run")
def capture_service_run(
    name: str = typer.Option(..., "--name", help="Service name (as given to install-service)."),
    profile: Path = typer.Option(..., "--profile", help="Profile (schema 1.0 to 1.2)."),
    out: Path = typer.Option(..., "--out", help="EXISTING output directory (one active capture per directory)."),
    ros_setup: list[str] = typer.Option([], "--ros-setup", help="ROS setup script to source (repeatable, in order)."),
    env: list[str] = typer.Option([], "--env", help="KEY=VALUE to forward to the recorder (allow-listed ROS variables only)."),
    no_auto_prepare: bool = typer.Option(False, "--no-auto-prepare", help="Do NOT prepare an incident package when the capture ends."),
) -> None:
    """What the systemd unit runs: reconcile, then record until told to stop.

    1. takes a per-name lock (a second instance exits at once);
    2. builds the ROS environment from --ros-setup scripts (no shell interpolation);
    3. settles stale sessions in the output directory (`capture recover --all --stop-orphan`:
       only recorders whose recorded identity still matches are ever signalled);
    4. records ONE new session (never resumes or overwrites one) until SIGTERM/SIGINT;
    5. NEVER prepares an incident itself: a stop or restart must not wait for copying, and a copy would compete
       with the next recording. Every ended session is recorded as DEFERRED (service mode) in
       <session>/incident_attempt.json; drain them with `incident pending run <out>` when the machine is idle
       (cron, a systemd timer, or by hand). Stale sessions found at start are recorded the same way.

    Exit codes: 0 = clean stop with a finalized bag; 1 = ended unclean (systemd restarts it, rate
    limited); 78 = configuration problem or another capture active (NOT restarted).
    """
    lock = FileLock(out / f".service-{name}.lock")
    try:
        loaded = load_profile(profile)
        if not out.is_dir():
            raise CaptureConfigError(f"Output directory '{out}' does not exist.")
        try:
            lock.acquire()
        except LockHeldError as e:
            raise CaptureConfigError(f"Another service-run for '{name}' is already running against '{out}'.") from e
        extra = parse_env_assignments(list(env))
        if ros_setup:
            renv, exe = load_ros_environment(list(ros_setup), extra)
            adapter = Rosbag2CliAdapter(executable=exe, env=renv)
        else:
            adapter = Rosbag2CliAdapter(env={**os.environ, **extra} if extra else None)
        for r in recover_all(out, stop_orphan=True):
            if r.outcome != "already_final":
                typer.echo(f"startup reconciliation: {r.session_dir.name}: {r.outcome}: {r.detail}")
        try:  # stat-only bookkeeping: make finished-but-unprepared sessions visible; prepares NOTHING before recording starts
            for sd in register_unprepared(out):
                typer.echo(f"pending incident preparation recorded for {sd.name} (see `incident pending list {out}`)")
        except Exception as e:  # noqa: BLE001 - bookkeeping must never stop a recording from starting
            typer.echo(f"warning: could not record pending preparation: {e}", err=True)
        stop = StopRequest()
        old = _install_stop_handlers(stop)
        try:
            result = CaptureSupervisor(adapter).run(loaded, out, stop_event=stop, launch_mode="service", service_name=name)
        finally:
            _restore_handlers(old)
    except PhysiCoreError as e:
        typer.echo(f"ERROR: {e.message}" + (f" ({e.hint})" if e.hint else ""), err=True)
        raise typer.Exit(code=EX_CONFIG) from e
    finally:
        lock.release()
    h = result.health
    typer.echo(f"capture {h.session_id} ended: state {h.state.value}, finalized: {h.bag_finalization.appears_finalized}")
    if h.state is CaptureState.FAILED_TO_START:
        typer.echo(f"ERROR: {h.errors[0] if h.errors else 'recorder failed to start'}", err=True)
        raise typer.Exit(code=EX_CONFIG)
    clean = _capture_exit_ok(h) and result.record_persisted
    if no_auto_prepare:
        typer.echo(AUTO_PREPARE_OFF_NOTE)
    elif not result.record_persisted:
        typer.echo("No incident queued: the final capture record could not be written. Run `capture recover`, then `incident pending run`.")
    else:
        err = defer_session(result.session_dir, code="deferred_service_mode", scheduled_by="service",
                            message="service mode never prepares an incident inline: a stop or restart must not wait for copying, "
                                    "and the copy would compete with the next recording",
                            hint=f"run `physicore-collector incident pending run {out}` when no capture is active")
        typer.echo(f"Incident preparation DEFERRED (service mode): {result.session_dir.name} is queued. "
                   f"Prepare when idle: physicore-collector incident pending run {out}"
                   + (f"  [warning: {err}]" if err else ""))
    raise typer.Exit(code=0 if clean else 1)


def _service_spec(name, user, group, profile, out, ros_setup, env, restart, executable, auto_prepare=True) -> ServiceSpec:
    exe = executable or shutil.which("physicore-collector") or sys.argv[0]
    prof_abs = profile.resolve()
    cap = None
    try:
        cap = load_profile(prof_abs).profile.capture
    except PhysiCoreError:
        pass  # validate_spec reports the profile problem with its full message
    return ServiceSpec(
        name=name, user=user, group=group, executable=str(Path(exe).resolve()), profile=str(prof_abs), out=str(out.resolve()),
        ros_setups=tuple(ros_setup), env=parse_env_assignments(list(env)), restart=restart,
        stop_timeout_seconds=cap.stop_timeout_seconds if cap else 15.0, auto_prepare=auto_prepare,
    )


@capture_app.command("install-service")
def capture_install_service(
    name: str = typer.Option(..., "--name", help="Service name; the unit is physicore-collector-<name>.service."),
    user: str = typer.Option(..., "--user", help="Account the service runs as (required; root is refused unless --allow-root)."),
    profile: Path = typer.Option(..., "--profile", help="Profile to record with (validated now)."),
    out: Path = typer.Option(..., "--out", help="EXISTING output directory for capture sessions."),
    ros_setup: list[str] = typer.Option([], "--ros-setup", help="ROS setup script to source (repeatable). No distro is assumed."),
    env: list[str] = typer.Option([], "--env", help="KEY=VALUE to forward (allow-listed ROS variables only)."),
    group: Optional[str] = typer.Option(None, "--group"),
    restart: str = typer.Option("on-failure", "--restart", help="'on-failure' (rate limited; each restart is a NEW session) or 'no'."),
    executable: Optional[str] = typer.Option(None, "--executable", help="Absolute path of physicore-collector (default: the one on PATH)."),
    dest_dir: Path = typer.Option(DEFAULT_DEST, "--dest-dir", help="Where to write the unit file."),
    enable: bool = typer.Option(False, "--enable", help="Also enable at boot. WITHOUT this the service never starts by itself."),
    start_now: bool = typer.Option(False, "--start", help="Also start it now."),
    force: bool = typer.Option(False, "--force", help="Replace an existing unit written by this tool."),
    allow_root: bool = typer.Option(False, "--allow-root"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and print the unit; write nothing."),
    no_systemctl: bool = typer.Option(False, "--no-systemctl", help="Do not call systemctl (daemon-reload/enable/start)."),
    no_auto_prepare: bool = typer.Option(False, "--no-auto-prepare", help="Generate a unit that does NOT prepare incident packages after a clean stop."),
) -> None:
    """Install the systemd service for unattended capture. Needs root for /etc/systemd/system.

    Nothing is enabled or started unless you ask: installing is not opting in to recording at boot.
    Exit codes: 0 = installed (or dry run), 2 = refused (see message).
    """
    try:
        spec = _service_spec(name, user, group, profile, out, ros_setup, env, restart, executable, auto_prepare=not no_auto_prepare)
        if dry_run:
            warnings = validate_spec(spec, allow_root=allow_root)
            typer.echo(render_unit(spec))
            for w in warnings:
                typer.echo(f"warning: {w}", err=True)
            typer.echo("(dry run: nothing was written)", err=True)
            return
        res = install_service(spec, dest_dir, systemctl=not no_systemctl, enable=enable, start=start_now, force=force, allow_root=allow_root)
    except PhysiCoreError as e:
        raise _fail(e) from e
    typer.echo(f"Installed {res.unit_path}")
    for w in res.warnings:
        typer.echo(f"warning: {w}")
    for n in res.notes:
        typer.echo(f"  {n}")
    typer.echo(f"Start:  systemctl start physicore-collector-{name}     Status: systemctl status physicore-collector-{name}")
    typer.echo(f"Sessions: physicore-collector capture list {out}")


@capture_app.command("uninstall-service")
def capture_uninstall_service(
    name: str = typer.Option(..., "--name"),
    dest_dir: Path = typer.Option(DEFAULT_DEST, "--dest-dir"),
    no_systemctl: bool = typer.Option(False, "--no-systemctl"),
) -> None:
    """Stop (gracefully), disable and remove a service installed by `install-service`.

    Only a unit carrying this tool's marker is removed. Recordings are never touched.
    Exit codes: 0 = removed, 2 = refused / not installed.
    """
    try:
        notes = uninstall_service(name, dest_dir, systemctl=not no_systemctl)
    except PhysiCoreError as e:
        raise _fail(e) from e
    for n in notes:
        typer.echo(n)


# --------------------------------------------------------------------------
# incident: prepare / list / show
# --------------------------------------------------------------------------


def _progress(msg: str) -> None:
    typer.echo(f"  {msg}", err=True)  # stderr: stdout stays clean for --json


@incident_app.command("prepare")
def incident_prepare(
    source: Path = typer.Argument(..., help="An .mcap file, a rosbag2 directory, or a capture session directory. "
                                  "A segment listed by a neighbouring metadata.yaml prepares its whole bag."),
    profile: Optional[Path] = typer.Option(None, "--profile", help="Robot profile to judge the recording against. "
                                           "Optional for a capture session directory (its own copy is used)."),
    out: Optional[Path] = typer.Option(None, "--out", help="NEW incident directory (must not exist). "
                                       "Default for a capture session directory: <session>/incident."),
    metadata: Optional[Path] = typer.Option(None, "--metadata", help="Optional metadata JSON file."),
    trigger: Optional[Path] = typer.Option(None, "--trigger", help="Optional trigger JSON file."),
    health: Optional[Path] = typer.Option(None, "--health", help="Capture-health sidecar for a bag directory (automatic for a capture session)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover and check; report what would be copied and the space needed. Writes nothing."),
    guard_dir: list[Path] = typer.Option([], "--guard-dir", help="Also treat an active capture in this directory as a reason to defer "
                                         "(repeatable; $PHYSICORE_GUARD_DIRS is added). List every capture output directory that "
                                         "shares storage with the target."),
    allow_while_recording: bool = typer.Option(False, "--allow-while-recording", help="Prepare even if a capture is active in a guarded "
                                               "directory (it WILL compete with that recording for storage)."),
    max_mib_per_sec: Optional[float] = typer.Option(None, "--max-mib-per-sec", help="Throttle PhysiCore's own hash/copy loops to this "
                                                    "average rate (default: $PHYSICORE_PREPARE_MAX_MIB_PER_SEC, else unthrottled). "
                                                    "Reduces contention; does not guarantee a concurrent recording is safe."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Prepare an investigation-ready incident package from a capture or an EXISTING recording.

    Finds every segment and the metadata itself, orders the segments, runs the existing validation and
    integrity checks, and writes <incident>/package/ (the verified export package), incident.json (machine-readable
    result) and INCIDENT_SUMMARY.md. The package is a SELF-CONTAINED COPY: free space is checked before copying.
    The source is only read. An existing target is never overwritten; repeating the command on an unchanged source
    is safe (it reports 'already prepared' after re-verifying). A failed attempt publishes nothing: just retry.

    Four separate statements are reported: artifact integrity, capture health, evidence coverage, preparation.
    A valid checksum does not show nothing was lost; a missing capture-health record is UNKNOWN, not healthy.

    Scheduling: it will not compete with a recording. If a capture is active in a guarded directory (or the recorder of
    this session may still be writing), nothing is done and the exit code is 3 (deferred). While running it stops cleanly
    (staging removed) if a capture starts. Override on purpose with --allow-while-recording.

    Exit codes: 0 = prepared, evidence not known to be adverse (success); 1 = prepared and verified but the evidence is
    partial/failed/interrupted/changed (degraded); 2 = nothing was prepared (failure: bad input, capture still
    running, not enough space, checksum mismatch, target exists, ...); 3 = deferred so as not to compete with a recording.
    """
    try:
        with terminable():  # SIGTERM cancels cleanly (staging removed) instead of killing the process mid-copy
            res = prepare_incident(source, profile, out, metadata=metadata, trigger=trigger, health=health,
                                   dry_run=dry_run, progress=_progress, guard_dirs=list(guard_dir),
                                   respect_active_captures=not allow_while_recording, max_mib_per_sec=max_mib_per_sec)
    except (PhysiCoreError, KeyboardInterrupt, OSError) as e:
        code, msg, hint = failure_info(e)
        deferred = code.startswith("deferred_")
        if json_output:
            typer.echo(json.dumps({"result": "deferred" if deferred else "failure", "state": "deferred" if deferred else "failed",
                                   "error": {"code": code, "message": msg, "hint": hint}}, indent=2))
        typer.echo(("DEFERRED: " if deferred else "ERROR: ") + msg + (f"\nHint: {hint}" if hint else ""), err=True)
        raise typer.Exit(code=3 if deferred else 2) from e
    if res.state == "dry_run":
        pl = res.plan
        if json_output:
            typer.echo(json.dumps({"result": "dry_run", "state": "dry_run", "target": str(pl.target), "recording_kind": pl.recording_kind,
                                   "discovery": pl.discovery, "files": [{"path": p, "bytes": n} for p, n in pl.files],
                                   "copy_bytes": pl.copy_bytes, "reserve_bytes": pl.reserve_bytes, "required_bytes": pl.required_bytes,
                                   "free_bytes": pl.free_bytes, "sufficient_space": pl.free_bytes is None or pl.free_bytes >= pl.required_bytes}, indent=2))
        else:
            typer.echo(f"DRY RUN: nothing was written.\n  source: {pl.recording_kind.replace('_', ' ')} (found via {DISCOVERY_TEXT[pl.discovery]})")
            typer.echo(f"  would copy {len(pl.files)} file(s), {pl.copy_bytes} bytes (self-contained copy) into {pl.target}/package")
            typer.echo(f"  space needed: {pl.required_bytes} bytes (copy + {pl.reserve_bytes} reserve + overhead); free: "
                       f"{pl.free_bytes if pl.free_bytes is not None else 'unknown'}")
        if pl.free_bytes is not None and pl.free_bytes < pl.required_bytes:
            typer.echo("INSUFFICIENT SPACE: a real run would fail before copying anything.", err=True)
            raise typer.Exit(code=1)
        return
    rec = res.record
    assert rec is not None and res.incident_dir is not None
    if json_output:
        typer.echo(json.dumps({"result": rec.outcome, "state": res.state, "incident_dir": str(res.incident_dir),
                               "incident": rec.model_dump(mode="json"), "notes": list(res.notes)}, indent=2))
    else:
        if res.state == "already_prepared":
            typer.echo(f"ALREADY PREPARED: an identical, verified incident exists at '{res.incident_dir}'. Nothing was written.\n")
        typer.echo(render_incident(rec, path=res.incident_dir), nl=False)
        for n in res.notes:
            typer.echo(f"note: {n}")
    if rec.outcome != "success":
        raise typer.Exit(code=1)


@incident_app.command("list")
def incident_list(
    root: Path = typer.Argument(..., help="A directory holding capture sessions and/or incident directories (or one incident)."),
    verify: bool = typer.Option(False, "--verify", help="Re-hash every listed package NOW (reads every package byte; slow for large "
                                "ones). Rows become VERIFIED NOW, TAMPERED or UNVERIFIABLE."),
    allow_while_recording: bool = typer.Option(False, "--allow-while-recording", help="With --verify: verify even if a capture is active "
                                               "(the re-hash WILL compete with it; by default it is skipped and the row is UNVERIFIABLE)."),
    guard_dir: list[Path] = typer.Option([], "--guard-dir", help="With --verify: also check this directory for an active capture."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """List incidents, and capture sessions that have none yet. Read-only.

    A package row is PREVIOUSLY PREPARED unless --verify is given: that is the stored result of the day it was prepared,
    NOT evidence the package is intact now. The package state (PREVIOUSLY PREPARED / VERIFIED NOW / TAMPERED /
    UNVERIFIABLE) is independent of the evidence outcome (SUCCESS / DEGRADED): a verified package can hold degraded evidence.
    Sessions without a package are NOT PREPARED, DEFERRED (on purpose; not an error) or FAILED.

    Exit code: 0 = listed, 2 = not a directory."""
    try:
        rows = list_incidents(root, verify=verify, respect_active=not allow_while_recording, guard_dirs=list(guard_dir))
    except PhysiCoreError as e:
        raise _fail(e) from e
    if json_output:
        typer.echo(json.dumps([{"name": r.name, "path": str(r.path), "state": r.state, "outcome": r.outcome,
                                "session_status": r.session_status, "integrity_at_preparation": r.integrity_at_preparation,
                                "capture_health": r.capture_health, "evidence_coverage": r.evidence_coverage,
                                "created_at_utc": r.created_at_utc, "detail": r.detail} for r in rows], indent=2))
        return
    if not rows:
        typer.echo(f"No incidents or capture sessions found under '{root}'.")
    label = {"previously_prepared": "PREVIOUSLY PREPARED (not re-verified)", "verified_now": "VERIFIED NOW"}
    for r in rows:
        if r.outcome is not None:
            typer.echo(f"{r.name}  {label.get(r.state, r.state.upper())}  evidence={r.outcome.upper()}  validation={r.session_status}  "
                       f"at-preparation: integrity={r.integrity_at_preparation} capture={r.capture_health} coverage={r.evidence_coverage}  "
                       f"prepared={r.created_at_utc}" + (f"\n    {r.detail}" if r.detail else ""))
        else:
            typer.echo(f"{r.name}  {r.state.upper().replace('_', ' ')}  {r.detail or ''}")
    if not verify and any(r.state == "previously_prepared" for r in rows):
        typer.echo("note: PREVIOUSLY PREPARED rows are stored results, not a fresh check. Add --verify to re-hash the packages "
                   "(reads every package byte).")


@incident_app.command("show")
def incident_show(
    path: Path = typer.Argument(..., help="An incident directory, its incident.json, or a capture session directory."),
    verify: bool = typer.Option(False, "--verify", help="Also re-hash the whole package against its manifest (offline)."),
    allow_while_recording: bool = typer.Option(False, "--allow-while-recording", help="With --verify: verify even if a capture is active "
                                               "(the re-hash WILL compete with it)."),
    guard_dir: list[Path] = typer.Option([], "--guard-dir", help="With --verify: also check this directory for an active capture."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Show one incident: the four separate statements, what is not established, known gaps. Read-only.

    Without --verify the STORED result is shown and nothing is re-hashed: it says what was true when the incident was
    prepared, not that the package is intact now. --verify re-checks every package file, the manifest against the digest
    recorded in incident.json, and that nothing unexpected is present: VERIFIED NOW means the bytes are what was
    prepared; it does not show the data is true or the recording complete. With a capture active, --verify is skipped
    (UNVERIFIABLE) unless --allow-while-recording.

    Exit codes: 0 = shown (and verified now, if asked), 1 = --verify found TAMPERED, 2 = not a readable incident or
    UNVERIFIABLE, 3 = --verify deferred because a capture is active."""
    try:
        rec, d = load_incident(path)
    except PhysiCoreError as e:
        raise _fail(e) from e
    v = verify_incident_detailed(d, respect_active=not allow_while_recording, guard_dirs=list(guard_dir)) if verify else None
    if json_output:
        payload = {"incident_dir": str(d), "incident": rec.model_dump(mode="json"),
                   "verification": {"state": "not_checked"} if v is None else
                   {"state": v.state, "verified": v.state == "verified_now", "problems": list(v.problems), "deferred": v.deferred}}
        typer.echo(json.dumps(payload, indent=2))
    else:
        if v is None:
            typer.echo("[STORED RESULT: not re-verified now; use --verify to check the package against its manifest]\n")
        typer.echo(render_incident(rec, path=d), nl=False)
        if v is not None:
            if v.state == "verified_now":
                typer.echo("VERIFIED NOW: package files match manifest.json and incident.json (SHA-256, sizes, no unexpected entries).")
                typer.echo("A matching digest shows the bytes are unchanged since preparation; it does not show the data is true or the recording complete.")
            else:
                typer.echo(f"{v.state.upper()}: {len(v.problems)} problem(s)")
                for pr in v.problems:
                    typer.echo(f"  - {pr}")
    if v is not None and v.state != "verified_now":
        raise typer.Exit(code=1 if v.state == "tampered" else 3 if v.deferred else 2)


# --------------------------------------------------------------------------
# incident pending: list / run
# --------------------------------------------------------------------------


@pending_app.command("list")
def pending_list(
    out_dirs: list[Path] = typer.Argument(..., help="Capture output directory (or several) holding the session directories."),
    guard_dir: list[Path] = typer.Option([], "--guard-dir", help="Also check this directory for an active capture (repeatable)."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """List capture sessions that still need an incident package, whether each can proceed NOW, and what to do if not.

    Covers sessions whose preparation was deferred or failed, finished sessions nobody prepared, and stale `running`
    sessions left by a crash, power loss or reboot. Read-only: nothing is hashed, recovered, signalled or written.

    Exit code: 0 = listed, 2 = a path is not a directory."""
    try:
        items = list_pending(out_dirs, guard_dirs=list(guard_dir))
    except PhysiCoreError as e:
        raise _fail(e) from e
    if json_output:
        typer.echo(json.dumps([{"session": str(i.session_dir), "kind": i.kind, "capture_state": i.capture_state,
                                "capture_assessment": i.capture_assessment, "recorder": i.recorder, "attempt_count": i.attempt_count,
                                "last_code": i.last_code, "detail": i.detail, "blocked_by": i.blocked_by,
                                "capture_active": i.capture_active,
                                "retry_eligible": i.retry_eligible, "action": i.action} for i in items], indent=2))
        return
    if not items:
        typer.echo("Nothing pending.")
    for i in items:
        flags = (f"  BLOCKED({i.blocked_by})" if i.blocked_by else "") + ("  [a capture is active: nothing can run now]" if i.capture_active else "") \
            + ("" if i.retry_eligible else "  NEEDS ATTENTION")
        typer.echo(f"{i.session_dir.name}  {i.kind.upper().replace('_', ' ')}  capture={i.capture_state}"
                   f"{'/' + i.capture_assessment if i.capture_assessment else ''}  recorder={i.recorder}  attempts={i.attempt_count}{flags}")
        typer.echo(f"    {i.detail}\n    next: {i.action}")


@pending_app.command("run")
def pending_run(
    out_dirs: list[Path] = typer.Argument(..., help="Capture output directory (or several) holding the session directories."),
    recover: bool = typer.Option(True, "--recover/--no-recover", help="Reconcile stale `running` sessions first with the existing "
                                 "`capture recover` logic (idempotent). Default: on."),
    stop_orphan: bool = typer.Option(False, "--stop-orphan", help="If a stale session's recorder is still running, stop it gracefully via "
                                     "`capture recover` (only a process whose recorded identity still matches is signalled)."),
    max_items: Optional[int] = typer.Option(None, "--max", help="Prepare at most this many sessions in this run."),
    guard_dir: list[Path] = typer.Option([], "--guard-dir", help="Also check this directory for an active capture (repeatable)."),
    max_mib_per_sec: Optional[float] = typer.Option(None, "--max-mib-per-sec", help="Throttle PhysiCore's own hash/copy loops "
                                                    "(default: $PHYSICORE_PREPARE_MAX_MIB_PER_SEC, else unthrottled)."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Prepare pending incidents, oldest session first, ONLY while no capture is active. Run it when the machine is idle.

    This is a command, not a daemon: nothing runs it for you. Use it by hand, from cron, or from a systemd timer
    (docs/OPERATIONS.md has an example). A capture that starts while it runs makes it stop cleanly; the session being
    prepared is recorded as DEFERRED and nothing is published for it. Source recordings are never modified or deleted.

    Exit codes: 0 = nothing pending or everything pending was prepared (an interrupted or degraded capture still yields a
    DEGRADED incident: that is a result, not an error); 2 = at least one failure or a session that needs an operator;
    3 = something was deferred (a capture is active or a recorder may still be writing): retry when idle."""
    try:
        results = run_pending(out_dirs, guard_dirs=list(guard_dir), recover=recover, stop_orphan=stop_orphan, max_items=max_items,
                              max_mib_per_sec=max_mib_per_sec, progress=_progress)
    except (PhysiCoreError, KeyboardInterrupt) as e:
        code, msg, _h = failure_info(e)
        typer.echo(f"ERROR: {msg}", err=True)
        raise typer.Exit(code=2) from e
    if json_output:
        typer.echo(json.dumps([{"session": str(r.session_dir), "action": r.action, "detail": r.detail, "code": r.code,
                                "outcome": r.outcome, "reconciled": r.reconciled} for r in results], indent=2))
    else:
        if not results:
            typer.echo("Nothing pending.")
        for r in results:
            typer.echo(f"{r.session_dir.name}  {r.action.upper().replace('_', ' ')}"
                       + (f" ({r.outcome.upper()})" if r.outcome else "") + f"  {r.detail}"
                       + (f"  [reconciled: {r.reconciled}]" if r.reconciled else ""))
    failed = any(r.action == "failed" or (r.action == "skipped" and r.code in NEEDS_ATTENTION_CODES) for r in results)
    deferred = any(r.action == "deferred" or (r.action == "skipped" and r.code not in (None, "max_items") and r.code not in NEEDS_ATTENTION_CODES)
                   for r in results)
    if failed:
        raise typer.Exit(code=2)
    if deferred:
        raise typer.Exit(code=3)


# --------------------------------------------------------------------------
# evidence inventory
# --------------------------------------------------------------------------


@evidence_app.command("inventory")
def evidence_inventory(
    path: Path = typer.Argument(..., help="An incident directory, an export package directory, a rosbag2 directory (or capture "
                                "session directory), or a single .mcap file."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable, versioned JSON (stdout) instead of the readable report."),
) -> None:
    """Inventory the evidence at PATH: artifacts, integrity, topics, clock domains, capture health, gaps. Read-only.

    Nothing is written, moved or locked inside PATH. An incident or package is re-hashed NOW against its manifest; a raw
    recording has no recorded digests, so its integrity is reported as not applicable and it is not validated against a
    profile. This is offline inspection of files: it does not show that a live ROS 2 capture works. No diagnosis is made.

    Exit codes: 0 = inspected and nothing known to be wrong with the evidence; 1 = inspected, but integrity failed or could
    not be checked, the evidence is partial/failed, or the inventory is partial; 2 = the input could not be inspected."""
    inv = inspect_evidence(path)
    typer.echo(inventory_json(inv) if json_output else render_inventory(inv), nl=False)
    code = inventory_exit_code(inv)
    if code:
        raise typer.Exit(code=code)


@evidence_app.command("investigate")
def evidence_investigate(
    path: Path = typer.Argument(..., help="Evidence to investigate: anything `evidence inventory` accepts."),
    expectations: Path = typer.Option(..., "--expectations", "-e", help="Explicit expectation contract (JSON or YAML, schema 1.0)."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable, versioned JSON (stdout) instead of the readable report."),
    registry_path: Optional[Path] = typer.Option(None, "--registry", help="Optional robot-family signal registry (YAML or JSON)."),
) -> None:
    """Compare explicitly expected behaviour with the evidence at PATH and report the earliest defensible divergence. Read-only.

    The expectations come only from the contract: nothing is inferred from the recording. 'No divergence observed' means no
    mismatch was established in what was evaluated, not that the system is correct. The first divergence is not a root cause.
    Nothing is written into PATH (no investigation record is added to the package).

    Exit codes: 0 = no divergence observed in the covered window; 1 = divergence found; 2 = inconclusive (insufficient evidence
    or incomparable timebases); 3 = the expectation contract is invalid."""
    from .investigator import exit_code as investigation_exit_code
    from .investigator import investigate, investigation_json, load_contract, render_investigation
    from .investigator.engine import invalid_result
    from .investigator.signal_registry import RegistryError, load_signal_registry

    loaded = load_contract(expectations)
    if loaded.contract is None:
        result = invalid_result(loaded.problems)
    else:
        try:
            registry = load_signal_registry(registry_path) if registry_path is not None else None
            result = investigate(inspect_evidence(path), loaded, registry=registry)
        except RegistryError as exc:
            raise typer.BadParameter(str(exc), param_hint="--registry") from exc
    typer.echo(investigation_json(result) if json_output else render_investigation(result), nl=False)
    code = investigation_exit_code(result)
    if code:
        raise typer.Exit(code=code)


@evidence_app.command("decode")
def evidence_decode(
    path: Path = typer.Argument(..., help="Evidence to read: anything `evidence inventory` accepts."),
    topic: str = typer.Option(..., "--topic", "-t", help="Topic whose messages to decode."),
    limit: int = typer.Option(5, "--limit", "-n", min=1, max=1000, help="How many messages (earliest log_time first)."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable, versioned JSON (stdout)."),
) -> None:
    """Show the decoded leaf values of the first messages of TOPIC, with artifact, message index, log_time and clock domain. Read-only.

    JSON payloads and CDR payloads with an embedded ros2msg schema (needs the optional extra: pip install physicore-collector[ros2])
    are decoded. Nothing is interpreted or diagnosed. Exit codes: 0 = decoded; 1 = decoded but some message failed or the evidence is
    incomplete; 2 = the topic could not be decoded or found."""
    from .investigator.decoding import decode_topic, render_view, view_json

    view = decode_topic(inspect_evidence(path), topic, limit=limit)
    typer.echo(view_json(view) if json_output else render_view(view), nl=False)
    if view.status not in ("ok", "partial"):
        raise typer.Exit(code=2)
    if view.status == "partial" or not view.evidence_complete:
        raise typer.Exit(code=1)


@evidence_app.command("detect-anomalies")
def evidence_detect_anomalies(
    path: Path = typer.Argument(..., help="Evidence to analyse: anything `evidence inventory` accepts."),
    anomaly_config: Path = typer.Option(..., "--anomaly-config", help="Explicit anomaly detection configuration (YAML or JSON, schema 1)."),
    detector: list[str] = typer.Option(None, "--detector", help="Run only this detector id (repeatable); default: every detector enabled in the configuration."),
    signal_profile: Optional[Path] = typer.Option(None, "--signal-profile", help="Optional strict numeric signal-anomaly profile (YAML). Requires --registry."),
    registry: Optional[Path] = typer.Option(None, "--registry", help="Optional robot-family signal registry used to decode and normalize profile signals."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON report (stdout) instead of the readable report."),
    fail_on_findings: bool = typer.Option(False, "--fail-on-findings", help="Exit 1 when at least one finding is reported."),
) -> None:
    """Report explicit failure indicators and signal/timing anomalies observed in the evidence at PATH. Read-only.

    Findings describe observations and configured parameters; they are not diagnoses. Numeric signal profiles require both
    --signal-profile and --registry; their events are also placed on per-clock-domain timelines. No cross-domain ordering
    or causal conclusion is inferred. Nothing is written into PATH."""
    from .anomaly.findings import DETECTOR_IDS
    from .anomaly.report import render_json, render_text
    from .anomaly.service import run_anomaly_detection

    unknown = [d for d in (detector or []) if d not in DETECTOR_IDS]
    if unknown:
        raise typer.BadParameter(f"unknown detector id {unknown[0]!r}; known: {', '.join(DETECTOR_IDS)}", param_hint="--detector")
    if (signal_profile is None) != (registry is None):
        raise typer.BadParameter("--signal-profile and --registry must be supplied together")
    report = run_anomaly_detection(path, anomaly_config, detector_ids=detector or None, fail_on_findings=fail_on_findings)
    if signal_profile is None:
        typer.echo(render_json(report) if json_output else render_text(report), nl=False)
    else:
        from .anomaly.profile_pipeline import run_signal_profile_detection, render_signal_profile_text
        try:
            profile_result = run_signal_profile_detection(path, signal_profile, registry)
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(str(exc), param_hint="--signal-profile/--registry") from exc
        if json_output:
            payload = {"anomaly_report": report.model_dump(mode="json", exclude_none=True), "signal_profile": profile_result}
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            typer.echo(render_text(report), nl=False)
            typer.echo(render_signal_profile_text(profile_result), nl=False)
    if report.exit_code:
        raise typer.Exit(code=report.exit_code)


@evidence_app.command("localize")
def evidence_localize(
    path: Path = typer.Argument(..., help="Evidence to analyse: anything `evidence inventory` accepts."),
    anomaly_config: Path = typer.Option(..., "--anomaly-config", help="Explicit anomaly detection configuration (YAML or JSON, schema 1)."),
    expectations: Path = typer.Option(None, "--expectations", help="Optional expectation contract; its divergences are included as symptoms."),
    dependencies: Path = typer.Option(None, "--dependencies", help="Optional dependency declarations (YAML or JSON, schema 1); without it only symptoms, lanes and earliest symptoms are reported."),
    detector: list[str] = typer.Option(None, "--detector", help="Run only this detector id (repeatable); default: every detector enabled in the configuration."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON report (stdout) instead of the readable report."),
) -> None:
    """Order the symptoms observed in the evidence at PATH and relate them along dependencies you declare. Read-only.

    The report states where symptoms were observed, in which order the recorder received them, and whether that order fits the dependencies you
    declared; it never states a cause. The declared dependencies cannot be verified by this tool. Nothing is written into PATH.

    Exit codes: 0 = completed; 1 = the evidence is partial / failed integrity (the report is still produced); 2 = the evidence could not be inspected
    (or a usage error); 3 = an input file (configuration, expectations or dependencies) is invalid (nothing is analysed)."""
    from .anomaly.findings import DETECTOR_IDS
    from .localization.output import render_text, to_json
    from .localization.service import run_localization

    unknown = [d for d in (detector or []) if d not in DETECTOR_IDS]
    if unknown:
        raise typer.BadParameter(f"unknown detector id {unknown[0]!r}; known: {', '.join(DETECTOR_IDS)}", param_hint="--detector")
    if str(dependencies) == "-" or str(expectations) == "-" or str(anomaly_config) == "-":
        raise typer.BadParameter("standard input is not accepted; give a file path", param_hint="--dependencies")
    report = run_localization(path, anomaly_config, expectations=expectations, dependencies=dependencies, detector_ids=detector or None)
    typer.echo(to_json(report) if json_output else render_text(report), nl=False)
    if report.exit_code:
        raise typer.Exit(code=report.exit_code)


@evidence_app.command("render")
def evidence_render(
    report: Path = typer.Argument(..., help="A localization report produced by `evidence localize --json` (schema 1.0)."),
    format: str = typer.Option(..., "--format", help="markdown (a Markdown investigation report) or html (one self-contained, offline, interactive page)."),
    verify_evidence: Path = typer.Option(None, "--verify-evidence", help="Re-read the recording at this path and check every evidence digest and message reference; the result is shown in the output."),
) -> None:
    """Render a localization report as a Markdown report or a single offline HTML investigation page. Read-only; the output goes to standard output.

    The view adds no analysis: every statement is in the report, and no sentence it generates states a cause. With --verify-evidence the digests and message
    references are checked against the recording on this run.

    Exit codes: 0 = rendered; 1 = rendered, but --verify-evidence found a mismatch; 2 = the report could not be read (or a usage error); 3 = the report is invalid,
    of an unsupported schema, or too large (nothing is rendered)."""
    from .investigation_view.service import FORMATS, run_render

    if format not in FORMATS:
        raise typer.BadParameter(f"use one of: {', '.join(FORMATS)}", param_hint="--format")
    if str(report) == "-":
        raise typer.BadParameter("standard input is not accepted; give a file path", param_hint="REPORT")
    if verify_evidence is not None and not verify_evidence.exists():
        raise typer.BadParameter("the path does not exist", param_hint="--verify-evidence")
    result = run_render(report, format, verify_evidence)
    if result.exit_code in (2, 3):
        for line in result.problems:
            typer.echo(line, err=True)
        raise typer.Exit(code=result.exit_code)
    typer.echo(result.text.encode("utf-8"), nl=False)
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@evidence_app.command("system-model")
def evidence_system_model(
    model: Path = typer.Argument(..., help="A system model file (YAML or JSON, schema 1) written by the people who run the robot."),
    evidence: Path = typer.Option(None, "--evidence", help="Optional recording to compare the model with: anything `evidence inventory` accepts."),
    dependencies: Path = typer.Option(None, "--dependencies", help="Optional dependency file; its digest is checked against the one the model names."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON report (stdout) instead of the readable report."),
) -> None:
    """Validate a declared system model and compare it with a recording. Read-only; nothing is inferred, corrected or written.

    The model is what its authors declare; this tool cannot verify it. Differences between the model and the recording are reported as findings and never
    repaired. No sentence states a cause.

    Exit codes: 0 = model valid; 1 = model valid, the recording is partial or unverified; 2 = the recording or the dependency file could not be read (or a usage
    error); 3 = the model is invalid (nothing is compared)."""
    from .rootcause.modelcheck import render_text, run_system_model, to_json

    if "-" in (str(model), str(evidence), str(dependencies)):
        raise typer.BadParameter("standard input is not accepted; give a file path", param_hint="MODEL")
    if evidence is not None and not evidence.exists():
        raise typer.BadParameter("the path does not exist", param_hint="--evidence")
    result = run_system_model(model, evidence, dependencies)
    if result["report"] is None:
        for line in result["problems"]:
            typer.echo(line, err=True)
        raise typer.Exit(code=result["exit_code"])
    typer.echo(to_json(result["report"]) if json_output else render_text(result["report"]), nl=False)
    if result["exit_code"]:
        raise typer.Exit(code=result["exit_code"])


@evidence_app.command("hypotheses")
def evidence_hypotheses(
    report: Path = typer.Argument(..., help="A localization report produced by `evidence localize --json` (schema 1.0)."),
    format: str = typer.Option("json", "--format", help="json (the report), markdown, or html (one self-contained, offline page)."),
    model: Path = typer.Option(None, "--model", help="Optional system model (YAML or JSON, schema 1) written by the people who run the robot."),
    dependencies: Path = typer.Option(None, "--dependencies", help="The dependency file the report was made with; its digest must equal the one in the report."),
    evidence: Path = typer.Option(None, "--evidence", help="The recording, read-only; needed to run generated checks and for --verify."),
    baseline: Path = typer.Option(None, "--baseline", help="A second localization report (a healthy baseline, or a recording after a change) for before/after contrasts."),
    catalogue: Path = typer.Option(None, "--catalogue", help="Optional extra catalogue entries (YAML or JSON); they are marked user_supplied."),
    verify: bool = typer.Option(False, "--verify", help="Re-read the recording at --evidence and check every evidence digest and message reference."),
) -> None:
    """Candidate mechanisms for the symptoms of a localization report, with the evidence for and against each. Read-only; output goes to standard output.

    Each hypothesis is an entry of a closed catalogue bound to real topics, with an evidence ledger, an ordinal tier and a claim level (0 to 4). Nothing is a
    probability, and no sentence states a cause. Catalogue entries are proposals until reviewed; their status is shown. `unresolved` is a legitimate result.

    Exit codes: 0 = done; 1 = done, but a verification found a mismatch or the recording did not match the report; 2 = an input could not be read (or a usage
    error); 3 = an input is invalid or does not fit the others (nothing is produced)."""
    from .rootcause.investigate import FORMATS, run_hypotheses

    if format not in FORMATS:
        raise typer.BadParameter(f"use one of: {', '.join(FORMATS)}", param_hint="--format")
    if "-" in {str(x) for x in (report, model, dependencies, evidence, baseline, catalogue) if x is not None}:
        raise typer.BadParameter("standard input is not accepted; give file paths", param_hint="REPORT")
    result = run_hypotheses(report, format, model_path=model, dependencies_path=dependencies, evidence_path=evidence, baseline_path=baseline, catalogue_path=catalogue, verify=verify)
    if not result["text"]:
        for line in result["problems"]:
            typer.echo(line, err=True)
        raise typer.Exit(code=result["exit_code"])
    typer.echo(result["text"].encode("utf-8"), nl=False)
    if result["exit_code"]:
        raise typer.Exit(code=result["exit_code"])
