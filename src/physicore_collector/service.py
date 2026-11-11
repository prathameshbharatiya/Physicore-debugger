"""systemd service for unattended capture: unit rendering, install, uninstall.

What the unit does (see docs/SERVICE.md):
  * `ExecStart` runs `physicore-collector capture service-run ...` directly: NO shell, no
    interpolation. The ROS environment is built by service-run from the named setup scripts.
  * One service = one output directory = at most one active capture (flocks, see supervisor.py).
  * The unit is installed DISABLED unless `--enable` is passed: nothing records at boot unless an
    operator explicitly opted in.
  * `Restart=on-failure` with a rate limit; each restart starts a NEW session (it never resumes or
    overwrites one), after the previous session was settled by `capture recover`. Exit status 78
    (configuration error, or another capture is active) is NOT restarted.
  * Uninstall removes only a unit carrying our marker, and never touches recordings.

What is NOT verified here (no systemd available in the development environment): that a real
`systemctl start/stop/restart` behaves as intended, boot-time ordering, and the sandboxing options
against a real DDS stack. Static validation with `systemd-analyze verify` is tested when present.
"""

from __future__ import annotations

import os
import pwd
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import CaptureConfigError
from .profile import load_profile
from .ros_env import FORWARDABLE, SAFE_PATH, SAFE_VALUE, validate_setup_file

MARKER = "# Managed by physicore-collector"
SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
USER_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\$?$")
UNIT_PREFIX = "physicore-collector-"
DEFAULT_DEST = Path("/etc/systemd/system")
EX_CONFIG = 78  # service-run exits with this for problems a restart cannot fix
RESTART_SECONDS = 10
START_LIMIT_INTERVAL_SECONDS = 600
START_LIMIT_BURST = 3
STOP_MARGIN_SECONDS = 15


def unit_name(name: str) -> str:
    return f"{UNIT_PREFIX}{name}.service"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    user: str
    executable: str
    profile: str
    out: str
    group: str | None = None
    ros_setups: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    restart: str = "on-failure"
    stop_timeout_seconds: float = 15.0
    auto_prepare: bool = True  # False -> the unit passes --no-auto-prepare to service-run


def _safe_arg(label: str, value: str) -> str:
    if not SAFE_PATH.match(value):
        raise CaptureConfigError(f"{label} {value!r} must be an absolute path using only letters, digits and '_ . / @ + -' "
                                 "(spaces, quotes, '$' and '%' would need escaping in a unit file and are refused).")
    return value


def validate_spec(spec: ServiceSpec, *, allow_root: bool = False, check_user: bool = True) -> list[str]:
    """Raise CaptureConfigError for anything unsafe; return non-fatal warnings."""
    warnings: list[str] = []
    if not SERVICE_NAME.match(spec.name):
        raise CaptureConfigError(f"Invalid service name {spec.name!r}: lowercase letters, digits, '_' or '-', starting with a letter (max 40).")
    if not USER_NAME.match(spec.user):
        raise CaptureConfigError(f"Invalid user name {spec.user!r}.")
    if spec.group is not None and not USER_NAME.match(spec.group):
        raise CaptureConfigError(f"Invalid group name {spec.group!r}.")
    if spec.restart not in ("on-failure", "no"):
        raise CaptureConfigError("--restart must be 'on-failure' or 'no'.")
    uid = None
    if check_user:
        try:
            pw = pwd.getpwnam(spec.user)
        except KeyError as e:
            raise CaptureConfigError(f"User '{spec.user}' does not exist on this machine.",
                                     hint="Create a dedicated account (e.g. `useradd --system --create-home physicore`) and use it with --user.") from e
        uid = pw.pw_uid
        if uid == 0 and not allow_root:
            raise CaptureConfigError("Refusing to run the capture service as root.",
                                     hint="Use a dedicated unprivileged account, or pass --allow-root to override knowingly.")
    exe = Path(_safe_arg("Executable", spec.executable))
    if not exe.is_file() or not os.access(exe, os.X_OK):
        raise CaptureConfigError(f"Executable '{exe}' does not exist or is not executable.")
    if exe.stat().st_mode & stat.S_IWOTH:
        raise CaptureConfigError(f"Executable '{exe}' is writable by any user; refusing to run it from a service.")
    prof = Path(_safe_arg("Profile", spec.profile))
    if not prof.is_file():
        raise CaptureConfigError(f"Profile '{prof}' does not exist.")
    load_profile(prof)  # full validation now, not at 3 a.m.
    if uid is not None and not os.access(prof, os.R_OK) and prof.stat().st_uid != uid and not (prof.stat().st_mode & stat.S_IROTH):
        warnings.append(f"profile '{prof}' may not be readable by user '{spec.user}'")
    out = Path(_safe_arg("Output directory", spec.out))
    if not out.is_dir():
        raise CaptureConfigError(f"Output directory '{out}' does not exist; create it first (the service never creates parent directories).")
    if uid is not None and out.stat().st_uid != uid and not (out.stat().st_mode & stat.S_IWOTH):
        warnings.append(f"output directory '{out}' is not owned by '{spec.user}': make sure that user can write there")
    trusted = {uid} if uid is not None else set()
    for s in spec.ros_setups:
        validate_setup_file(s, trusted_uids=trusted)
    for k, v in spec.env.items():
        if k not in FORWARDABLE or not SAFE_VALUE.match(v):
            raise CaptureConfigError(f"Cannot forward {k}={v!r}.")
    return warnings


def render_unit(spec: ServiceSpec) -> str:
    """Pure function: spec -> unit text. Every operator-supplied value is validated by
    `validate_spec` to a character set that needs no quoting or escaping in a unit file."""
    state = f"physicore-collector/{spec.name}"
    args = [spec.executable, "capture", "service-run", "--name", spec.name, "--profile", spec.profile, "--out", spec.out]
    for s in spec.ros_setups:
        args += ["--ros-setup", s]
    for k in sorted(spec.env):
        args += ["--env", f"{k}={spec.env[k]}"]
    if not spec.auto_prepare:
        args.append("--no-auto-prepare")
    stop_budget = int(spec.stop_timeout_seconds) + STOP_MARGIN_SECONDS
    lines = [
        f"{MARKER} (service name: {spec.name}). Re-run `capture install-service` to change it; do not edit by hand.",
        "[Unit]",
        f"Description=PhysiCore Collector capture supervisor ({spec.name})",
        "After=local-fs.target network.target",
        f"StartLimitIntervalSec={START_LIMIT_INTERVAL_SECONDS}",
        f"StartLimitBurst={START_LIMIT_BURST}",
        "",
        "[Service]",
        "Type=simple",
        f"User={spec.user}",
        *([f"Group={spec.group}"] if spec.group else []),
        "ExecStart=" + " ".join(args),
        f"Restart={spec.restart}",
        f"RestartSec={RESTART_SECONDS}",
        f"RestartPreventExitStatus={EX_CONFIG}",
        # SIGTERM goes to the supervisor only (mixed); it stops the recorder gracefully. After the
        # budget below, systemd kills everything left in the unit's control group.
        "KillMode=mixed",
        "KillSignal=SIGTERM",
        f"TimeoutStopSec={stop_budget}",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        f"ReadWritePaths={spec.out} /tmp",
        f"StateDirectory={state}",
        "UMask=0027",
        f"Environment=HOME=/var/lib/{state}",
        f"Environment=ROS_LOG_DIR=/var/lib/{state}/ros_log",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def _systemctl(*args: str) -> tuple[bool, str]:
    exe = shutil.which("systemctl")
    if exe is None:
        return False, "systemctl not found"
    p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=60)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


@dataclass(frozen=True)
class InstallResult:
    unit_path: Path
    warnings: tuple[str, ...]
    notes: tuple[str, ...]


def install_service(spec: ServiceSpec, dest_dir: Path = DEFAULT_DEST, *, systemctl: bool = True, enable: bool = False,
                    start: bool = False, force: bool = False, allow_root: bool = False) -> InstallResult:
    warnings = validate_spec(spec, allow_root=allow_root)
    text = render_unit(spec)
    path = Path(dest_dir) / unit_name(spec.name)
    if not Path(dest_dir).is_dir():
        raise CaptureConfigError(f"Unit directory '{dest_dir}' does not exist.")
    if path.exists():
        managed = path.read_text(errors="replace").startswith(MARKER)
        if not (force and managed):
            raise CaptureConfigError(
                f"'{path}' already exists" + ("" if managed else " and was not written by physicore-collector; it will not be touched"),
                hint="Use `uninstall-service` first" + (", or --force to replace this managed unit." if managed else "."))
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except PermissionError as e:
        raise CaptureConfigError(f"Cannot write '{path}': permission denied.",
                                 hint="Run as root (sudo), or use --dry-run to print the unit, or --dest-dir to write elsewhere.") from e
    notes: list[str] = []
    if systemctl:
        ok, msg = _systemctl("daemon-reload")
        notes.append("systemctl daemon-reload: " + ("ok" if ok else f"FAILED ({msg})"))
        if enable:
            ok, msg = _systemctl("enable", unit_name(spec.name))
            notes.append(f"enabled at boot: {'yes' if ok else f'FAILED ({msg})'}")
        else:
            notes.append("NOT enabled at boot (pass --enable to opt in)")
        if start:
            ok, msg = _systemctl("start", unit_name(spec.name))
            notes.append(f"started: {'yes' if ok else f'FAILED ({msg})'}")
    else:
        notes.append("systemctl was not called (--no-systemctl)")
    return InstallResult(path, tuple(warnings), tuple(notes))


def uninstall_service(name: str, dest_dir: Path = DEFAULT_DEST, *, systemctl: bool = True) -> list[str]:
    if not SERVICE_NAME.match(name):
        raise CaptureConfigError(f"Invalid service name {name!r}.")
    path = Path(dest_dir) / unit_name(name)
    if not path.is_file():
        raise CaptureConfigError(f"No unit '{path}' is installed.")
    if not path.read_text(errors="replace").startswith(MARKER):
        raise CaptureConfigError(f"'{path}' was not written by physicore-collector; refusing to remove it.")
    notes: list[str] = []
    if systemctl:
        ok, msg = _systemctl("stop", unit_name(name))
        notes.append("stopped (graceful, bounded by TimeoutStopSec): " + ("ok" if ok else f"no/failed ({msg})"))
        ok, msg = _systemctl("disable", unit_name(name))
        notes.append("disabled: " + ("ok" if ok else f"no/failed ({msg})"))
    path.unlink()
    notes.append(f"removed {path}")
    if systemctl:
        ok, msg = _systemctl("daemon-reload")
        notes.append("systemctl daemon-reload: " + ("ok" if ok else f"FAILED ({msg})"))
    notes.append("recordings and capture sessions were not touched")
    return notes
