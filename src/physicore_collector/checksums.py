"""Streaming SHA-256 helpers. Files are never read fully into memory by
`sha256_file`; `read_and_hash` is for small files that must be parsed and
hashed from the exact same bytes."""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
from typing import Callable, Iterator

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Optional cooperative hook for every BULK read/write PhysiCore does itself. It is called with the number of bytes
# just moved: once per chunk in `sha256_file`, `read_and_hash` and `copy_and_hash`, and once per read of the MCAP
# scan (`mcap_reader._BoundedReader.read` calls `notify_io`). It exists so incident preparation can throttle itself
# and stop cleanly (by raising) when a recording starts; when unset, behaviour is unchanged.
# Process-wide and single-threaded by design: set it only through `io_hook`. It is NOT a bandwidth guarantee for the
# machine: it sees only the I/O issued through these functions, not the page cache or other processes.
_io_hook: Callable[[int], None] | None = None
# While a hook is installed, `copy_and_hash` also `fdatasync`s every this many bytes so the dirty-page backlog (and
# the final flush stall) stays bounded and every checkpoint is reached promptly. Unset hook: no extra syncs.
SYNC_EVERY_BYTES = 32 * 1024 * 1024


def notify_io(nbytes: int) -> None:
    """Report `nbytes` of bulk I/O to the installed hook (a no-op when none is installed). May raise: that is how
    preparation yields."""
    if _io_hook is not None:
        _io_hook(nbytes)


@contextlib.contextmanager
def io_hook(hook: Callable[[int], None] | None) -> Iterator[None]:
    """Install `hook` for the duration of the block (restored on any exit, including exceptions)."""
    global _io_hook
    previous = _io_hook
    _io_hook = hook
    try:
        yield
    finally:
        _io_hook = previous


def _check_chunk(chunk_size: int) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")


def sha256_file(path: str | Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Return the lowercase hex SHA-256 of a file, reading it in chunks."""
    _check_chunk(chunk_size)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
            notify_io(len(chunk))
    return h.hexdigest()


class FileTooLargeError(Exception):
    """Raised by `read_and_hash` when the file exceeds `max_bytes`."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"file exceeds the {max_bytes}-byte limit")
        self.max_bytes = max_bytes


def read_and_hash(
    path: str | Path, *, max_bytes: int, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> tuple[bytes, str]:
    """Single streaming pass returning (content, sha256_hex).

    Hashing and returning the same bytes guarantees the digest describes
    exactly what the caller goes on to parse, even if the file changes on disk
    afterwards. Stops reading as soon as `max_bytes` is exceeded.
    """
    _check_chunk(chunk_size)
    h = hashlib.sha256()
    buf = bytearray()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise FileTooLargeError(max_bytes)
            h.update(chunk)
            notify_io(len(chunk))
    return bytes(buf), h.hexdigest()


def copy_and_hash(
    src: str | Path, dst: str | Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> tuple[str, int]:
    """Copy src to a NEW file dst byte-for-byte, returning (sha256 of the bytes read, size).

    `dst` is opened exclusively ('xb'): an existing file is never overwritten. The data is
    flushed and fsynced before returning.
    """
    _check_chunk(chunk_size)
    h = hashlib.sha256()
    total = 0
    unsynced = 0
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        while chunk := fin.read(chunk_size):
            fout.write(chunk)
            h.update(chunk)
            total += len(chunk)
            if _io_hook is not None:
                unsynced += len(chunk)
                if unsynced >= SYNC_EVERY_BYTES:
                    fout.flush()
                    os.fdatasync(fout.fileno())
                    unsynced = 0
                _io_hook(len(chunk))
        fout.flush()
        os.fsync(fout.fileno())
    return h.hexdigest(), total
