"""Offline MCAP scanning: topics, message counts, log_time range, integrity.

READABILITY DEFINITIONS (what this module actually establishes):

  readable            The file starts with MCAP magic and a Header record, the data
                      section was read sequentially to a DataEnd record and a Footer,
                      the closing magic matched, no bytes follow it, no record failed
                      to parse, chunk CRCs and the data-section CRC (where the file
                      carries them) verified, channels/schemas are consistent, and, if a
                      Statistics record exists, its message_count equals the number of
                      messages actually read.
  partially_readable  A valid start magic and Header were read, but at least one of the
                      above did not hold. Data read before the problem is kept and
                      reported; everything after it is unknown. (Truncated files land
                      here. Experimentally, EVERY proper prefix of a valid file is
                      rejected: see tests/test_mcap_reader.py.)
  unreadable          Empty file, wrong start magic, or no valid Header could be read.

NOT VERIFIED (stated in every result, see `NOT_VERIFIED`): the content of the summary
section (chunk/message index offsets, summary offsets) is parsed but not cross-checked.
Experimentally ~6% of single-byte corruptions fall there and go undetected.
The scan is also not hardened against adversarial files (e.g. decompression bombs).

Reads are bounded by the file size, so corrupted length fields cannot cause huge allocations
on the file-reading path. Decompression of a chunk whose header lies about its uncompressed
size is NOT bounded (documented limitation).

Memory: log_time values for every message are kept (8 bytes each) so that cadence can
be analysed without a second pass.
"""

from __future__ import annotations

import struct
import zlib
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from mcap.exceptions import EndOfFile, McapError
from mcap.records import (
    Channel, DataEnd, Footer, Header, Message, Metadata, Schema, Statistics,
)
from mcap.stream_reader import StreamReader

try:  # zstandard ships with mcap; guard so a broken install is reported, not hidden
    from zstandard import ZstdError
except ImportError:  # pragma: no cover
    ZstdError = McapError  # type: ignore[misc,assignment]

from .checksums import notify_io
from .errors import InputError
from .schemas import (
    ArtifactReadability, InspectionResult, IntegrityInfo, MetadataRecord, RecordingWindow,
    TopicInfo,
)

MCAP_MAGIC = b"\x89MCAP0\r\n"

NOT_VERIFIED = [
    "Summary-section contents (chunk indexes, message indexes, summary offsets) are parsed but not cross-validated.",
    "Records are validated sequentially; the scan is not hardened against adversarial or malicious files.",
    "Message payloads are not decoded or interpreted.",
]

# Exactly the failures the mcap/zstd/lz4/struct layers raise on bad bytes (established by
# fuzzing truncations and byte flips). RuntimeError is what lz4 raises for a bad frame.
# Anything else (e.g. a bug in this module) is deliberately NOT caught.
_FORMAT_ERRORS: tuple[type[BaseException], ...] = (
    McapError, ValueError, struct.error, OverflowError, zlib.error, EOFError, ZstdError, RuntimeError,
)


def _describe(e: BaseException, offset: int) -> str:
    """Plain-language description that still names the underlying exception."""
    if isinstance(e, (EndOfFile, struct.error, EOFError)):
        what = "unexpected end of data: the file is truncated or a record is corrupt"
    else:
        what = str(e) or "record could not be parsed"
    return f"{what} [{type(e).__name__}; reading stopped near byte offset {offset}]"


@dataclass
class ScanResult:
    inspection: InspectionResult
    # channel_id -> log_time_ns values in file order (unsigned 64-bit)
    log_times_by_channel: dict[int, array] = field(default_factory=dict)

    @property
    def readability(self) -> ArtifactReadability:
        return self.inspection.readability

    @property
    def errors(self) -> list[str]:
        return self.inspection.errors

    def topic_names(self) -> set[str]:
        return {t.topic for t in self.inspection.topics}

    def message_count_for_topic(self, topic: str) -> int:
        return sum(t.message_count for t in self.inspection.topics if t.topic == topic)

    def log_times_for_topic(self, topic: str) -> list[int]:
        """All log_time values (ns) for a topic across its channels, sorted ascending."""
        times: list[int] = []
        for t in self.inspection.topics:
            if t.topic == topic:
                times.extend(self.log_times_by_channel.get(t.channel_id, ()))
        times.sort()
        return times


class _BoundedReader:
    """File wrapper that refuses reads larger than the bytes remaining.

    A corrupted length prefix can make the mcap library ask for gigabytes; a plain file
    object would try to allocate that (MemoryError). A request beyond the end of the file is
    by definition truncation/corruption, so it is reported as EOFError instead.
    """

    def __init__(self, f, size: int) -> None:
        self._f = f
        self._size = size

    def read(self, n: int = -1) -> bytes:
        remaining = self._size - self._f.tell()
        if n < 0:
            n = remaining
        if n > remaining:
            raise EOFError(f"a record claims {n} bytes but only {remaining} remain in the file")
        data = self._f.read(n)
        notify_io(len(data))  # checkpoint for incident preparation (throttle / yield); a no-op unless a hook is installed
        return data


def _channel_key(c: Channel) -> tuple:
    return (c.topic, c.schema_id, c.message_encoding, tuple(sorted(c.metadata.items())))


def _empty_integrity(**kw) -> IntegrityInfo:
    base = dict(
        start_magic_valid=False, header_present=False, data_end_present=False,
        footer_present=False, end_magic_valid=False, trailing_bytes=0,
        statistics_present=False, statistics_message_count=None,
    )
    base.update(kw)
    return IntegrityInfo(**base)


def scan_recording(path: str | Path) -> ScanResult:
    """Scan an MCAP file. Never raises for malformed *content* (that is reported in the
    result); raises InputError only when the file cannot be accessed at all."""
    p = Path(path)
    if not p.exists():
        raise InputError(f"Recording not found: '{p}'", hint="Check the path to the .mcap file.")
    if not p.is_file():
        raise InputError(f"Recording path is not a regular file: '{p}'")
    try:
        size = p.stat().st_size
        f = open(p, "rb")
    except OSError as e:
        raise InputError(f"Cannot read recording '{p}': {e.strerror or e}") from e

    with f:
        try:
            return _scan_stream(f, p.name, size)
        except OSError as e:  # I/O failure while reading is an environment problem, not a format finding
            raise InputError(f"I/O error while reading recording '{p}': {e.strerror or e}") from e


def _unreadable(name: str, size: int, error: str, *, start_magic_valid: bool = False) -> ScanResult:
    return ScanResult(
        InspectionResult(
            file_name=name, size_bytes=size, readability=ArtifactReadability.UNREADABLE,
            errors=[error], total_message_count=0,
            integrity=_empty_integrity(start_magic_valid=start_magic_valid),
            not_verified=list(NOT_VERIFIED),
        )
    )


def _scan_stream(f, name: str, size: int) -> ScanResult:
    if size == 0:
        return _unreadable(name, size, "file is empty")
    head = f.read(len(MCAP_MAGIC))
    if head != MCAP_MAGIC:
        return _unreadable(name, size, "not an MCAP file: start magic bytes do not match")
    f.seek(0)

    errors: list[str] = []
    schemas: dict[int, Schema] = {}
    channels: dict[int, Channel] = {}
    counts: dict[int, int] = {}
    mins: dict[int, int] = {}
    maxs: dict[int, int] = {}
    times: dict[int, array] = {}
    metadata_records: list[MetadataRecord] = []
    header: Header | None = None
    data_end = footer = False
    stats: Statistics | None = None
    orphan_messages = 0
    first_record = True
    completed = False

    reader = StreamReader(_BoundedReader(f, size), validate_crcs=True, record_size_limit=size)
    try:
        for rec in reader.records:
            if first_record:
                first_record = False
                if not isinstance(rec, Header):
                    errors.append(f"first record is {type(rec).__name__}, expected Header")
                    break
                header = rec
                continue
            if isinstance(rec, Schema):
                schemas.setdefault(rec.id, rec)
            elif isinstance(rec, Channel):
                prev = channels.get(rec.id)
                if prev is None:
                    channels[rec.id] = rec
                    counts[rec.id] = 0
                    times[rec.id] = array("Q")
                elif _channel_key(prev) != _channel_key(rec):
                    errors.append(f"channel id {rec.id} is defined twice with different content")
            elif isinstance(rec, Message):
                cid = rec.channel_id
                if cid not in channels:
                    orphan_messages += 1
                    continue
                counts[cid] += 1
                t = rec.log_time
                times[cid].append(t)
                mins[cid] = t if cid not in mins else min(mins[cid], t)
                maxs[cid] = t if cid not in maxs else max(maxs[cid], t)
            elif isinstance(rec, Metadata):
                metadata_records.append(MetadataRecord(name=rec.name, values=dict(rec.metadata)))
            elif isinstance(rec, Statistics):
                stats = rec
            elif isinstance(rec, DataEnd):
                data_end = True
            elif isinstance(rec, Footer):
                footer = True
        else:
            completed = True  # loop ended because the footer + closing magic were read
    except _FORMAT_ERRORS as e:
        errors.append(_describe(e, f.tell()))

    if header is None:
        errors.append("no valid Header record could be read")
        return ScanResult(
            InspectionResult(
                file_name=name, size_bytes=size, readability=ArtifactReadability.UNREADABLE,
                errors=errors, total_message_count=0,
                integrity=_empty_integrity(start_magic_valid=True),
                not_verified=list(NOT_VERIFIED),
            )
        )

    trailing = 0
    end_magic_valid = False
    if completed:
        trailing = max(0, size - f.tell())
        end_magic_valid = trailing == 0
        if trailing:
            errors.append(f"{trailing} unexpected byte(s) follow the closing magic")
        if not data_end:
            errors.append("no DataEnd record before the footer")
    else:
        if not errors:  # defensive: should be impossible, but never report 'readable' by accident
            errors.append("scan ended before the footer was reached")

    total = sum(counts.values())
    if orphan_messages:
        errors.append(f"{orphan_messages} message(s) reference an undefined channel")
    for cid, ch in channels.items():
        if ch.schema_id != 0 and ch.schema_id not in schemas:
            errors.append(f"channel {cid} ('{ch.topic}') references undefined schema id {ch.schema_id}")
    if completed and stats is not None and stats.message_count != total:
        errors.append(
            f"Statistics record claims {stats.message_count} message(s) but {total} were read"
        )

    topics = []
    for cid in sorted(channels, key=lambda c: (channels[c].topic, c)):
        ch = channels[cid]
        sch = schemas.get(ch.schema_id)
        topics.append(
            TopicInfo(
                channel_id=cid, topic=ch.topic,
                schema_name=sch.name if sch else None,
                message_encoding=ch.message_encoding,
                message_count=counts[cid],
                first_log_time_ns=mins.get(cid), last_log_time_ns=maxs.get(cid),
            )
        )
    window = (
        RecordingWindow(min_log_time_ns=min(mins.values()), max_log_time_ns=max(maxs.values()))
        if mins else None
    )
    readability = ArtifactReadability.READABLE if not errors else ArtifactReadability.PARTIALLY_READABLE

    return ScanResult(
        InspectionResult(
            file_name=name, size_bytes=size, readability=readability, errors=errors,
            mcap_profile=header.profile, mcap_library=header.library,
            total_message_count=total, recording_window=window, topics=topics,
            metadata_records=metadata_records,
            integrity=IntegrityInfo(
                start_magic_valid=True, header_present=True, data_end_present=data_end,
                footer_present=footer, end_magic_valid=end_magic_valid, trailing_bytes=trailing,
                statistics_present=stats is not None,
                statistics_message_count=stats.message_count if stats else None,
            ),
            not_verified=list(NOT_VERIFIED),
        ),
        log_times_by_channel=times,
    )
