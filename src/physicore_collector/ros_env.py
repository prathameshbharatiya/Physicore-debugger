"""The ROS environment for an unattended capture, built WITHOUT trusting an interactive shell.

A systemd service has no login shell, so `ros2` and its libraries are not on PATH. The operator
names the setup scripts to source (e.g. /opt/ros/<distro>/setup.bash, a workspace install/setup.bash)
and we source them ONCE, in a fixed, non-interactive `bash --noprofile --norc`, then capture the
resulting environment and hand exactly that to the recorder.

Safety properties (each tested):
  * No shell interpolation of operator input: the bash script text is a constant; setup file
    paths are positional arguments, and are also restricted to a conservative character set.
  * A setup script that other users can modify is refused (sourcing it would let them run code as
    the service user), as is one owned by an unrelated user.
  * Only an allow-list of variables can be forwarded explicitly; values are restricted to a
    conservative character set. There is no "forward everything" switch.
  * Nothing is guessed: no distro is assumed, no path is hard-coded. If `ros2` is not on the
    resulting PATH, that is an error, not a fallback.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from .errors import CaptureConfigError

SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./@+-]+$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@+=,-]*$")
FORWARDABLE = (
    "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION", "ROS_LOCALHOST_ONLY", "ROS_AUTOMATIC_DISCOVERY_RANGE",
    "ROS_STATIC_PEERS", "CYCLONEDDS_URI", "FASTRTPS_DEFAULT_PROFILES_FILE", "ROS_DISTRO",
)
# Added by bash itself or meaningless after the sourcing subprocess exits.
_DROP = {"_", "SHLVL", "PWD", "OLDPWD"}
_SCRIPT = 'set -e; for f in "$@"; do . "$f"; done; exec env -0'  # constant: never built from input
_BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SOURCE_TIMEOUT_SECONDS = 60


def validate_setup_file(path: str, *, trusted_uids: set[int] | None = None) -> Path:
    if not SAFE_PATH.match(path or ""):
        raise CaptureConfigError(
            f"ROS setup path {path!r} must be absolute and use only letters, digits and '_ . / @ + -'.",
        )
    p = Path(path)
    if not p.is_file():
        raise CaptureConfigError(f"ROS setup script '{p}' does not exist or is not a regular file.")
    st = p.stat()  # follows symlinks: what is actually sourced is what is checked
    if st.st_mode & stat.S_IWOTH:
        raise CaptureConfigError(f"ROS setup script '{p}' is writable by any user; refusing to source it.",
                                 hint="chmod o-w on it (and check its directory): sourcing it runs its code as the service user.")
    allowed = {0, os.getuid()} | (trusted_uids or set())
    if st.st_uid not in allowed:
        raise CaptureConfigError(f"ROS setup script '{p}' is owned by uid {st.st_uid}, who is neither root nor the service user; refusing to source it.")
    return p


def parse_env_assignments(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or key not in FORWARDABLE:
            raise CaptureConfigError(f"Cannot forward {item!r}: expected KEY=VALUE with KEY one of {', '.join(FORWARDABLE)}.")
        if not SAFE_VALUE.match(value):
            raise CaptureConfigError(f"Value for {key} contains characters outside letters, digits and '_ . / : @ + = , -'.")
        if key in out:
            raise CaptureConfigError(f"{key} was given more than once.")
        out[key] = value
    return out


def load_ros_environment(setup_files: list[str], extra: dict[str, str] | None = None,
                         *, trusted_uids: set[int] | None = None) -> tuple[dict[str, str], str]:
    """Source the setup scripts and return (environment, absolute path of `ros2`)."""
    files = [str(validate_setup_file(f, trusted_uids=trusted_uids)) for f in setup_files]
    base = {"PATH": _BASE_PATH, "LANG": "C.UTF-8"}
    if "HOME" in os.environ:
        base["HOME"] = os.environ["HOME"]
    try:
        proc = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", _SCRIPT, "physicore-ros-env", *files],
            env=base, stdin=subprocess.DEVNULL, capture_output=True, timeout=SOURCE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise CaptureConfigError(f"Sourcing the ROS setup scripts did not finish within {SOURCE_TIMEOUT_SECONDS} s.") from e
    except OSError as e:
        raise CaptureConfigError(f"Cannot run /bin/bash to source the ROS setup scripts: {e.strerror or e}") from e
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise CaptureConfigError("Sourcing the ROS setup scripts failed" + (": " + " | ".join(tail) if tail else "."))
    env: dict[str, str] = {}
    for rec in proc.stdout.split(b"\0"):
        if not rec:
            continue
        k, sep, v = rec.decode("utf-8", "replace").partition("=")
        if sep and k not in _DROP and k.replace("_", "a").isalnum():
            env[k] = v
    env.update(extra or {})
    exe = shutil.which("ros2", path=env.get("PATH", ""))
    if exe is None:
        raise CaptureConfigError("`ros2` is not on PATH after sourcing the given setup scripts.",
                                 hint="Name the right scripts with --ros-setup (e.g. the distro's setup.bash).")
    return env, exe
