"""Advisory file locks (flock) as the authority for "is a supervisor alive?".

Why a lock and not a PID: the kernel releases an flock when the holding process dies for ANY
reason (SIGKILL, crash, OOM, power loss, reboot), and a lock cannot be "reused" by an unrelated
process the way a PID can. The lock file is opened close-on-exec (Python default) and children
are started with close_fds, so a recorder that outlives its supervisor does NOT keep the lock
alive: "lock free" means "no supervisor", even if the recorder survives (an orphan).

Limits: flock is advisory (cooperating Collector processes only) and is only reliable on local
filesystems; it is not meant for NFS. A probe briefly takes a shared lock, so a supervisor that
is starting retries acquisition for a short grace period instead of failing on a probe.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

ACQUIRE_GRACE_SECONDS = 1.0


class LockHeldError(RuntimeError):
    """Another live process holds the lock."""


class FileLock:
    """Exclusive, non-blocking (with a short grace) lock held until `release()`/exit."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self, grace: float = ACQUIRE_GRACE_SECONDS) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        deadline = time.monotonic() + grace
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockHeldError(f"lock '{self.path}' is held by another process") from None
                time.sleep(0.02)
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    def write_text(self, text: str) -> None:
        """Replace the lock file's content (e.g. the owning session id) while holding the lock."""
        assert self._fd is not None, "lock not held"
        os.ftruncate(self._fd, 0)
        os.pwrite(self._fd, text.encode(), 0)

    @property
    def held(self) -> bool:
        return self._fd is not None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def lock_is_held_elsewhere(path: Path) -> bool | None:
    """True if a live process holds the lock, False if nobody does, None if it cannot be
    determined. A missing lock file means nobody holds it. Never blocks, never leaves a lock."""
    p = Path(path)
    try:
        fd = os.open(p, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def read_lock_text(path: Path, limit: int = 256) -> str | None:
    """Best-effort read of a lock file's descriptive content (never needs the lock)."""
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", errors="replace").strip() or None
    except OSError:
        return None
