"""rosbag2 bag-directory support: deterministic discovery, per-segment scanning, cross-checks
against `metadata.yaml`, and a merge into the SAME `ScanResult` shape the Day 1 pipeline consumes.

Design (see docs/ARCHITECTURE.md):

  * Every storage file is scanned independently by the unchanged Day 1 `scan_recording`
    (streaming; log_time values only are kept in memory, never message payloads).
  * The per-segment results are merged into one `ScanResult`, so `validate_recording`, cadence
    analysis, manifests and export need no knowledge of how many files a recording had.
    A cadence gap that straddles a segment boundary is therefore still seen.
  * What `metadata.yaml` claims is cross-checked against the files, never trusted.
    File names alone never prove ordering or completeness.
  * The merged readability is READABLE only if every structural condition holds. Anything else
    (missing/unreadable/duplicate/unaccounted segment, missing or invalid metadata, count
    mismatch, unsupported storage) yields PARTIALLY_READABLE (something is inspectable) or
    UNREADABLE (nothing is), so a bag can never be reported complete by accident.
  * Nothing is written, moved or repaired. Original files are only read.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
from array import array
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .bag_metadata import MAX_METADATA_BYTES, ParsedBagMetadata, parse_bag_metadata
from .checksums import FileTooLargeError, read_and_hash, sha256_file
from .errors import InputError
from .mcap_reader import NOT_VERIFIED, ScanResult, scan_recording
from .schemas import (
    ArtifactReadability, ArtifactRole, BagInfo, CheckCategory, CheckOutcome, CheckResult,
    InspectionResult, IntegrityInfo, MetadataRecord, RecordingWindow, SegmentInfo, SegmentState,
    TopicInfo, UnaccountedEntry,
)

METADATA_NAME = "metadata.yaml"
SUPPORTED_STORAGE = "mcap"
_STORAGE_HINTS = (".mcap", ".db3", ".zstd", ".lz4")

BAG_NOT_VERIFIED = [
    "metadata.yaml is written by the recorder when it closes; agreement between it and the files "
    "shows consistency, not completeness or that every published message was recorded.",
    "metadata.yaml starting_time/duration use the recorder's clock and are not compared with "
    "MCAP log_time.",
]


@dataclass(frozen=True)
class BagFile:
    """A file of the bag that export will copy. Hashed once, before analysis."""

    relative_path: str  # relative to the bag directory
    path: Path
    sha256: str
    size_bytes: int
    role: ArtifactRole


@dataclass(frozen=True)
class BagAnalysis:
    root: Path
    info: BagInfo
    files: tuple[BagFile, ...]  # metadata.yaml (if readable) + every hashable listed segment
    checks: tuple[CheckResult, ...]
    parsed: ParsedBagMetadata | None

    @property
    def digest(self) -> str:
        """Deterministic digest of the bag: SHA-256 over sorted '<sha256>  <path>' lines.
        A Collector convention (rosbag2 has no such notion); it identifies exactly these bytes."""
        lines = "".join(f"{f.sha256}  {f.relative_path}\n" for f in sorted(self.files, key=lambda f: f.relative_path))
        return hashlib.sha256(lines.encode()).hexdigest()


@dataclass
class _Seg:
    info: SegmentInfo
    scan: ScanResult | None = None
    bagfile: BagFile | None = None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _norm(rel: str) -> str:
    return posixpath.normpath(rel)


def _resolve_inside(root: Path, rel: str) -> tuple[Path | None, str | None]:
    """The path for `rel` under `root`, or (None, reason) if it is unsafe."""
    if "\x00" in rel or "\\" in rel:
        return None, "path contains a NUL or backslash"
    pp = PurePosixPath(rel)
    if pp.is_absolute():
        return None, "path is absolute"
    if ".." in pp.parts:
        return None, "path contains '..'"
    cand = root / rel
    real_root = os.path.realpath(root)
    real = os.path.realpath(cand)
    if real != real_root and not real.startswith(real_root + os.sep):
        return None, "path resolves (via a symlink) outside the bag directory"
    return cand, None


def _state_from(scan: ScanResult) -> SegmentState:
    return {
        ArtifactReadability.READABLE: SegmentState.READABLE,
        ArtifactReadability.PARTIALLY_READABLE: SegmentState.PARTIALLY_READABLE,
        ArtifactReadability.UNREADABLE: SegmentState.UNREADABLE,
    }[scan.readability]


def _inspect_segment(
    root: Path, rel: str, source: str, expected_count: int | None, unsupported: str | None
) -> _Seg:
    def info(state: SegmentState, **kw) -> SegmentInfo:
        return SegmentInfo(
            relative_path=rel, source=source, state=state, metadata_message_count=expected_count, **kw
        )

    path, why = _resolve_inside(root, rel)
    if path is None:
        return _Seg(info(SegmentState.UNSAFE_PATH, errors=[f"refused: {why}"]))
    if not os.path.lexists(path):
        return _Seg(info(SegmentState.MISSING, errors=["file listed by metadata but not found on disk"]))
    if not path.is_file():
        return _Seg(info(SegmentState.NOT_A_FILE, errors=["path exists but is not a regular file"]))
    try:
        size = path.stat().st_size
        digest = sha256_file(path)
    except OSError as e:
        return _Seg(info(SegmentState.UNREADABLE, errors=[f"cannot read file: {e.strerror or e}"]))
    bagfile = BagFile(rel, path, digest, size, ArtifactRole.RECORDING)
    if unsupported is not None:
        return _Seg(info(SegmentState.UNSUPPORTED, size_bytes=size, sha256=digest, errors=[unsupported]), None, bagfile)
    try:
        scan = scan_recording(path)
    except InputError as e:
        return _Seg(info(SegmentState.UNREADABLE, size_bytes=size, sha256=digest, errors=[e.message]), None, bagfile)
    insp = scan.inspection
    w = insp.recording_window
    return _Seg(
        info(
            _state_from(scan), size_bytes=size, sha256=digest,
            observed_message_count=insp.total_message_count,
            first_log_time_ns=w.min_log_time_ns if w else None,
            last_log_time_ns=w.max_log_time_ns if w else None,
            errors=list(insp.errors),
        ),
        scan, bagfile,
    )


def _looks_like_storage(name: str) -> bool:
    return name.endswith(_STORAGE_HINTS) or ".db3" in name


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


def scan_bag(path: str | Path) -> tuple[ScanResult, BagAnalysis]:
    """Inspect a rosbag2 directory. Never raises for bad *content*; raises InputError only if
    the directory itself cannot be accessed."""
    root = Path(path)
    if not root.exists():
        raise InputError(f"Recording not found: '{root}'", hint="Check the path to the bag directory.")
    if not root.is_dir():
        raise InputError(f"Recording path is not a directory: '{root}'")
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError as e:
        raise InputError(f"Cannot list bag directory '{root}': {e.strerror or e}") from e

    conflicts: list[str] = []
    files: list[BagFile] = []

    # ---- metadata.yaml -------------------------------------------------------------------
    meta_path = root / METADATA_NAME
    metadata_present = os.path.lexists(meta_path)
    parsed: ParsedBagMetadata | None = None
    meta_errors: list[str] = []
    if metadata_present:
        mp, why = _resolve_inside(root, METADATA_NAME)
        if mp is None:
            meta_errors = [f"metadata.yaml refused: {why}"]
        elif not mp.is_file():
            meta_errors = ["metadata.yaml exists but is not a regular file"]
        else:
            try:
                raw, digest = read_and_hash(mp, max_bytes=MAX_METADATA_BYTES)
            except FileTooLargeError:
                meta_errors = [f"metadata.yaml is larger than {MAX_METADATA_BYTES} bytes"]
            except OSError as e:
                meta_errors = [f"cannot read metadata.yaml: {e.strerror or e}"]
            else:
                files.append(BagFile(METADATA_NAME, mp, digest, len(raw), ArtifactRole.BAG_METADATA))
                parsed, meta_errors = parse_bag_metadata(raw)
    metadata_valid = parsed is not None

    # ---- which segments, in which order ----------------------------------------------------
    candidates: list[tuple[str, str]] = []  # (relative path as written, source)
    expected_counts: dict[str, int] = {}
    unsupported: str | None = None
    if parsed is not None:
        s = parsed.summary
        seen: set[str] = set()
        for rel in s.relative_file_paths:
            n = _norm(rel)
            if n in seen:
                conflicts.append(f"relative_file_paths lists '{rel}' more than once")
                continue
            seen.add(n)
            candidates.append((rel, "metadata"))
        entry_paths: list[str] = []
        for p, c in parsed.file_entries:
            n = _norm(p)
            if n in entry_paths:
                conflicts.append(f"files[] lists '{p}' more than once")
                continue
            entry_paths.append(n)
            expected_counts[n] = c
            if n not in seen:
                conflicts.append(f"files[] lists '{p}' which is not in relative_file_paths")
                seen.add(n)
                candidates.append((p, "metadata_files_only"))
        if parsed.file_entries:
            for rel in s.relative_file_paths:
                if _norm(rel) not in entry_paths:
                    conflicts.append(f"relative_file_paths lists '{rel}' which is absent from files[]")
        if s.storage_identifier != SUPPORTED_STORAGE:
            unsupported = (
                f"storage_identifier '{s.storage_identifier}' is not supported "
                f"(only '{SUPPORTED_STORAGE}'); the file was not read"
            )
        elif (s.compression_mode or "").strip().lower() == "file":
            unsupported = (
                f"file-level compression (compression_mode FILE, format '{s.compression_format}') "
                "is not supported; the file was not decompressed or read"
            )
    else:
        for e in entries:
            if e.name.endswith(".mcap") and e.is_file(follow_symlinks=False):
                candidates.append((e.name, "discovered"))

    segs = [
        _inspect_segment(root, rel, src, expected_counts.get(_norm(rel)), unsupported)
        for rel, src in candidates
    ]
    files.extend(sg.bagfile for sg in segs if sg.bagfile is not None)

    if parsed is None:  # no authoritative order: order by what was observed, then by name
        inf = float("inf")
        segs.sort(key=lambda sg: (sg.info.first_log_time_ns if sg.info.first_log_time_ns is not None else inf, sg.info.relative_path))
    order_basis = "metadata" if parsed is not None else "inferred_from_observed_log_time"

    # ---- unaccounted entries ---------------------------------------------------------------
    accounted_top = {METADATA_NAME} | {PurePosixPath(_norm(sg.info.relative_path)).parts[0] for sg in segs if PurePosixPath(_norm(sg.info.relative_path)).parts}
    unaccounted: list[UnaccountedEntry] = []
    for e in entries:
        if e.name in accounted_top:
            continue
        if e.is_symlink():
            kind = "other"
        elif e.is_dir(follow_symlinks=False):
            kind = "directory"
        elif e.is_file(follow_symlinks=False):
            kind = "file"
        else:
            kind = "other"
        size = None
        if kind == "file":
            try:
                size = e.stat(follow_symlinks=False).st_size
            except OSError:
                size = None
        unaccounted.append(UnaccountedEntry(name=e.name, kind=kind, size_bytes=size, looks_like_storage=_looks_like_storage(e.name)))

    info = BagInfo(
        bag_directory_name=root.resolve().name or "bag",
        metadata_present=metadata_present, metadata_valid=metadata_valid, metadata_errors=meta_errors,
        metadata=parsed.summary if parsed else None,
        segment_order_basis=order_basis,
        segments=[sg.info for sg in segs], unaccounted=unaccounted, conflicts=conflicts,
    )
    checks = tuple(_build_checks(info, segs, parsed, unsupported))
    scan = _merge(root, info, segs, checks)
    return scan, BagAnalysis(root=root, info=info, files=tuple(files), checks=checks, parsed=parsed)


# --------------------------------------------------------------------------
# checks (bag:*)
# --------------------------------------------------------------------------


def _check(cid: str, outcome: CheckOutcome, required: bool, message: str, *, observed=None, unknowns=None) -> CheckResult:
    return CheckResult(
        check_id=cid, category=CheckCategory.BAG_STRUCTURE, outcome=outcome, required=required,
        message=message, observed=observed or {}, unknowns=unknowns or [],
    )


def _segment_problems(info: BagInfo) -> list[str]:
    probs = [f"'{s.relative_path}' is {s.state.value.replace('_', ' ')}" for s in info.segments if s.state is not SegmentState.READABLE]
    probs += [f"unaccounted storage-like file '{u.name}' is not listed by metadata.yaml" for u in info.unaccounted if u.looks_like_storage and info.metadata_valid]
    probs += list(info.conflicts)
    return probs


def _build_checks(info: BagInfo, segs: list[_Seg], parsed: ParsedBagMetadata | None, unsupported: str | None) -> list[CheckResult]:
    out: list[CheckResult] = []

    # bag:metadata
    if info.metadata_valid and parsed is not None:
        m = parsed.summary
        out.append(_check(
            "bag:metadata", CheckOutcome.PASS, True,
            f"metadata.yaml present and parsed (version {m.metadata_version}, storage '{m.storage_identifier}', "
            f"{len(m.relative_file_paths)} listed file(s))",
            observed={"metadata_version": m.metadata_version, "storage_identifier": m.storage_identifier},
            unknowns=list(BAG_NOT_VERIFIED) + list(parsed.warnings),
        ))
    elif not info.metadata_present:
        out.append(_check(
            "bag:metadata", CheckOutcome.FAIL, True,
            "metadata.yaml is missing. rosbag2 writes it when the recording is closed, so this is consistent "
            "with an interrupted recording but does not prove one. Segment list, order and message counts "
            "cannot be confirmed; the *.mcap files found on disk were inspected as an unconfirmed set.",
            unknowns=["which segments the recording was supposed to contain", "segment order", "expected message counts"],
        ))
    else:
        out.append(_check(
            "bag:metadata", CheckOutcome.FAIL, True,
            "metadata.yaml could not be used: " + "; ".join(info.metadata_errors),
            observed={"errors": list(info.metadata_errors)},
            unknowns=["which segments the recording was supposed to contain", "segment order", "expected message counts"],
        ))

    # bag:storage_format
    if parsed is not None and unsupported is not None:
        out.append(_check("bag:storage_format", CheckOutcome.FAIL, True, unsupported + ".",
                          observed={"storage_identifier": parsed.summary.storage_identifier,
                                    "compression_mode": parsed.summary.compression_mode},
                          unknowns=["content of the recording"]))
    elif parsed is not None:
        out.append(_check("bag:storage_format", CheckOutcome.PASS, True,
                          f"storage '{parsed.summary.storage_identifier}' is supported",
                          observed={"compression_mode": parsed.summary.compression_mode}))
    else:
        out.append(_check("bag:storage_format", CheckOutcome.INCONCLUSIVE, True,
                          "storage format taken from the *.mcap file extension because metadata.yaml is unusable",
                          unknowns=["declared storage_identifier and compression"]))

    # bag:segments
    probs = _segment_problems(info)
    seg_obs = {"segments": [{"path": s.relative_path, "state": s.state.value, "source": s.source} for s in info.segments]}
    if not info.segments:
        out.append(_check("bag:segments", CheckOutcome.FAIL, True,
                          "no storage segments could be identified" + (" (no *.mcap files found)" if parsed is None else ""),
                          observed=seg_obs, unknowns=["recording content"]))
    elif probs:
        out.append(_check("bag:segments", CheckOutcome.FAIL, True,
                          f"{len(probs)} problem(s) with the recording's segments: " + "; ".join(probs),
                          observed=seg_obs, unknowns=["content of segments that are missing, unreadable or unsupported"]))
    elif not info.metadata_valid:
        out.append(_check("bag:segments", CheckOutcome.INCONCLUSIVE, True,
                          f"{len(info.segments)} *.mcap file(s) found and individually readable, but without a valid "
                          "metadata.yaml it cannot be established that they are all the segments of the recording",
                          observed=seg_obs, unknowns=["whether any segment is missing"]))
    else:
        out.append(_check("bag:segments", CheckOutcome.PASS, True,
                          f"all {len(info.segments)} segment(s) listed by metadata.yaml are present and individually readable",
                          observed=seg_obs, unknowns=["whether the recorder itself dropped messages"]))

    # bag:message_counts
    out.append(_message_count_check(info, segs, parsed))

    # bag:segment_order (warning only)
    out.append(_order_check(info))

    # bag:unaccounted_files (warning only)
    extra = [u for u in info.unaccounted]
    if extra:
        out.append(_check("bag:unaccounted_files", CheckOutcome.FAIL, False,
                          f"{len(extra)} entr{'y' if len(extra) == 1 else 'ies'} in the bag directory not accounted for by "
                          "metadata.yaml (not inspected, not exported): " + ", ".join(f"'{u.name}'" for u in extra),
                          observed={"entries": [u.model_dump(mode='json') for u in extra]}))
    else:
        out.append(_check("bag:unaccounted_files", CheckOutcome.PASS, False, "no unaccounted entries in the bag directory"))
    return out


def _message_count_check(info: BagInfo, segs: list[_Seg], parsed: ParsedBagMetadata | None) -> CheckResult:
    cid = "bag:message_counts"
    if parsed is None:
        return _check(cid, CheckOutcome.NOT_APPLICABLE, True,
                      "no valid metadata.yaml to compare message counts against (see bag:metadata)",
                      unknowns=["expected message count"])
    if any(sg.info.state is not SegmentState.READABLE for sg in segs):
        return _check(cid, CheckOutcome.INCONCLUSIVE, True,
                      "message counts cannot be compared because at least one segment is not fully readable",
                      unknowns=["message counts of missing, unreadable or unsupported segments"])
    m = parsed.summary
    problems: list[str] = []
    observed_total = sum(sg.info.observed_message_count or 0 for sg in segs)
    if observed_total != m.message_count:
        problems.append(f"metadata message_count {m.message_count} != {observed_total} messages read")
    for sg in segs:
        exp = sg.info.metadata_message_count
        if exp is not None and exp != sg.info.observed_message_count:
            problems.append(f"'{sg.info.relative_path}': metadata says {exp}, {sg.info.observed_message_count} read")
    per_topic: dict[str, int] = {}
    for sg in segs:
        assert sg.scan is not None
        for t in sg.scan.inspection.topics:
            per_topic[t.topic] = per_topic.get(t.topic, 0) + t.message_count
    for name in sorted(set(per_topic) | set(m.topic_message_counts)):
        a, b = m.topic_message_counts.get(name, 0), per_topic.get(name, 0)
        if a != b:
            problems.append(f"topic {name}: metadata says {a}, {b} read")
    obs = {"metadata_message_count": m.message_count, "observed_message_count": observed_total}
    if problems:
        return _check(cid, CheckOutcome.FAIL, True,
                      "metadata.yaml disagrees with the files: " + "; ".join(problems) +
                      ". This shows an inconsistency, not which side is wrong or why.",
                      observed=obs, unknowns=["cause of the disagreement"])
    return _check(cid, CheckOutcome.PASS, True,
                  f"message counts in metadata.yaml match the files ({observed_total} message(s), "
                  f"{len(m.topic_message_counts)} topic(s))", observed=obs,
                  unknowns=["metadata.yaml is the recorder's own assertion; matching it does not show nothing was dropped before recording"])


def _order_check(info: BagInfo) -> CheckResult:
    cid = "bag:segment_order"
    if info.segment_order_basis != "metadata":
        return _check(cid, CheckOutcome.NOT_APPLICABLE, False,
                      "segment order was inferred from observed log_time because metadata.yaml is unusable",
                      unknowns=["the recorder's actual segment order"])
    timed = [s for s in info.segments if s.state is SegmentState.READABLE and s.first_log_time_ns is not None]
    bad = []
    for prev, cur in zip(timed, timed[1:], strict=False):
        if cur.first_log_time_ns < prev.last_log_time_ns:  # type: ignore[operator]
            bad.append(f"'{cur.relative_path}' starts at {cur.first_log_time_ns} before '{prev.relative_path}' ends at {prev.last_log_time_ns}")
    if bad:
        return _check(cid, CheckOutcome.FAIL, False,
                      "observed log_time windows overlap or go backwards in metadata order: " + "; ".join(bad) +
                      ". Small overlaps can occur with out-of-order recorder timestamps; this is not proof of a problem.",
                      observed={"overlaps": bad})
    return _check(cid, CheckOutcome.PASS, False,
                  "observed log_time windows are consistent with the segment order in metadata.yaml",
                  unknowns=["ordering is an observation about log_time, not proof of recorder behaviour"])


# --------------------------------------------------------------------------
# merge into one ScanResult
# --------------------------------------------------------------------------


def _merge(root: Path, info: BagInfo, segs: list[_Seg], checks: tuple[CheckResult, ...]) -> ScanResult:
    scanned = [sg for sg in segs if sg.scan is not None]
    errors: list[str] = []
    if not info.metadata_valid:
        errors += [f"bag metadata: {e}" for e in (info.metadata_errors or ["metadata.yaml is missing"])]
    errors += [f"bag: {c}" for c in info.conflicts]
    for sg in segs:
        if sg.info.state is SegmentState.READABLE:
            continue
        errors += [f"segment '{sg.info.relative_path}' ({sg.info.state.value}): {e}" for e in sg.info.errors] or [
            f"segment '{sg.info.relative_path}' is {sg.info.state.value}"
        ]
    errors += [f"bag: storage-like file '{u.name}' is not listed by metadata.yaml" for u in info.unaccounted if u.looks_like_storage and info.metadata_valid]
    for c in checks:
        if c.check_id == "bag:message_counts" and c.outcome is CheckOutcome.FAIL:
            errors.append(f"bag: {c.message}")
        if c.check_id == "bag:storage_format" and c.outcome is CheckOutcome.FAIL:
            errors.append(f"bag: {c.message}")
    if not segs:
        errors.append("bag: no storage segments could be identified")

    # topics merged across segments by (topic, schema, encoding); channel ids renumbered
    merged: dict[tuple, dict] = {}
    for sg in scanned:
        insp = sg.scan.inspection  # type: ignore[union-attr]
        for t in insp.topics:
            key = (t.topic, t.schema_name, t.message_encoding)
            m = merged.setdefault(key, {"count": 0, "first": None, "last": None, "times": array("Q")})
            m["count"] += t.message_count
            if t.first_log_time_ns is not None:
                m["first"] = t.first_log_time_ns if m["first"] is None else min(m["first"], t.first_log_time_ns)
                m["last"] = t.last_log_time_ns if m["last"] is None else max(m["last"], t.last_log_time_ns)
            m["times"].extend(sg.scan.log_times_by_channel.get(t.channel_id, ()))  # type: ignore[union-attr]
    topics: list[TopicInfo] = []
    log_times: dict[int, array] = {}
    for cid, key in enumerate(sorted(merged, key=lambda k: (k[0], k[1] or "", k[2] or "")), start=1):
        m = merged[key]
        topics.append(TopicInfo(channel_id=cid, topic=key[0], schema_name=key[1], message_encoding=key[2],
                                message_count=m["count"], first_log_time_ns=m["first"], last_log_time_ns=m["last"]))
        log_times[cid] = m["times"]

    mins = [t.first_log_time_ns for t in topics if t.first_log_time_ns is not None]
    maxs = [t.last_log_time_ns for t in topics if t.last_log_time_ns is not None]
    window = RecordingWindow(min_log_time_ns=min(mins), max_log_time_ns=max(maxs)) if mins else None

    insps = [sg.scan.inspection for sg in scanned]  # type: ignore[union-attr]
    stats_all = bool(insps) and all(i.integrity.statistics_present for i in insps)
    integrity = IntegrityInfo(
        start_magic_valid=bool(insps) and all(i.integrity.start_magic_valid for i in insps),
        header_present=bool(insps) and all(i.integrity.header_present for i in insps),
        data_end_present=bool(insps) and all(i.integrity.data_end_present for i in insps),
        footer_present=bool(insps) and all(i.integrity.footer_present for i in insps),
        end_magic_valid=bool(insps) and all(i.integrity.end_magic_valid for i in insps),
        trailing_bytes=sum(i.integrity.trailing_bytes for i in insps),
        statistics_present=stats_all,
        statistics_message_count=sum(i.integrity.statistics_message_count or 0 for i in insps) if stats_all else None,
    )
    meta_records: list[MetadataRecord] = []
    seen_meta: set[str] = set()
    for i in insps:
        for r in i.metadata_records:
            k = r.model_dump_json()
            if k not in seen_meta:
                seen_meta.add(k)
                meta_records.append(r)

    usable = [sg for sg in segs if sg.info.state in (SegmentState.READABLE, SegmentState.PARTIALLY_READABLE)]
    all_readable = bool(segs) and all(sg.info.state is SegmentState.READABLE for sg in segs)
    if not usable:
        readability = ArtifactReadability.UNREADABLE
    elif errors or not all_readable:
        readability = ArtifactReadability.PARTIALLY_READABLE
    else:
        readability = ArtifactReadability.READABLE

    profiles = {i.mcap_profile for i in insps}
    libs = {i.mcap_library for i in insps}
    return ScanResult(
        InspectionResult(
            file_name=info.bag_directory_name,
            size_bytes=sum(sg.info.size_bytes or 0 for sg in segs),
            readability=readability, errors=errors,
            mcap_profile=profiles.pop() if len(profiles) == 1 else None,
            mcap_library=libs.pop() if len(libs) == 1 else None,
            total_message_count=sum(t.message_count for t in topics),
            recording_window=window, topics=topics, metadata_records=meta_records,
            integrity=integrity, not_verified=list(NOT_VERIFIED) + BAG_NOT_VERIFIED,
        ),
        log_times_by_channel=log_times,
    )
