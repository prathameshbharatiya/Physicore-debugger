"""Recorder adapter boundary: the ONLY place that knows how to start rosbag2.

PhysiCore does not record messages. `Rosbag2CliAdapter` starts the supported recorder
(`ros2 bag record`) as a child process, forwards a graceful-stop request (SIGINT, exactly what
Ctrl+C does to it, which makes rosbag2 flush and write metadata.yaml), and can force-kill it.
Everything about *what* to record is expressed with documented `ros2 bag record` options:
  --output, --storage, --max-bag-size, --max-bag-duration, and positional topic names.
(Option names verified against ros2bag/ros2bag/verb/record.py in https://github.com/ros2/rosbag2.)

`RecorderAdapter` is a Protocol so the supervisor can be tested without ROS 2 using doubles.
Nothing here imports rclpy or any ROS 2 Python package; ROS 2 is a runtime dependency of the
`ros2` executable only, so offline MCAP/bag inspection never needs it.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import RecorderStartError

OUTPUT_TAIL_LINES = 50
OUTPUT_LINE_MAX_CHARS = 500
SESSION_DIR_PLACEHOLDER = "{SESSION_DIR}"

# The recorder must start with a DETERMINISTIC signal state, whatever the supervisor inherited:
#   * SIGINT must work (it is how a graceful stop is requested). If the supervisor's environment ignores
#     it (a backgrounded shell job, nohup, a test runner), a plain exec would inherit "ignored" and the
#     graceful stop would silently do nothing until the recorder is killed. Blocked signals are inherited
#     across exec too, so the mask is cleared.
#   * SIGPIPE must stay IGNORED: this supervisor holds the read end of the recorder's output pipe, so if it
#     dies, the recorder's next log line would otherwise kill it instantly (no flush, no metadata.yaml).
# A constant trampoline sets this state and exec()s the recorder: same pid, so the recorded process
# identity is the recorder's. No operator input is ever interpolated: the command line is argv.
_TRAMPOLINE = (
    "import os, signal, sys\n"
    "for n in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGUSR1, signal.SIGUSR2, "
    "signal.SIGALRM, signal.SIGCHLD):\n"
    "    signal.signal(n, signal.SIG_DFL)\n"
    "signal.pthread_sigmask(signal.SIG_SETMASK, [])\n"
    "signal.signal(signal.SIGPIPE, signal.SIG_IGN)\n"
    "os.execv(sys.argv[1], sys.argv[1:])\n"
)

# Text rosbag2 prints (source: rosbag2_cpp cache/message_cache.cpp, message_cache_circular_buffer.cpp,
# verified in the upstream source) when its own cache dropped or failed to flush messages. This is
# a human-readable log line, NOT a stable machine interface: it is kept verbatim and never parsed
# for numbers. Its ABSENCE proves nothing (other loss paths, other versions, log levels).
LOSS_SIGNAL_PATTERN = re.compile(r"Cache buffers lost messages|Total lost:|Cache buffers were unflushed|Dropping message|exceeds snapshot buffer", re.I)
MAX_SIGNAL_LINES = 50


@dataclass(frozen=True)
class RecorderSpec:
    bag_dir: Path  # must not exist: rosbag2 itself refuses an existing output folder
    topics: tuple[str, ...]
    storage_id: str = "mcap"
    max_bag_size_bytes: int | None = None
    max_bag_duration_seconds: int | None = None


class RecorderAdapter(Protocol):
    name: str

    def command(self, spec: RecorderSpec) -> list[str]:
        """The argument vector that start() will run (informational; may be sanitized by caller)."""

    def start(self, spec: RecorderSpec) -> None:
        """Start recording. Raises RecorderStartError if the recorder cannot be launched."""

    @property
    def pid(self) -> int | None: ...

    def poll(self) -> int | None:
        """Exit status if the recorder has ended (negative = killed by that signal), else None."""

    def request_stop(self) -> None:
        """Ask the recorder to finish gracefully (flush and finalize). Must not block."""

    def wait(self, timeout: float) -> int | None:
        """Wait up to `timeout` seconds. Exit status, or None if still running."""

    def kill(self) -> None:
        """Force-terminate the recorder and everything it started. Must not raise if it is gone."""

    def output_tail(self) -> list[str]:
        """Last lines of recorder console output (bounded)."""

    def output_signals(self) -> list[str]:
        """Verbatim recorder output lines matching rosbag2's own message-loss warnings (bounded)."""


def sanitize_command(argv: list[str], bag_dir: Path) -> list[str]:
    """What is written to the capture-health sidecar: the executable's base name and the bag path
    replaced by a placeholder, so the record is portable and leaks no host directory layout."""
    out = []
    for i, a in enumerate(argv):
        if i == 0:
            out.append(os.path.basename(a))
        elif a == str(bag_dir):
            out.append(f"{SESSION_DIR_PLACEHOLDER}/{bag_dir.name}")
        else:
            out.append(a)
    return out


class Rosbag2CliAdapter:
    """Delegates recording to `ros2 bag record`."""

    name = "rosbag2-cli"

    def __init__(self, executable: str | None = None, env: dict[str, str] | None = None) -> None:
        """`env`: the COMPLETE environment for the recorder (service mode builds it from the ROS
        setup scripts). None: inherit this process's environment."""
        self._executable = executable
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._tail: deque[str] = deque(maxlen=OUTPUT_TAIL_LINES)
        self._signals: list[str] = []
        self._reader: threading.Thread | None = None

    def _resolve(self) -> str:
        exe = self._executable or shutil.which("ros2", path=(self.env or {}).get("PATH") if self.env else None)
        if exe is None:
            raise RecorderStartError(
                "The 'ros2' executable was not found on PATH.",
                hint="Source your ROS 2 setup first (e.g. `source /opt/ros/<distro>/setup.bash`), then retry.",
            )
        return exe

    def command(self, spec: RecorderSpec) -> list[str]:
        cmd = [self._executable or shutil.which("ros2") or "ros2", "bag", "record",
               "--output", str(spec.bag_dir), "--storage", spec.storage_id]
        if spec.max_bag_size_bytes is not None:
            cmd += ["--max-bag-size", str(spec.max_bag_size_bytes)]
        if spec.max_bag_duration_seconds is not None:
            cmd += ["--max-bag-duration", str(spec.max_bag_duration_seconds)]
        cmd += list(spec.topics)
        return cmd

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def start(self, spec: RecorderSpec) -> None:
        if os.name != "posix":
            raise RecorderStartError("Capture is supported on Linux/POSIX only.")
        if self._proc is not None:
            raise RecorderStartError("This adapter instance has already been started.")
        exe = self._resolve()
        cmd = self.command(spec)
        cmd[0] = exe
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            raise RecorderStartError(f"Could not execute '{exe}': not an executable file.")
        cmd = [sys.executable, "-I", "-S", "-c", _TRAMPOLINE, *cmd]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env,
                restore_signals=False,  # the trampoline sets the complete signal state explicitly (see _TRAMPOLINE)
                start_new_session=True,  # own process group: stop/kill reach every child, Ctrl+C on us does not hit it directly
            )
        except FileNotFoundError as e:
            raise RecorderStartError(f"Could not execute '{exe}': {e.strerror or e}") from e
        except PermissionError as e:
            raise RecorderStartError(f"Not permitted to execute '{exe}': {e.strerror or e}") from e
        except OSError as e:
            raise RecorderStartError(f"Could not start the recorder: {e.strerror or e}") from e
        self._reader = threading.Thread(target=self._drain, name="recorder-output", daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in iter(self._proc.stdout.readline, b""):  # keeps the pipe empty so the recorder never blocks
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self._tail.append(line[:OUTPUT_LINE_MAX_CHARS])
                if LOSS_SIGNAL_PATTERN.search(line) and len(self._signals) < MAX_SIGNAL_LINES:
                    self._signals.append(line[:OUTPUT_LINE_MAX_CHARS])
        self._proc.stdout.close()

    def poll(self) -> int | None:
        return None if self._proc is None else self._proc.poll()

    def _signal_group(self, sig: int) -> None:
        if self._proc is None:
            return
        try:
            os.killpg(self._proc.pid, sig)  # start_new_session => pgid == pid
        except ProcessLookupError:
            pass  # already gone
        except PermissionError:
            pass  # group no longer ours (cannot happen in normal operation)

    def request_stop(self) -> None:
        self._signal_group(signal.SIGINT)

    def wait(self, timeout: float) -> int | None:
        if self._proc is None:
            return None
        try:
            rc = self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        if self._reader is not None:
            self._reader.join(timeout=2)
        return rc

    def kill(self) -> None:
        self._signal_group(signal.SIGKILL)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL on a group we own
                pass
        if self._reader is not None:
            self._reader.join(timeout=2)

    def output_tail(self) -> list[str]:
        return list(self._tail)

    def output_signals(self) -> list[str]:
        return list(self._signals)
