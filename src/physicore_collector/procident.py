"""Process identity: the answer to "is this PID still the process I recorded?".

A bare PID is not an identity: after a process dies the kernel may hand its PID to an unrelated
process, and after a reboot every PID means something else. Identity here is the triple
(pid, process start time in clock ticks since boot, boot id) read from /proc (Linux).

Rules enforced by `signal_if_same`:
  * a process is signalled ONLY if its current identity equals the recorded one, checked
    immediately before the signal (the unavoidable check-then-signal window is a few
    instructions; the kernel offers no atomic "signal pid if start time == X" on Linux without
    pidfd, which is not used here to stay portable across the supported kernels);
  * a process-GROUP signal additionally requires the process to be its own group leader
    (`getpgid(pid) == pid`), which is how supervisors start recorders (start_new_session), so a
    reused pid cannot cause a signal to reach an unrelated group.
Without /proc (non-Linux) identity is `unknown` and NOTHING is ever signalled.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

PROC = Path("/proc")
BOOT_ID_PATH = PROC / "sys" / "kernel" / "random" / "boot_id"


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    starttime_ticks: int
    boot_id: str


class IdentityMatch(str, Enum):
    SAME = "same"  # the very process that was recorded
    DIFFERENT = "different"  # the pid exists but is another process (reuse) or another boot
    GONE = "gone"  # no such process
    UNKNOWN = "unknown"  # cannot be determined (no /proc, unreadable): never treated as SAME


def read_boot_id() -> str | None:
    try:
        return BOOT_ID_PATH.read_text().strip() or None
    except OSError:
        return None


def read_starttime_ticks(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat. The command name (field 2) may contain spaces and ')', so
    parse after the LAST ')'."""
    try:
        raw = (PROC / str(pid) / "stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError:
        return None
    try:
        fields = raw[raw.rindex(")") + 2:].split()
        return int(fields[19])  # fields[0] is field 3 (state); starttime is field 22
    except (ValueError, IndexError):
        return None


def process_state(pid: int) -> str | None:
    try:
        raw = (PROC / str(pid) / "stat").read_text()
        return raw[raw.rindex(")") + 2:].split()[0]
    except (OSError, ValueError, IndexError):
        return None


def identity_of(pid: int) -> ProcessIdentity | None:
    """Identity of a currently running process, or None if it cannot be established."""
    boot, start = read_boot_id(), read_starttime_ticks(pid)
    if boot is None or start is None:
        return None
    return ProcessIdentity(pid, start, boot)


def check_identity(recorded: ProcessIdentity | None) -> IdentityMatch:
    if recorded is None:
        return IdentityMatch.UNKNOWN
    boot = read_boot_id()
    if boot is None:
        return IdentityMatch.UNKNOWN
    if boot != recorded.boot_id:
        return IdentityMatch.DIFFERENT if (PROC / str(recorded.pid)).exists() else IdentityMatch.GONE
    start = read_starttime_ticks(recorded.pid)
    if start is None:
        return IdentityMatch.GONE
    if process_state(recorded.pid) == "Z":
        return IdentityMatch.GONE  # a zombie has exited; it only awaits reaping
    return IdentityMatch.SAME if start == recorded.starttime_ticks else IdentityMatch.DIFFERENT


def signal_if_same(recorded: ProcessIdentity | None, sig: int, *, group: bool = False) -> bool:
    """Send `sig` to the recorded process (or its process group) iff its identity still matches.
    Returns True if a signal was sent. Never raises for a vanished process."""
    if check_identity(recorded) is not IdentityMatch.SAME:
        return False
    assert recorded is not None
    try:
        if group:
            if os.getpgid(recorded.pid) != recorded.pid:
                return False  # not a group leader: refuse rather than risk signalling a foreign group
            os.killpg(recorded.pid, sig)
        else:
            os.kill(recorded.pid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


GRACEFUL = signal.SIGINT
